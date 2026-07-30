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
from dataclasses import dataclass
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

from app.modules.simledger.models import Recommendation, SafetyState, SimAccount
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


# THE entry window, ET (review #8): entry_cycle books only inside
# [09:30, 10:10). Single source of truth — the two celery beat slots
# (celery_app.py "quant-entry-cycle-*") must fire inside this window.
ENTRY_WINDOW_ET = ((9, 30), (10, 10))


def in_rth(now: datetime | None = None, *, open_grace_min: int = 0) -> bool:
    """Shared ET/RTH gate (review #15: three hand-rolled copies risked drifting
    when half-day handling lands). open_grace_min shifts the session start for
    consumers that must wait for the first cycles to run (watchdog)."""
    from quant.data import calendar as qcal
    n = now or now_et()
    if not qcal.is_trading_day(n.date()):
        return False
    minutes = n.hour * 60 + n.minute
    return (9 * 60 + 30 + open_grace_min) <= minutes < (16 * 60)


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
                          bars_fn=qbars.get_bars) -> list[dict]:
    """Compute the shortlist + phase reads from daily bars as of session_date.
    Pure given bars_fn. Returns dicts ready to become Recommendation rows with
    trade_date = the NEXT session (decide on D close, act at D+1 open)."""
    rows = []
    for sym in symbols:
        try:
            b = bars_fn(sym, "1d", end=session_date)
        except Exception:
            logger.warning("signal_cycle: bars failed for %s", sym, exc_info=True)
            continue
        sp = qstrategy.StrategyParams()
        if b.empty or len(b) < sp.warmup + 5:
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
            "sector": "unknown",     # populated from the sector cache by caller
        })
    if not rows:
        return []
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
    return out


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


async def daily_exit_management(db: AsyncSession, account: SimAccount,
                                session_date: date, *,
                                exit_params: ExitParams = RECOMMENDED_EXITS,
                                bars_fn=qbars.get_bars) -> list[str]:
    """Post-close exit pass on the system account — the simulator's exact
    ordering: trailing ratchet from info through D-1, fold D's bar into state,
    then close-based exits (reversal/stagnation) at D's close. Stop breaches
    intraday were position_cycle's job; the idempotency key (one close per lot)
    makes an overlap harmless."""
    closed: list[str] = []
    for sp in await SimLedgerService.get_open_positions(db, account.id):
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
        # review #33/#27: a failed sync leaves yesterday's bar as iloc[-1];
        # folding it again double-counts bars_held/reversal and re-tests a
        # cleared low. Only fold a bar for the session under management.
        last_bar_date = b["ts"].iloc[-1].tz_convert("America/New_York").date()
        if last_bar_date != session_date:
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
                params=exit_params, bar_open=float(last_bar["open"]))
            if decision is not None:
                await SimLedgerService.close_position(
                    db, account, sp, raw_price=decision.price, reason=decision.action,
                    idempotency_key=f"exit:{sp.id}")
                closed.append(sp.symbol)
            else:
                sp.stop = _dec(p.stop)
                sp.high_water = _dec(p.high_water)
                sp.reversal_count = p.reversal_count
                sp.bars_held = p.bars_held
        except Exception:
            logger.warning("daily_exit: %s failed — skipped", sp.symbol,
                           exc_info=True)
    return closed


# --- entry cycle -----------------------------------------------------------

async def run_entries(db: AsyncSession, account: SimAccount, today: date, *,
                      quote_fn, now: datetime | None = None,
                      risk_pct: float = qconfig.PER_TRADE_RISK_PCT) -> list[str]:
    """Book shortlisted entries at the live open-ish quote. Idempotent per
    (account, symbol, session). Protections must be checked by the caller."""
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
    held = {p.symbol for p in open_positions}
    etf_set = set(qconfig.ETF_WHITELIST)
    stock_count = sum(1 for s in held if s not in etf_set)
    etf_count = len(held) - stock_count

    # review #3: prefetch the union of held + shortlist concurrently — each
    # symbol is quoted exactly once (held names used to be quoted twice)
    quote_map = await _gather_quotes(quote_fn,
                                     [p.symbol for p in open_positions]
                                     + [r.symbol for r in recs])
    quotes: dict[str, float] = {}
    for p in open_positions:
        q = quote_map.get(p.symbol)
        if quote_is_usable(q, now=now):
            quotes[p.symbol] = q.price
    equity = SimLedgerService.equity(account, open_positions, quotes)
    slots = qsizing.concurrent_slots(equity)

    booked: list[str] = []
    for rec in recs:
        sym = rec.symbol
        q = quote_map.get(sym)
        if not quote_is_usable(q, now=now):
            logger.warning("entry_cycle: stale/no quote for %s — skipped", sym)
            continue
        feats = rec.features or {}
        stop_distance = float(feats.get("stop_distance") or 0)
        if stop_distance <= 0:
            continue
        adv = float(feats.get("adv") or 0) or None
        # review #26: size against the COST-INCLUSIVE buy price, exactly like
        # the backtest — sizing on the raw quote guarantees InsufficientCash
        # whenever the cash cap binds (booked cost = raw*(1+bps) > cash)
        entry_eff = entry_cost_price(q.price, adv=adv)
        stop = entry_eff - stop_distance
        if stop <= 0:
            continue

        pos = None
        if sym in held:
            pos = next(p for p in open_positions if p.symbol == sym)
            if not qsizing.pyramid_allowed(float(pos.avg_cost), entry_eff,
                                           int(pos.adds_done)):
                continue
        elif sym in etf_set:
            if etf_count >= qconfig.ETF_MAX_SLOTS:     # A4-Extra: own slot pool
                continue
        elif stock_count >= slots:
            continue

        qty = qsizing.position_size(equity, entry_eff, stop, risk_pct=risk_pct,
                                    slots=slots, settled_cash=float(account.cash),
                                    adv=adv)
        if qty <= 0:
            continue
        try:
            order = await SimLedgerService.open_or_add(
                db, account, symbol=sym, qty=qty, raw_price=q.price, stop=stop,
                reason="pyramid" if pos is not None else "entry",
                idempotency_key=f"entry:{account.id}:{sym}:{today}",
                trade_date=today, adv=adv, recommendation_id=rec.id,
                position=pos, equity_for_risk=equity)
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
    from app.modules.simledger.models import AccountSnapshot

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
