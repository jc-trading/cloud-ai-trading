"""Strategy-group API (P1 reads, P3a lifecycle).

  GET   /strategy-groups              every group, oldest first (optional ?status=)
  GET   /strategy-groups/registry     strategies + bounds, screener vocabulary
  POST  /strategy-groups              create a group (+ its system account)
  POST  /strategy-groups/{group_id}/clone
                                      copy a group with overrides
  PATCH /strategy-groups/{group_id}/status
                                      active / paused / archived
  GET   /strategy-groups/{group_id}/runs?days=&cycle=
                                      that group's per-cycle outcomes
  GET   /strategy-groups/{group_id}   one group + its params schema + account block
  GET   /strategy-groups/{group_id}/screen?date=
                                      that group's screener run for one session

There is no default group: every per-strategy endpoint elsewhere takes a
required ``group_id`` picked from this list. Reads are auth-only (CurrentUser),
like the other dashboard reads; writes need AdminUser.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quant import config as qconfig
from quant.data import calendar as qcal
from quant.data import panel as qpanel
from quant.data import screen as qscreen
from quant.engine import trade_rules
from quant.engine.screener import (
    DEFAULT_SCREENER, FIELDS, MAX_LIST, MAX_RULES, OPS, VALUE_BOUNDS, ScreenerSpecError,
    parse_spec,
)
from quant.engine.strategies import (
    CROSS_RULES, REGISTRY, StrategyParamsError, get_spec, ui_schema,
)

from app.dependencies import DB, AdminUser, CurrentUser
from app.modules.simledger.models import AccountSnapshot, SimAccount
from app.modules.simledger.service import SimLedgerService
from app.modules.strategy_groups.models import (
    GROUP_STATUSES, RUN_CYCLES, StrategyGroup, StrategyGroupRun,
)
from app.modules.strategy_groups.service import (
    GroupConflict, GroupInvalid, _require_group, clone_group, create_group, set_status,
)

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


class CreateGroupRequest(BaseModel):
    name: str
    strategy_key: str
    params_json: dict = {}
    screener_json: dict = {}
    trade_settings_json: dict = {}
    notes: str | None = Field(default=None, max_length=2000)
    starting_capital: float = 2000
    status: Literal["active", "paused"] = "paused"


class CloneGroupRequest(BaseModel):
    name: str
    params_json: dict | None = None
    screener_json: dict | None = None
    trade_settings_json: dict | None = None
    notes: str | None = Field(default=None, max_length=2000)
    starting_capital: float | None = None
    status: Literal["active", "paused"] = "paused"
    archive_source: bool = False


class StatusRequest(BaseModel):
    status: Literal["active", "paused", "archived"]


@router.get("/registry")
async def get_registry(user: CurrentUser):
    return {
        "strategies": [
            {"key": key, "description": spec.description, "params_schema": ui_schema(key),
             "cross_rules": [{"left": a, "op": "<", "right": b} for a, b in CROSS_RULES[key]]}
            for key, spec in REGISTRY.items()],
        "screener": {"fields": FIELDS, "ops": OPS, "value_bounds": VALUE_BOUNDS,
                     "max_rules": MAX_RULES, "max_list": MAX_LIST,
                     "asset_types": list(qconfig.ELIGIBLE_ASSET_TYPES),
                     "default": DEFAULT_SCREENER},
        "trade_settings": trade_rules.registry_entry(),
        "timeframes": ["1d"],
        "markets": ["us"],
    }


async def _write(db, op):
    try:
        return await op
    except GroupInvalid as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except GroupConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="name already exists") from None


@router.post("", status_code=201)
async def create_strategy_group(data: CreateGroupRequest, admin: AdminUser, db: DB = None):
    group = await _write(db, create_group(db, user_id=admin.id, **data.model_dump()))
    return await _detail(db, group)


@router.post("/{group_id}/clone", status_code=201)
async def clone_strategy_group(group_id: UUID, data: CloneGroupRequest, admin: AdminUser,
                               db: DB = None):
    src = await _require_group(db, group_id)
    group = await _write(db, clone_group(db, src, user_id=admin.id, **data.model_dump()))
    return await _detail(db, group)


@router.patch("/{group_id}/status")
async def set_group_status(group_id: UUID, data: StatusRequest, admin: AdminUser,
                           db: DB = None):
    group = await _require_group(db, group_id)
    await _write(db, set_status(db, group, data.status))
    return await _detail(db, group)


@router.get("/{group_id}/runs")
async def list_group_runs(group_id: UUID, user: CurrentUser, db: DB = None,
                          days: int = Query(default=30, ge=1, le=365),
                          cycle: str | None = Query(default=None)):
    await _require_group(db, group_id)
    if cycle and cycle not in RUN_CYCLES:
        raise HTTPException(status_code=422,
                            detail=f"cycle must be one of {', '.join(RUN_CYCLES)}")
    stmt = select(StrategyGroupRun).where(
        StrategyGroupRun.group_id == group_id,
        StrategyGroupRun.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if cycle:
        stmt = stmt.where(StrategyGroupRun.cycle == cycle)
    rows = (await db.execute(
        stmt.order_by(StrategyGroupRun.created_at.desc()).limit(500))).scalars().all()
    return [{"id": str(r.id), "cycle": r.cycle, "session_date": r.session_date.isoformat(),
             "seq": r.seq, "outcome": r.outcome, "meta": r.meta,
             "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/{group_id}")
async def get_group_detail(group_id: UUID, user: CurrentUser, db: DB = None):
    return await _detail(db, await _require_group(db, group_id))


async def _detail(db, group: StrategyGroup) -> dict:
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


@router.get("/{group_id}/screen")
async def get_group_screen(group_id: UUID, user: CurrentUser, db: DB = None,
                           for_date: date | None = Query(default=None, alias="date")):
    """Run the group's screener for one session (default: the latest complete
    panel session). The pool includes the group account's open positions, as
    signal_cycle's does."""
    group = await _require_group(db, group_id)
    if not group.screener_json:
        raise HTTPException(status_code=409,
                            detail="group uses the legacy v1 pool (screener_json is empty)")
    try:
        spec = parse_spec(group.screener_json)
    except ScreenerSpecError as e:
        raise HTTPException(status_code=409, detail=f"screener_json invalid: {e}")
    if for_date is not None and not qcal.is_trading_day(for_date):
        raise HTTPException(status_code=422, detail=f"{for_date} is not a trading day")
    as_of = for_date or qpanel.latest_complete()
    if as_of is None:
        raise HTTPException(status_code=409, detail="no complete panel yet")
    if not qpanel.is_complete(as_of):
        raise HTTPException(status_code=409, detail=f"no complete panel for {as_of}")
    positions = await SimLedgerService.get_open_positions(db, group.sim_account_id)
    held = sorted({p.symbol for p in positions})
    try:
        res = await asyncio.to_thread(qscreen.run_screen, spec, as_of, held=held)
    except qscreen.PanelIncomplete:
        raise HTTPException(status_code=409, detail=f"no complete panel for {as_of}")
    picked = set(res.screened) | set(spec.include)
    return {
        "group_id": str(group.id), "group_name": group.name,
        "as_of": res.as_of.isoformat(), "evaluated": res.evaluated,
        "asset_types": list(spec.asset_types),
        "rules": [{"index": s.index, "field": s.field, "window": s.window, "op": s.op,
                   "value": s.value, "on_null": s.on_null, "passed": s.passed,
                   "null": s.null, "passed_cumulative": s.passed_cumulative}
                  for s in res.rule_stats],
        "screened": len(res.screened),
        "include_added": list(res.include_added),
        "include_ignored": list(res.include_ignored),
        "excluded_removed": list(res.excluded_removed),
        "held_added": sorted(set(res.pool) - picked),
        "pool_size": len(res.pool),
        "pool_cap": qconfig.SCREEN_POOL_MAX,
        "over_cap": len(res.pool) > qconfig.SCREEN_POOL_MAX,
        "pool": list(res.pool),
    }
