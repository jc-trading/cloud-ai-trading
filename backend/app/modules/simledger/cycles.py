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
from app.modules.simledger.service import SimLedgerService, _dec

logger = logging.getLogger(__name__)

# R0-9 G1 baseline consensus (2026-07-30): only C1 moved off design defaults.
RECOMMENDED_FUNNEL = qfunnel.FunnelParams(min_confidence=65.0)     # [C1]
RECOMMENDED_EXITS = ExitParams()                                    # C5/C6/C8 = defaults
QUOTE_STALE_SECONDS = 15 * 60
HALT_SENTINEL = "/app/runtime/HALT"          # its EXISTENCE refuses all entries


@dataclass
class QuoteReading:
    price: float
    at: datetime


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
        b = bars_fn(sp.symbol, "1d", end=session_date)
        if b.empty:
            continue
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

    quotes: dict[str, float] = {}
    for p in open_positions:
        q = quote_fn(p.symbol)
        if quote_is_usable(q, now=now):
            quotes[p.symbol] = q.price
    equity = SimLedgerService.equity(account, open_positions, quotes)
    slots = qsizing.concurrent_slots(equity)

    booked: list[str] = []
    for rec in recs:
        sym = rec.symbol
        q = quote_fn(sym)
        if not quote_is_usable(q, now=now):
            logger.warning("entry_cycle: stale/no quote for %s — skipped", sym)
            continue
        feats = rec.features or {}
        stop_distance = float(feats.get("stop_distance") or 0)
        if stop_distance <= 0:
            continue
        entry_ref = q.price
        stop = entry_ref - stop_distance
        if stop <= 0:
            continue

        if sym in held:
            pos = next(p for p in open_positions if p.symbol == sym)
            if not qsizing.pyramid_allowed(float(pos.avg_cost), entry_ref,
                                           int(pos.adds_done)):
                continue
        elif sym in etf_set:
            if etf_count >= qconfig.ETF_MAX_SLOTS:     # A4-Extra: own slot pool
                continue
        elif stock_count >= slots:
            continue

        qty = qsizing.position_size(equity, entry_ref, stop, risk_pct=risk_pct,
                                    slots=slots, settled_cash=float(account.cash),
                                    adv=float(feats.get("adv") or 0) or None)
        if qty <= 0:
            continue
        order = await SimLedgerService.open_or_add(
            db, account, symbol=sym, qty=qty, raw_price=entry_ref, stop=stop,
            reason="pyramid" if sym in held else "entry",
            idempotency_key=f"entry:{account.id}:{sym}:{today}",
            trade_date=today, adv=float(feats.get("adv") or 0) or None,
            recommendation_id=rec.id)
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


# --- position cycle --------------------------------------------------------

async def check_stops(db: AsyncSession, account: SimAccount, *, quote_fn,
                      now: datetime | None = None) -> list[str]:
    """Intraday stop-breach pass: quote at/below the resting stop -> close at
    the quote (a live stop can't fill better than the market). NEVER ratchets
    the trailing stop here — end-of-day information only (backtest F2 rule)."""
    now = now or datetime.now(timezone.utc)
    closed = []
    for sp in await SimLedgerService.get_open_positions(db, account.id):
        q = quote_fn(sp.symbol)
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
