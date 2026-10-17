"""Strategy-group API (P1, read-only).

  GET /strategy-groups              every group, oldest first (optional ?status=)
  GET /strategy-groups/{group_id}   one group + its params schema + account block

There is no default group: every per-strategy endpoint elsewhere takes a
required ``group_id`` picked from this list. Auth-only (CurrentUser), like the
other dashboard reads.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from quant.engine.strategies import StrategyParamsError, get_spec

from app.dependencies import DB, CurrentUser
from app.modules.simledger.models import AccountSnapshot, SimAccount
from app.modules.simledger.service import SimLedgerService
from app.modules.strategy_groups.models import GROUP_STATUSES, StrategyGroup
from app.modules.strategy_groups.service import _require_group

router = APIRouter(prefix="/strategy-groups", tags=["Strategy groups"])


def _summary(g: StrategyGroup) -> dict:
    return {
        "id": str(g.id), "name": g.name, "market": g.market,
        "strategy_key": g.strategy_key, "timeframe": g.timeframe,
        "status": g.status, "sim_account_id": str(g.sim_account_id),
        "created_at": g.created_at.isoformat(),
        "archived_at": g.archived_at.isoformat() if g.archived_at else None,
        "notes": g.notes,
    }


@router.get("")
async def list_groups(user: CurrentUser, db: DB = None,
                      status: str | None = Query(default=None)):
    if status is not None and status not in GROUP_STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {', '.join(GROUP_STATUSES)}")
    stmt = select(StrategyGroup).order_by(StrategyGroup.created_at, StrategyGroup.id)
    if status is not None:
        stmt = stmt.where(StrategyGroup.status == status)
    return [_summary(g) for g in (await db.execute(stmt)).scalars().all()]


@router.get("/{group_id}")
async def get_group_detail(group_id: UUID, user: CurrentUser, db: DB = None):
    group = await _require_group(db, group_id)
    try:
        params_schema = get_spec(group.strategy_key).params_schema()
    except StrategyParamsError:
        params_schema = None
    account = await db.get(SimAccount, group.sim_account_id)
    positions = await SimLedgerService.get_open_positions(db, account.id)
    snap = (await db.execute(
        select(AccountSnapshot).where(AccountSnapshot.account_id == account.id)
        .order_by(AccountSnapshot.snapshot_date.desc()).limit(1)
    )).scalar_one_or_none()
    return {
        **_summary(group),
        "params_json": group.params_json,
        "screener_json": group.screener_json,
        "trade_settings_json": group.trade_settings_json,
        "risk_json": group.risk_json,
        "params_schema": params_schema,
        "account": {
            "id": str(account.id), "name": account.name,
            "entry_mode": account.entry_mode,
            "starting_capital": float(account.starting_capital),
            "cash": float(account.cash),
            "open_positions": len(positions),
            "last_snapshot": None if snap is None else {
                "date": snap.snapshot_date.isoformat(), "equity": float(snap.equity),
            },
        },
    }
