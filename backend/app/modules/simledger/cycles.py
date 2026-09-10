"""v3 three-tier cycle logic (R1-2 + R1-3): the live analog of the backtest.

Three cycles, mirroring the simulator's exact ordering so the live scoreboard
stays comparable to R0-9:

  signal_cycle    after close (session D): sync daily bars, compute signals +
                  phase for the current point-in-time universe, publish the
                  Recommendation shortlist FOR the next session, run the
                  DAILY exit management on the system account (trailing ratchet
                  from info through D — never same-bar; reversal/stagnation at
                  close), update protections, snapshot accounts.
  entry_cycle     shortly after the next open: protections gate (HALT sentinel /
                  halt / pause) -> book shortlisted entries at the live quote
                  via the sim ledger (idempotent per account+symbol+session).
  position_cycle  every 5 minutes during RTH: stop-breach checks at the live
                  quote. It never ratchets the trailing stop intraday — the
                  ratchet uses only end-of-day information (backtest F2 rule).

All network/data dependencies are injectable for tests. Recommended params come
from the R0-9 G1 baseline consensus (only C1 moved off the design defaults).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from quant import config as qconfig
from quant.data import bars as qbars
from quant.data import calendar as qcal
from quant.engine import funnel as qfunnel
from quant.engine import phase as qphase
from quant.engine import sizing as qsizing
from quant.engine import strategy as qstrategy
from quant.engine.exits import (ExitParams, Position, evaluate_exit,
                                maybe_raise_trailing, update_position_bar)
from quant.engine.signal import Direction

from app.modules.simledger.models import (AccountSnapshot, Recommendation,
                                          SafetyState, SimAccount)
from app.modules.simledger.service import (InsufficientCash, SimLedgerService,
                                           _dec, entry_cost_price)

logger = logging.getLogger(__name__)

# R0-9 G1 baseline consensus (2026-07-30): only C1 moved off design defaults.
RECOMMENDED_FUNNEL = qfunnel.FunnelParams(min_confidence=65.0)     # [C1]
RECOMMENDED_EXITS = ExitParams()                                    # C5/C6/C8 = defaults
QUOTE_STALE_SECONDS = 15 * 60
try:
    from app.config import settings as _settings
    HALT_SENTINEL = _settings.HALT_SENTINEL_PATH   # existence refuses all entries
except Exception:                                   # quant-only contexts
    HALT_SENTINEL = "/app/runtime/HALT"


@dataclass
class QuoteReading:
    price: float
    at: datetime


@dataclass
class RecommendationBatch:
    """build_recommendations' output plus WHY symbols dropped out. The counters
    are the caller's fail-closed input: a night that scans 500 names and
    publishes nothing must alert, not just log."""
    rows: list[dict] = field(default_factory=list)
    scanned: int = 0
    stale: int = 0
    unadjusted: int = 0

    @property
    def excluded(self) -> int:
        return self.stale + self.unadjusted


@dataclass
class ExitPass:
    closed: list[str] = field(default_factory=list)
    data_end: list[str] = field(default_factory=list)
    unadjusted: list[str] = field(default_factory=list)


def finnhub_quote(client, symbol: str) -> QuoteReading | None:
    """The ONE Finnhub-quote -> QuoteReading conversion (review #16: the router
    and the tasks each had a copy; staleness semantics must never diverge)."""
    q = client.quote(symbol)
    if not q:
        return None
    return QuoteReading(price=float(q["c"]),
                        at=datetime.fromtimestamp(int(q.get("t") or 0),
                                                  tz=timezone.utc))


async def _gather_quotes(quote_fn, symbols) -> dict[str, QuoteReading]:
    """Concurrent quote prefetch (review #3/#4/#5: serial blocking quote calls
    inside async fns blocked the event loop; gather pattern from
    market/service.py). Each distinct symbol is quoted exactly once; failed or
    None quotes are silently omitted — a missing entry reads as unusable
    through quote_is_usable."""
    async def _one(sym: str):
        try:
            return sym, await asyncio.to_thread(quote_fn, sym)
        except Exception:
            logger.warning("quote failed for %s — omitted", sym, exc_info=True)
            return sym, None

    pairs = await asyncio.gather(*[_one(s) for s in dict.fromkeys(symbols)])
    return {s: q for s, q in pairs if q is not None}


async def fetch_quotes(client, symbols) -> dict[str, QuoteReading]:
    """Concurrent Finnhub quotes for many symbols in ~one round-trip.
    Failed symbols are silently omitted from the result."""
    return await _gather_quotes(lambda s: finnhub_quote(client, s), symbols)


async def get_safety_state(db: AsyncSession, account: SimAccount, *,
                           create: bool = False) -> SafetyState | None:
    """Single owner of the SafetyState scope-key convention (review #17: four
    copy-pasted lookups had already grown two divergent create variants)."""
    state = (await db.execute(
        select(SafetyState).where(SafetyState.scope == str(account.id))
    )).scalar_one_or_none()
    if state is None and create:
        state = SafetyState(scope=str(account.id), halted=False)
        db.add(state)
        await db.flush()
    return state


def now_et() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))


# Entry modes (Phase C 拍板 2026-09-10). open_once is the 对照账户 default: one
# fill per name at the session's 09:30 open, the fixed_oos scoreboard's own
# semantics. intraday_ladder is the v3.1 live-only behaviour (every 15 min at
# the live quote, chase-capped) kept for a second portfolio instance.
ENTRY_MODE_OPEN_ONCE = "open_once"
ENTRY_MODE_LADDER = "intraday_ladder"
ENTRY_MODES = (ENTRY_MODE_OPEN_ONCE, ENTRY_MODE_LADDER)

# Minutes after the session open during which open_once may act. It cannot start
# before the free-tier SIP delay makes the 09:30 bar readable (15 min + a minute
# of slack); past the upper bound the session has moved on and a fill booked at
# the open price would be fiction. The price itself is always the 09:30 bar, so
# WHERE inside the window the cycle lands does not change what is booked.
OPEN_ONCE_WINDOW_MIN = (16, 90)


def account_entry_mode(account: SimAccount) -> str:
    """THE entry-mode read. getattr-guarded so the code is correct both before
    and after migration 018 adds the column."""
    return getattr(account, "entry_mode", None) or ENTRY_MODE_OPEN_ONCE


def in_rth(now: datetime | None = None, *, open_grace_min: int = 0) -> bool:
    """Shared ET/RTH gate (review #15: three hand-rolled copies risked drifting
    when half-day handling lands). The session's real [open, close) comes from
    the XNYS calendar, so a 13:00 ET early close ends the gate on time instead
    of leaving entries and stop checks running into a closed tape.
    open_grace_min shifts the session start for consumers that must wait for the
    first cycles to run (watchdog). A naive `now` is read as ET."""
    from quant.data import calendar as qcal
    n = now or now_et()
    if not qcal.is_trading_day(n.date()):
        return False
    ts = pd.Timestamp(n)
    ts = ts.tz_localize("America/New_York") if ts.tzinfo is None else ts
    open_ts, close_ts = qcal.rth_bounds(n.date())
    return open_ts + pd.Timedelta(minutes=open_grace_min) <= ts < close_ts


def in_open_once_window(now: datetime | None = None) -> bool:
    """True while the open_once entry cycle may book. Offsets from the session's
    real open, so DST and a 13:00 ET half day both follow the calendar rather
    than a wall-clock constant. A naive `now` is read as ET."""
    n = now or now_et()
    if not qcal.is_trading_day(n.date()):
        return False
    ts = pd.Timestamp(n)
    ts = ts.tz_localize("America/New_York") if ts.tzinfo is None else ts
    open_ts, _ = qcal.rth_bounds(n.date())
    start, end = OPEN_ONCE_WINDOW_MIN
    return (open_ts + pd.Timedelta(minutes=start) <= ts
            < open_ts + pd.Timedelta(minutes=end))


def quote_is_usable(q: QuoteReading | None, *, now: datetime) -> bool:
    """Stale-quote guard: no quote, non-positive, or older than the staleness
    window -> the cycle must SKIP the symbol (and say so), never trade on it."""
    if q is None or q.price <= 0:
        return False
    return (now - q.at).total_seconds() <= QUOTE_STALE_SECONDS


def entries_blocked_reason(state: SafetyState | None, *, today: date,
                           sentinel_path: str = HALT_SENTINEL) -> str | None:
    """The R1-4 protections gate. Sentinel file works even when the DB is down —
    callers check it FIRST."""
    if os.path.exists(sentinel_path):
        return "HALT sentinel present"
    if state is None:
        return None
    if state.halted and (state.halted_until is None
                         or datetime.now(timezone.utc) < state.halted_until):
        return f"halted: {state.reason or 'drawdown'}"
    if state.paused_until is not None and today <= state.paused_until:
        return f"daily-loss pause until {state.paused_until}"
    return None


# --- signal cycle ----------------------------------------------------------

def memoized_bars_fn(end: date, *, get_bars=qbars.get_bars):
    """Per-cycle bar memo (review #2): build_recommendations and
    daily_exit_management read the same daily parquets within one signal
    cycle — cache by symbol so each frame is read (and adjusted) once.
    A plain dict-backed closure, not functools.lru_cache: the call pattern is
    fixed per cycle ((symbol, "1d", end=session_date)) and date kwargs would
    defeat lru_cache's positional hashing anyway. A failed read is NOT cached,
    so per-symbol error handling in the callers keeps its retry semantics."""
    cache: dict[str, pd.DataFrame] = {}
    default_end = end

    def bars_fn(symbol: str, timeframe: str = "1d", *, end: date | None = None):
        if symbol not in cache:
            cache[symbol] = get_bars(symbol, timeframe, end=end or default_end)
        return cache[symbol]

    return bars_fn


def build_recommendations(symbols: list[str], session_date: date, *,
                          funnel_params: qfunnel.FunnelParams = RECOMMENDED_FUNNEL,
                          bars_fn=qbars.get_bars,
                          sectors: dict[str, str] | None = None) -> RecommendationBatch:
    """Compute the shortlist + phase reads from daily bars as of session_date.
    Pure given bars_fn. Returns dicts ready to become Recommendation rows with
    trade_date = the NEXT session (decide on D close, act at D+1 open).

    `sectors` must be injected HERE, before the funnel runs: build_shortlist
    caps each sector at MAX_PER_SECTOR, so scoring a pool whose sector is
    uniformly 'unknown' collapses every shortlist into one 2-name bucket.
    Two per-symbol fail-closed guards drop a name rather than the cycle: bars
    that are not from session_date, and prices flagged RAW for want of a
    cached corporate action. Both are COUNTED into the returned batch — the
    caller alerts on them."""
    sector_map = sectors or {}
    rows = []
    stale = unadjusted = 0
    for sym in symbols:
        try:
            b = bars_fn(sym, "1d", end=session_date)
        except Exception:
            logger.warning("signal_cycle: bars failed for %s", sym, exc_info=True)
            continue
        if b.attrs.get("unadjusted"):
            unadjusted += 1
            logger.error("signal_cycle: %s prices are RAW (split-sized jump, no "
                         "cached corporate action) — excluded from tonight's "
                         "recommendations", sym)
            continue
        sp = qstrategy.StrategyParams()
        if b.empty or len(b) < sp.warmup + 5:
            continue
        last_bar_date = b["ts"].iloc[-1].tz_convert("America/New_York").date()
        if last_bar_date != session_date:
            stale += 1
            logger.warning("signal_cycle: %s last bar %s != session %s — stale "
                           "bars, not recommended (sync failed?)",
                           sym, last_bar_date, session_date)
            continue
        sig = qstrategy.compute_signals(b, sp)
        last = sig.iloc[-1]
        close = b["close"]
        adv = float((b["close"] * b["volume"]).rolling(20).mean().iloc[-1])
        ph = qphase.latest(close)
        ma_slow = sig["ma_slow"]
        rows.append({
            "symbol": sym,
            "direction": str(last["direction"]),
            "confidence": float(last["confidence"]),
            "atr_pct": float(last["atr"] / close.iloc[-1]),
            "price": float(close.iloc[-1]),
            "adv": adv,
            "above_rising_ma20": bool(close.iloc[-1] > ma_slow.iloc[-1]
                                      and ma_slow.iloc[-1] > ma_slow.iloc[-6]),
            "stop_distance": float(last["stop_distance"]),
            "expected_move": float(last["expected_move"]),
            "phase": ph.phase,
            "phase_reason": ph.reason,
            "sector": sector_map.get(sym, "unknown"),
        })
    if stale or unadjusted:
        logger.warning("signal_cycle: excluded %d symbols on stale bars and %d on "
                       "unadjusted prices", stale, unadjusted)
    batch = RecommendationBatch(scanned=len(symbols), stale=stale,
                                unadjusted=unadjusted)
    if not rows:
        return batch
    feats = pd.DataFrame(rows)
    shortlist = qfunnel.build_shortlist(feats, funnel_params)
    shortlist += qfunnel.select_etfs(feats)
    rank = {s: i + 1 for i, s in enumerate(shortlist)}
    next_day = qcal.next_session(session_date)
    out = []
    for r in rows:
        if r["symbol"] not in rank and r["direction"] != "up":
            # keep the table lean: persist shortlisted names + up-signals only
            continue
        out.append({
            "symbol": r["symbol"], "trade_date": next_day,
            "direction": r["direction"], "confidence": round(r["confidence"], 3),
            "shortlist_rank": rank.get(r["symbol"]),
            "phase": r["phase"], "phase_reason": r["phase_reason"],
            "features": {k: r[k] for k in ("price", "adv", "atr_pct",
                                           "stop_distance", "expected_move",
                                           "above_rising_ma20", "sector")},
        })
    batch.rows = out
    return batch


async def store_recommendations(db: AsyncSession, recs: list[dict]) -> int:
    """Idempotent publish: replace the whole batch for that trade_date."""
    if not recs:
        return 0
    td = recs[0]["trade_date"]
    await db.execute(delete(Recommendation).where(Recommendation.trade_date == td))
    for r in recs:
        db.add(Recommendation(**r))
    await db.flush()
    return len(recs)


async def shortlist_symbols(db: AsyncSession, today: date) -> list[str]:
    """Today's shortlisted names in rank order — the set open_once needs opening
    prices for. Pyramid candidates are a subset (a name is only added to when it
    is BOTH held and recommended), so this is the whole fetch set."""
    return list((await db.execute(
        select(Recommendation.symbol)
        .where(Recommendation.trade_date == today,
               Recommendation.shortlist_rank.isnot(None))
        .order_by(Recommendation.shortlist_rank)
    )).scalars().all())


DATA_END_STALE_SESSIONS = 3   # consecutive sessions with no new bar -> force close


def benchmark_closes(bars_fn, session_date: date, *, symbol: str = "SPY"):
    """Benchmark closes indexed by ET session date, for the stagnation hurdle
    (simulator.run builds the same series). None when the benchmark has no
    bars — evaluate_exit then falls back to its no-benchmark branch."""
    try:
        b = bars_fn(symbol, "1d", end=session_date)
    except Exception:
        logger.warning("daily_exit: benchmark %s bars failed", symbol, exc_info=True)
        return None
    if b.empty:
        return None
    return b.set_index(b["ts"].dt.tz_convert("America/New_York").dt.date)["close"]


def benchmark_return_since(closes, entry_date: date, session_date: date) -> float | None:
    if closes is None or entry_date not in closes.index \
            or session_date not in closes.index:
        return None
    return float(closes.loc[session_date]) / float(closes.loc[entry_date]) - 1.0


async def daily_exit_management(db: AsyncSession, account: SimAccount,
                                session_date: date, *,
                                exit_params: ExitParams = RECOMMENDED_EXITS,
                                bars_fn=qbars.get_bars) -> ExitPass:
    """Post-close exit pass on the system account — the simulator's exact
    ordering: trailing ratchet from info through D-1, fold D's bar into state,
    then close-based exits (reversal/stagnation) at D's close. Stop breaches
    intraday were position_cycle's job; the idempotency key (one close per lot)
    makes an overlap harmless.

    Returns an ExitPass: everything closed, the subset force-closed for want of
    data, and the symbols left UNMARKED because their prices are RAW — the
    caller alerts on the last two."""
    out = ExitPass()
    positions = await SimLedgerService.get_open_positions(db, account.id)
    if not positions:
        return out
    benchmark = benchmark_closes(bars_fn, session_date)
    for sp in positions:
        # review #32: one bad symbol must never abort the whole nightly
        # transaction (recommendations + other exits + snapshot)
        try:
            b = bars_fn(sp.symbol, "1d", end=session_date)
        except Exception:
            logger.warning("daily_exit: bars failed for %s — skipped",
                           sp.symbol, exc_info=True)
            continue
        if b.empty:
            continue
        if b.attrs.get("unadjusted"):
            # RAW prices: an unsynced 2:1 split reads as a -50% day and would
            # book a spurious hard_stop at half price. Mark nothing on this lot.
            out.unadjusted.append(sp.symbol)
            logger.error("daily_exit: %s prices are RAW (split-sized jump, no "
                         "cached corporate action) — position NOT marked and NOT "
                         "closed this session", sp.symbol)
            continue
        if sp.entry_date == session_date:
            # entered intraday today: the full day's OHLC contains prints from
            # before the fill, so a stop/trailing test against it marks a low
            # this lot never held. The simulator runs exits BEFORE the day's
            # entries, so it never folds the entry bar either; today's downside
            # was position_cycle's quote check. Checked BEFORE the stale/data_end
            # branch so a lot opened today can never be force-closed.
            continue
        # review #33/#27: a failed sync leaves yesterday's bar as iloc[-1];
        # folding it again double-counts bars_held/reversal and re-tests a
        # cleared low. Only fold a bar for the session under management.
        last_bar_date = b["ts"].iloc[-1].tz_convert("America/New_York").date()
        if last_bar_date != session_date:
            missing = max(len(qcal.sessions_in_range(last_bar_date, session_date)) - 1, 0)
            if missing >= DATA_END_STALE_SESSIONS:
                # the simulator's F6 data_end (simulator.py:178-183). It force-closes
                # after ONE missing session because a backtest bar is either there or
                # the symbol is dead; live loses bars to ordinary sync failures, so
                # DATA_END_STALE_SESSIONS buys deliberate slack before selling.
                await SimLedgerService.close_position(
                    db, account, sp, raw_price=float(b["close"].iloc[-1]),
                    reason="data_end", idempotency_key=f"exit:{sp.id}")
                out.closed.append(sp.symbol)
                out.data_end.append(sp.symbol)
                logger.error("daily_exit: %s has had no new bar for %d sessions "
                             "(last %s) — force-closed at its last close",
                             sp.symbol, missing, last_bar_date)
            else:
                logger.warning("daily_exit: %s last bar %s != session %s — stale "
                               "bars, skipped (sync failed?)",
                               sp.symbol, last_bar_date, session_date)
            continue
        try:
            sig = qstrategy.compute_signals(b, qstrategy.StrategyParams())
            row, last_bar = sig.iloc[-1], b.iloc[-1]
            prev_atr = float(sig["atr"].iloc[-2]) if len(sig) > 1 else float(row["atr"])
            p = Position(symbol=sp.symbol, shares=float(sp.shares),
                         avg_cost=float(sp.avg_cost), stop=float(sp.stop),
                         r_unit=float(sp.r_unit), entry_date=sp.entry_date,
                         high_water=float(sp.high_water), adds_done=int(sp.adds_done),
                         reversal_count=int(sp.reversal_count),
                         bars_held=int(sp.bars_held))
            direction = Direction(str(row["direction"]))
            below_ma = float(last_bar["close"]) < float(row["ma_slow"])
            maybe_raise_trailing(p, prev_atr, exit_params)
            update_position_bar(p, float(last_bar["close"]), float(last_bar["high"]),
                                direction, below_ma)
            decision = evaluate_exit(
                p, float(last_bar["low"]), float(last_bar["close"]),
                signal_direction=direction, expected_move=float(row["expected_move"]),
                params=exit_params, bar_open=float(last_bar["open"]),
                benchmark_return_since_entry=benchmark_return_since(
                    benchmark, sp.entry_date, session_date))
            if decision is not None:
                await SimLedgerService.close_position(
                    db, account, sp, raw_price=decision.price, reason=decision.action,
                    idempotency_key=f"exit:{sp.id}")
                out.closed.append(sp.symbol)
            else:
                sp.stop = _dec(p.stop)
                sp.high_water = _dec(p.high_water)
                sp.reversal_count = p.reversal_count
                sp.bars_held = p.bars_held
        except Exception:
            logger.warning("daily_exit: %s failed — skipped", sp.symbol,
                           exc_info=True)
    return out


# --- entry cycle -----------------------------------------------------------

@dataclass
class _EntrySource:
    """Everything that differs between the two entry modes: where a fill price
    comes from, which equity sizes the trade, how much cash is off-limits, and
    whether the chase cap applies. The booking loop itself is shared."""
    prices: dict[str, float]
    equity: float
    cash_offset: float = 0.0
    chase_cap: float | None = None


async def _equity_at_prior_close(db: AsyncSession, account: SimAccount, today: date, *,
                                 open_positions, quote_fn, now: datetime) -> float:
    """D-1 closing equity — the backtest's sizing base (simulator's
    equity_curve[prev]). A missing snapshot is a degradation, not a mode: it
    falls back to a live mark and says so."""
    snap = (await db.execute(
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account.id,
               AccountSnapshot.snapshot_date < today)
        .order_by(AccountSnapshot.snapshot_date.desc()).limit(1)
    )).scalar_one_or_none()
    if snap is not None and float(snap.equity) > 0:
        return float(snap.equity)
    logger.error("entry_cycle: no account snapshot before %s — sizing on a live "
                 "mark instead of D-1 close equity", today)
    quotes: dict[str, float] = {}
    if quote_fn is not None and open_positions:
        quote_map = await _gather_quotes(quote_fn, [p.symbol for p in open_positions])
        quotes = {s: q.price for s, q in quote_map.items()
                  if quote_is_usable(q, now=now)}
    return SimLedgerService.equity(account, open_positions, quotes)


async def _entry_price_source(mode: str, db: AsyncSession, account: SimAccount,
                              today: date, *, recs, open_positions, quote_fn,
                              now: datetime, open_prices: dict[str, float] | None,
                              chase_cap: float | None) -> _EntrySource:
    if mode == ENTRY_MODE_LADDER:
        if quote_fn is None:
            raise ValueError("intraday_ladder needs a quote_fn")
        # review #3: prefetch the union of held + shortlist concurrently — each
        # symbol is quoted exactly once (held names used to be quoted twice)
        quote_map = await _gather_quotes(quote_fn,
                                         [p.symbol for p in open_positions]
                                         + [r.symbol for r in recs])
        prices = {s: q.price for s, q in quote_map.items()
                  if quote_is_usable(q, now=now)}
        held_marks = {p.symbol: prices[p.symbol] for p in open_positions
                      if p.symbol in prices}
        return _EntrySource(
            prices=prices,
            equity=SimLedgerService.equity(account, open_positions, held_marks),
            chase_cap=chase_cap)
    if mode != ENTRY_MODE_OPEN_ONCE:
        raise ValueError(f"unknown entry_mode {mode!r}")
    return _EntrySource(
        prices={s: p for s, p in (open_prices or {}).items() if p > 0},
        equity=await _equity_at_prior_close(db, account, today,
                                            open_positions=open_positions,
                                            quote_fn=quote_fn, now=now),
        # the backtest funds entries from cash as of the OPEN, so proceeds from a
        # position closed later today must not buy a 09:30 fill (simulator's
        # cash_at_open). No chase cap: the open IS the reference price.
        cash_offset=await SimLedgerService.cash_from_exits_on(db, account, today))


async def run_entries(db: AsyncSession, account: SimAccount, today: date, *,
                      quote_fn=None, now: datetime | None = None,
                      risk_pct: float = qconfig.PER_TRADE_RISK_PCT,
                      chase_cap: float | None = qconfig.INTRADAY_ENTRY_CHASE_CAP,
                      entry_mode: str = ENTRY_MODE_LADDER,
                      open_prices: dict[str, float] | None = None,
                      max_slots: int | None = None) -> list[str]:
    """Book today's shortlisted entries. Idempotent per (account, symbol,
    session); protections are checked by the caller.

    ENTRY_MODE_OPEN_ONCE is the fixed_oos scoreboard's own semantics: D-1
    signals filled at D's 09:30 open (injected via open_prices), sized on D-1
    closing equity and the session's opening cash. A name with no open price
    does not enter today — the caller alerts.

    ENTRY_MODE_LADDER is the v3.1 live-only behaviour: called every 15 min
    during RTH, a name enters the first cycle its price is a good entry — at or
    below the recommendation's reference * (1 + chase_cap) — so a big intraday
    gap-up waits for a pullback instead of chasing. chase_cap=None fills at any
    price. ⚠️ This timing is NOT backtested; it diverges from the scoreboard."""
    now = now or datetime.now(timezone.utc)
    recs = list((await db.execute(
        select(Recommendation)
        .where(Recommendation.trade_date == today,
               Recommendation.shortlist_rank.isnot(None))
        .order_by(Recommendation.shortlist_rank)
    )).scalars().all())
    if not recs:
        return []

    open_positions = await SimLedgerService.get_open_positions(db, account.id)
    closed_today = await SimLedgerService.get_positions_closed_on(db, account.id, today)
    held = {p.symbol for p in open_positions}
    exited_today = {p.symbol for p in closed_today}
    etf_set = set(qconfig.ETF_WHITELIST)
    # backtest parity (simulator.py:169-173): an intraday exit neither frees a
    # slot nor allows a re-entry. The ladder counts the session-open snapshot
    # plus today's entries — which is exactly the open lots union today's
    # closed ones, whichever cycle in the day this is.
    slot_taken = held | exited_today
    stock_count = sum(1 for s in slot_taken if s not in etf_set)
    etf_count = len(slot_taken) - stock_count

    source = await _entry_price_source(
        entry_mode, db, account, today, recs=recs, open_positions=open_positions,
        quote_fn=quote_fn, now=now, open_prices=open_prices, chase_cap=chase_cap)
    equity = source.equity
    # The equity ladder sets BOTH the concurrency gate and the per-position
    # dollar cap (equity/slots). A platform tightening (settings.py §8.6) may
    # only narrow the GATE — folding it into the sizing divisor would make each
    # remaining position bigger, which is the opposite of tightening.
    ladder_slots = qsizing.concurrent_slots(equity)
    slot_gate = ladder_slots if max_slots is None else min(ladder_slots, max_slots)

    booked: list[str] = []
    for rec in recs:
        sym = rec.symbol
        if sym in exited_today:
            logger.info("entry_cycle: %s already exited today — no re-entry", sym)
            continue
        price = source.prices.get(sym)
        if price is None:
            logger.warning("entry_cycle: no usable %s for %s — skipped",
                           "09:30 open price" if entry_mode == ENTRY_MODE_OPEN_ONCE
                           else "quote", sym)
            continue
        feats = rec.features or {}
        stop_distance = float(feats.get("stop_distance") or 0)
        if stop_distance <= 0:
            continue
        # v3.1 intraday timing: don't chase. The reco reference price is D-1's
        # close; if the live price has run more than chase_cap above it, wait —
        # a later 15-min cycle may catch a pullback (or the day ends unentered).
        ref_price = float(feats.get("price") or 0)
        if source.chase_cap is not None and ref_price > 0 \
                and price > ref_price * (1 + source.chase_cap):
            logger.info("entry_cycle: %s at %.2f is >%.0f%% above ref %.2f — "
                        "not chasing", sym, price, source.chase_cap * 100, ref_price)
            continue
        adv = float(feats.get("adv") or 0) or None
        # review #26: size against the COST-INCLUSIVE buy price, exactly like
        # the backtest — sizing on the raw quote guarantees InsufficientCash
        # whenever the cash cap binds (booked cost = raw*(1+bps) > cash)
        entry_eff = entry_cost_price(price, adv=adv)
        stop = entry_eff - stop_distance
        if stop <= 0:
            continue
        avail = float(account.cash) - source.cash_offset
        if avail <= 0:
            continue

        pos = None
        if sym in held:
            pos = next(p for p in open_positions if p.symbol == sym)
            # simulator.py gates the add on the RAW open; the ladder has always
            # compared its cost-inclusive price and stays as it is
            gate_price = price if entry_mode == ENTRY_MODE_OPEN_ONCE else entry_eff
            if not qsizing.pyramid_allowed(float(pos.avg_cost), gate_price,
                                           int(pos.adds_done)):
                continue
        elif sym in etf_set:
            if etf_count >= qconfig.ETF_MAX_SLOTS:     # A4-Extra: own slot pool
                continue
        elif stock_count >= slot_gate:
            continue

        qty = qsizing.position_size(equity, entry_eff, stop, risk_pct=risk_pct,
                                    slots=ladder_slots, settled_cash=avail, adv=adv)
        if qty <= 0:
            continue
        try:
            order = await SimLedgerService.open_or_add(
                db, account, symbol=sym, qty=qty, raw_price=price, stop=stop,
                reason="pyramid" if pos is not None else "entry",
                idempotency_key=f"entry:{account.id}:{sym}:{today}",
                trade_date=today, adv=adv, recommendation_id=rec.id,
                position=pos, equity_for_risk=equity, risk_pct=risk_pct)
        except InsufficientCash as e:
            # a marginal symbol must never poison the whole cycle (review #26 —
            # the backtest `continue`s here too)
            logger.warning("entry_cycle: %s skipped — %s", sym, e)
            continue
        if order is not None:
            booked.append(sym)
            if sym in held:
                pass
            elif sym in etf_set:
                etf_count += 1
            else:
                stock_count += 1
                held.add(sym)
    return booked


async def closing_marks(positions, session_date: date, *,
                        bars_fn=qbars.get_bars, quote_fn=None) -> dict[str, float]:
    """End-of-day marks for the equity snapshot and the protections read.

    The backtest marks equity at D's daily CLOSE (simulator's equity_curve[D]),
    so the store's own close for THIS session is the primary source. A live
    quote is only the fallback — post-close Finnhub quotes are >15 min stale by
    design and a thin name may have none — and SimLedgerService.equity falls
    back to avg_cost for whatever is still missing."""
    marks: dict[str, float] = {}
    unmarked: list[str] = []
    for sp in positions:
        try:
            b = bars_fn(sp.symbol, "1d", end=session_date)
        except Exception:
            logger.warning("snapshot: bars failed for %s", sp.symbol, exc_info=True)
            b = None
        close = None
        if b is not None and not b.empty and not b.attrs.get("unadjusted") \
                and b["ts"].iloc[-1].tz_convert("America/New_York").date() == session_date:
            close = float(b["close"].iloc[-1])
        if close is not None and close > 0:
            marks[sp.symbol] = close
        else:
            unmarked.append(sp.symbol)
    from_close = sorted(marks)
    from_quote: list[str] = []
    if unmarked and quote_fn is not None:
        quote_map = await _gather_quotes(quote_fn, unmarked)
        for sym, q in quote_map.items():
            if q.price > 0:
                marks[sym] = q.price
                from_quote.append(sym)
    at_cost = sorted(set(unmarked) - set(from_quote))
    logger.info("snapshot marks — daily close: %s | quote: %s | avg_cost: %s",
                from_close or "-", sorted(from_quote) or "-", at_cost or "-")
    return marks


OPEN_FILL_DRIFT_BPS = 10.0


async def open_fill_drift(db: AsyncSession, account: SimAccount, session_date: date, *,
                          bars_fn=qbars.get_bars,
                          tolerance_bps: float = OPEN_FILL_DRIFT_BPS) -> list[str]:
    """Post-close sentinel for open_once: every fill booked today was priced off
    the SIP 09:30 1min bar, so the session's stored daily ``open`` must agree.
    A drift past tolerance means the two price sources disagreed — reporting
    only, the fill is long booked. Returns one human-readable line per breach."""
    out: list[str] = []
    for symbol, raw_price in await SimLedgerService.get_entry_fills_on(
            db, account, session_date):
        if raw_price <= 0:
            continue
        try:
            b = bars_fn(symbol, "1d", end=session_date)
        except Exception:
            logger.warning("fill reconciliation: bars failed for %s", symbol,
                           exc_info=True)
            continue
        if b.empty or b.attrs.get("unadjusted"):
            continue
        if b["ts"].iloc[-1].tz_convert("America/New_York").date() != session_date:
            continue
        daily_open = float(b["open"].iloc[-1])
        if daily_open <= 0:
            continue
        drift = (raw_price / daily_open - 1.0) * 10_000
        if abs(drift) > tolerance_bps:
            out.append(f"{symbol} filled {raw_price:.4f} vs daily open "
                       f"{daily_open:.4f} ({drift:+.1f}bps)")
    if out:
        logger.error("open_once fill reconciliation: %s", "; ".join(out))
    return out


# --- protections (R1-4) ----------------------------------------------------

async def update_protections(db: AsyncSession, account: SimAccount, equity: float,
                             today: date, *,
                             pause_pct: float = qconfig.DAILY_LOSS_PAUSE_PCT,
                             halt_pct: float = qconfig.PORTFOLIO_DRAWDOWN_HALT_PCT,
                             halt_cooldown_days: int = 30) -> SafetyState:
    """Post-close protections bookkeeping, persisted so restarts keep the state:
    daily-loss pause (blocks the NEXT session's entries), drawdown halt with a
    cooldown + peak-baseline reset on expiry (models A5's manual-review restart —
    same semantics the backtest simulator uses)."""
    state = await get_safety_state(db, account, create=True)
    if state.peak_equity is None:
        state.peak_equity = _dec(equity)

    prev_snap = (await db.execute(
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account.id,
               AccountSnapshot.snapshot_date < today)
        .order_by(AccountSnapshot.snapshot_date.desc()).limit(1)
    )).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    # halt expiry -> modeled human restart: clear + reset the drawdown baseline
    if state.halted and state.halted_until is not None and now >= state.halted_until:
        state.halted = False
        state.halted_until = None
        state.peak_equity = _dec(equity)
        state.reason = "halt expired — baseline reset"

    peak = max(float(state.peak_equity or equity), equity)
    state.peak_equity = _dec(peak)

    if prev_snap is not None and float(prev_snap.equity) > 0:
        day_ret = equity / float(prev_snap.equity) - 1.0
        if day_ret <= -pause_pct:
            # review #35: EXTEND only, never shorten — a manual /pause (30d)
            # must not be silently cut to one session by an automatic pause
            candidate = qcal.next_session(today)
            if state.paused_until is None or candidate > state.paused_until:
                state.paused_until = candidate
                state.reason = f"daily loss {day_ret:.1%} on {today}"

    if not state.halted and equity <= peak * (1.0 - halt_pct):
        state.halted = True
        state.halted_until = now + pd.Timedelta(days=halt_cooldown_days).to_pytimedelta()
        state.reason = f"drawdown {equity / peak - 1.0:.1%} from peak {peak:.0f}"
    return state


# --- position cycle --------------------------------------------------------

async def check_stops(db: AsyncSession, account: SimAccount, *, quote_fn,
                      now: datetime | None = None) -> list[str]:
    """Intraday stop-breach pass: quote at/below the resting stop -> close at
    the quote (a live stop can't fill better than the market). NEVER ratchets
    the trailing stop here — end-of-day information only (backtest F2 rule)."""
    now = now or datetime.now(timezone.utc)
    closed = []
    positions = await SimLedgerService.get_open_positions(db, account.id)
    # review #4: gather all open-position quotes concurrently up front
    quote_map = await _gather_quotes(quote_fn, [p.symbol for p in positions])
    for sp in positions:
        q = quote_map.get(sp.symbol)
        if not quote_is_usable(q, now=now):
            logger.warning("position_cycle: stale/no quote for %s — skipped",
                           sp.symbol)
            continue
        if q.price <= float(sp.stop):
            action = "trailing" if float(sp.stop) >= float(sp.avg_cost) else "hard_stop"
            await SimLedgerService.close_position(
                db, account, sp, raw_price=q.price, reason=action,
                idempotency_key=f"exit:{sp.id}")
            closed.append(sp.symbol)
    return closed
