"""Sim-ledger API (R1-7 backend half, Direction v3).

  GET  /sim/recommendations   latest recommendation feed (shortlist + phase)
  GET  /sim/account           the caller's practice account + the 对照账户 view
  POST /sim/trade             manual practice trade (buy/sell) on the caller's
                              own account — the "自己 vs 系统" learning loop
Auth-only (CurrentUser) — the 2026-07-01 /decisions 403 lesson: never gate a
dashboard read behind an admin permission.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.dependencies import DB, CurrentUser
from app.modules.fundamentals.finnhub_client import FinnhubClient
from app.modules.simledger.models import AccountSnapshot, Recommendation
from app.modules.simledger.service import (InsufficientCash, SimLedgerService,
                                           SimLedgerError)
from app.modules.simledger import cycles

router = APIRouter(prefix="/sim", tags=["Sim ledger"])

# The honest scoreboard badge = the FIXED deployed config's stitched
# walk-forward OOS, produced by `python -m quant.research.r09 --fixed-oos`
# (review #10 + assessment A1: the per-window-calibrated aggregate PF 1.34 is
# NOT the deployed config's performance and must not be shown as such).
_BADGE_FILE = "/app/cat-data/r09/fixed_oos.json"
_badge_cache: dict = {"mtime": None, "value": None}


def oos_badge() -> dict:
    import json
    import os
    try:
        mtime = os.path.getmtime(_BADGE_FILE)
        if _badge_cache["mtime"] != mtime:
            data = json.loads(open(_BADGE_FILE).read())
            _badge_cache.update(mtime=mtime, value=data.get("badge"))
        if _badge_cache["value"]:
            return _badge_cache["value"]
    except (OSError, ValueError):
        pass
    return {"profit_factor": None, "win_rate": None, "avg_r": None,
            "source": "no fixed-param OOS result on file — run "
                      "quant.research.r09 --fixed-oos"}


class TradeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0,
                               description="required for buys — no lot without a stop")


@router.get("/recommendations")
async def recommendations(user: CurrentUser, db: DB = None,
                          for_date: date | None = Query(default=None)):
    """Latest published feed (defaults to the newest trade_date). Phase wording
    is DESCRIPTIVE by design — see the G1 report §3.2."""
    td = for_date
    if td is None:
        td = (await db.execute(
            select(Recommendation.trade_date)
            .order_by(Recommendation.trade_date.desc()).limit(1)
        )).scalar_one_or_none()
    if td is None:
        return {"trade_date": None, "oos_badge": oos_badge(), "items": []}
    rows = list((await db.execute(
        select(Recommendation).where(Recommendation.trade_date == td)
        .order_by(Recommendation.shortlist_rank.nulls_last(),
                  Recommendation.confidence.desc())
    )).scalars().all())
    return {
        "trade_date": td, "oos_badge": oos_badge(),
        "items": [{
            "symbol": r.symbol, "rank": r.shortlist_rank,
            "direction": r.direction, "confidence": float(r.confidence),
            "phase": r.phase, "phase_reason": r.phase_reason,
            "features": r.features, "llm_explanation": r.llm_explanation,
        } for r in rows],
    }


async def _account_payload(db, account) -> dict:
    positions = await SimLedgerService.get_open_positions(db, account.id)
    snaps = list((await db.execute(
        select(AccountSnapshot).where(AccountSnapshot.account_id == account.id)
        .order_by(AccountSnapshot.snapshot_date)
    )).scalars().all())
    return {
        "id": str(account.id), "name": account.name,
        "is_system": account.is_system,
        "starting_capital": float(account.starting_capital),
        "cash": float(account.cash),
        "positions": [{
            "symbol": p.symbol, "shares": float(p.shares),
            "avg_cost": float(p.avg_cost), "stop": float(p.stop),
            "entry_date": p.entry_date.isoformat(), "adds_done": p.adds_done,
        } for p in positions],
        "equity_curve": [{"date": s.snapshot_date.isoformat(),
                          "equity": float(s.equity)} for s in snaps],
    }


@router.get("/account")
async def account(user: CurrentUser, db: DB = None):
    """The caller's own practice account (created on first use) plus the
    system 对照账户 (read-only view) for the '自己 vs 系统' comparison."""
    mine = await SimLedgerService.get_or_create_account(db, user.id, "practice")
    system = await SimLedgerService.system_account(db)
    await db.commit()
    return {
        "mine": await _account_payload(db, mine),
        "system": await _account_payload(db, system) if system else None,
    }


@router.post("/trade")
async def trade(payload: TradeRequest, user: CurrentUser, db: DB = None):
    """Manual practice trade at the live quote (same cost model as the system).
    Buys REQUIRE a stop — the platform never opens an unprotected lot."""
    account = await SimLedgerService.get_or_create_account(db, user.id, "practice")
    symbol = payload.symbol.upper()
    reading = cycles.finnhub_quote(FinnhubClient(), symbol)
    if reading is None:
        raise HTTPException(status_code=422, detail=f"no live quote for {symbol}")
    now = datetime.now(timezone.utc)
    if not cycles.quote_is_usable(reading, now=now):
        raise HTTPException(status_code=422, detail=f"stale quote for {symbol}")
    price = reading.price

    try:
        if payload.side == "buy":
            if payload.stop is None or payload.stop >= price:
                raise HTTPException(status_code=422,
                                    detail="a buy requires stop < current price")
            order = await SimLedgerService.open_or_add(
                db, account, symbol=symbol, qty=payload.qty, raw_price=price,
                stop=payload.stop, reason="manual",
                idempotency_key=f"manual:{uuid4()}", trade_date=now.date())
        else:
            pos = await SimLedgerService.get_open_position(db, account.id, symbol)
            if pos is None:
                raise HTTPException(status_code=422, detail=f"no open lot in {symbol}")
            # review #30: closes are whole-lot only — a partial qty would be
            # silently ignored while the receipt echoed it. Be honest instead.
            if abs(payload.qty - float(pos.shares)) > 1e-6:
                raise HTTPException(
                    status_code=422,
                    detail=f"sells close the whole lot: qty must equal "
                           f"{float(pos.shares)} for {symbol}")
            order = await SimLedgerService.close_position(
                db, account, pos, raw_price=price, reason="manual",
                idempotency_key=f"manual:{uuid4()}")
    except InsufficientCash as e:
        raise HTTPException(status_code=422, detail=str(e))
    except SimLedgerError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await db.commit()
    return {"order_id": str(order.id), "symbol": symbol, "side": payload.side,
            "qty": payload.qty, "raw_price": price,
            "cash_after": float(account.cash)}
