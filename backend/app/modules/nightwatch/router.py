"""Night-watch log API (深夜股票检测 — the G2 paper-observation run, read-only).

  GET /nightwatch/log   "now" panel (cycle heartbeats + protections) plus one
                        row per US session, assembled ENTIRELY from tables the
                        cycles already write: recommendations, sim_orders +
                        sim_fills (system account), llm_calls, account_snapshots.
                        No new tables, no writes — the observation-period code
                        freeze stays intact.

Row semantics — everything is anchored on the US SESSION DATE (ET):
  - signal: the recommendation set published FOR that session (generated the
    previous ET evening / 05:30 MYT) — count, shortlist, ran_at, explained.
  - orders: system-account orders requested during that ET date (entries in
    RTH, exits from the daily exit pass).
  - llm: cost booked on that ET date (the nightly explanation calls — these
    describe the NEXT session's recs; this is a daily cost ledger, not a
    per-recset attribution).
  - equity: the system account's end-of-day snapshot, when one exists.
A session with no recommendations shows recs=0 — either the Mac was offline
(no signal run) or the A2 fail-closed gate suppressed publishing; the
distinction is in Telegram/worker logs, not reconstructable from the DB.

Auth-only (CurrentUser), like /llm/log — the 2026-07-01 /decisions 403 lesson.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.dependencies import DB, CurrentUser
from app.modules.llm.models import LLMCall
from app.modules.simledger.models import (
    AccountSnapshot, HeartbeatRecord, Recommendation, SafetyState,
    SimAccount, SimFill, SimOrder,
)

router = APIRouter(prefix="/nightwatch", tags=["Night watch"])

ET = ZoneInfo("America/New_York")
ENTRY_REASONS = {"entry", "pyramid"}


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _night(nights: dict, d) -> dict:
    key = d.isoformat()
    if key not in nights:
        nights[key] = {
            "date": key,
            "signal": {"ran_at": None, "recommendations": 0,
                       "explained": 0, "shortlist": []},
            "orders": [],
            "entries_filled": 0, "exits_filled": 0, "orders_rejected": 0,
            "llm": {"calls": 0, "cost_usd": 0.0},
            "equity": None, "open_positions": None,
        }
    return nights[key]


@router.get("/log")
async def nightwatch_log(user: CurrentUser, db: DB = None,
                         days: int = Query(default=30, ge=1, le=120)):
    since = date.today() - timedelta(days=days)
    nights: dict[str, dict] = {}

    # --- recommendations FOR each session -------------------------------
    R = Recommendation
    rec_agg = (await db.execute(
        select(R.trade_date, func.count().label("n"),
               func.min(R.created_at).label("ran_at"),
               func.count(R.llm_explanation).label("explained"))
        .where(R.trade_date >= since).group_by(R.trade_date)
    )).all()
    for trade_date, n, ran_at, explained in rec_agg:
        night = _night(nights, trade_date)
        night["signal"].update({
            "recommendations": int(n),
            "explained": int(explained),
            "ran_at": ran_at.isoformat() if ran_at else None,
        })

    shortlist = (await db.execute(
        select(R.trade_date, R.shortlist_rank, R.symbol, R.confidence, R.phase)
        .where(R.trade_date >= since, R.shortlist_rank.isnot(None))
        .order_by(R.trade_date, R.shortlist_rank)
    )).all()
    for trade_date, rank, symbol, confidence, phase in shortlist:
        _night(nights, trade_date)["signal"]["shortlist"].append({
            "rank": rank, "symbol": symbol,
            "confidence": _f(confidence), "phase": phase,
        })

    # --- system-account (对照账户) orders + snapshots ---------------------
    system_account_id = (await db.execute(
        select(SimAccount.id).where(SimAccount.is_system.is_(True))
        .order_by(SimAccount.created_at).limit(1)
    )).scalar_one_or_none()

    if system_account_id is not None:
        orders = (await db.execute(
            select(SimOrder, SimFill.price)
            .outerjoin(SimFill, SimFill.order_id == SimOrder.id)
            .where(SimOrder.account_id == system_account_id,
                   SimOrder.requested_at >= since)
            .order_by(SimOrder.requested_at)
        )).all()
        for order, fill_price in orders:
            night = _night(nights, order.requested_at.astimezone(ET).date())
            filled = order.status == "filled"
            if filled and order.reason in ENTRY_REASONS:
                night["entries_filled"] += 1
            elif filled:
                night["exits_filled"] += 1
            elif order.status == "rejected":
                night["orders_rejected"] += 1
            night["orders"].append({
                "id": str(order.id),
                "requested_at": order.requested_at.isoformat(),
                "symbol": order.symbol, "side": order.side,
                "reason": order.reason, "status": order.status,
                "qty": _f(order.qty), "price": _f(fill_price),
                "reject_reason": order.reject_reason,
            })

        snapshots = (await db.execute(
            select(AccountSnapshot.snapshot_date, AccountSnapshot.equity,
                   AccountSnapshot.open_positions)
            .where(AccountSnapshot.account_id == system_account_id,
                   AccountSnapshot.snapshot_date >= since)
        )).all()
        for snapshot_date, equity, open_positions in snapshots:
            night = _night(nights, snapshot_date)
            night["equity"] = _f(equity)
            night["open_positions"] = open_positions

    # --- LLM cost booked on each ET date --------------------------------
    C = LLMCall
    et_day = func.date(func.timezone("America/New_York", C.created_at))
    llm_agg = (await db.execute(
        select(et_day.label("day"), func.count().label("calls"),
               func.coalesce(func.sum(C.cost_usd), 0).label("cost_usd"))
        .where(C.created_at >= since).group_by(et_day)
    )).all()
    for day, calls, cost_usd in llm_agg:
        _night(nights, day)["llm"] = {
            "calls": int(calls), "cost_usd": _f(cost_usd) or 0.0,
        }

    # --- "now" panel ----------------------------------------------------
    heartbeats = (await db.execute(
        select(HeartbeatRecord).order_by(HeartbeatRecord.name)
    )).scalars().all()
    # scope holds an account uuid in practice (not "global") — surface any
    # row with an active pause/halt, else the first row as the status source
    safety_rows = (await db.execute(select(SafetyState))).scalars().all()
    active = [s for s in safety_rows if s.halted or s.paused_until]
    safety = active[0] if active else (safety_rows[0] if safety_rows else None)

    ordered = sorted(nights.values(), key=lambda n: n["date"], reverse=True)
    return {
        "now": {
            "heartbeats": [{
                "name": h.name,
                "last_beat_at": h.last_beat_at.isoformat() if h.last_beat_at else None,
                "meta": h.meta,
            } for h in heartbeats],
            "safety": None if safety is None else {
                "halted": safety.halted,
                "halted_until": safety.halted_until.isoformat() if safety.halted_until else None,
                "paused_until": safety.paused_until.isoformat() if safety.paused_until else None,
                "reason": safety.reason,
            },
        },
        "nights": ordered,
    }
