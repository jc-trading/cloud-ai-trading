"""Backtest API (P3b).

  POST /backtests                     queue a single / walk_forward run (admin)
  GET  /backtests?group_id=&status=&limit=
                                      runs, newest first
  GET  /backtests/compare?group_ids=&run_ids=&start=
                                      live accounts and succeeded runs side by side
  GET  /backtests/{run_id}            one run (snapshot, metrics, calibration)
  GET  /backtests/{run_id}/equity     its equity curve(s)
  POST /backtests/{run_id}/cancel     cancel a queued run / ask a running one to stop (admin)

The API only writes rows; the research worker claims and executes them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update

from quant import config as qconfig
from quant.backtest import metrics as qmetrics

from app.dependencies import DB, AdminUser, CurrentUser
from app.modules.backtests.models import STATUSES, TERMINAL, BacktestRun
from app.modules.backtests.service import RunConflict, RunInvalid, create_run, live_series
from app.modules.strategy_groups.service import _require_group

router = APIRouter(prefix="/backtests", tags=["Backtests"])

COMPARE_MAX_ITEMS = 8


class CreateRunRequest(BaseModel):
    group_id: UUID
    kind: str
    start: date
    end: date
    is_years: int | None = None
    oos_years: int | None = None
    is_sessions: int | None = None
    oos_sessions: int | None = None
    grid: dict | None = None


def _iso(v):
    return v.isoformat() if v is not None else None


def _summary(r: BacktestRun) -> dict:
    return {
        "id": str(r.id), "group_id": str(r.group_id), "kind": r.kind, "status": r.status,
        "data_start": _iso(r.data_start), "data_end": _iso(r.data_end),
        "progress": r.progress, "cancel_requested": r.cancel_requested,
        "universe_label": r.universe_label, "survivorship_biased": r.survivorship_biased,
        "error": r.error, "artifact_path": r.artifact_path,
        "created_by": str(r.created_by) if r.created_by else None,
        "created_at": _iso(r.created_at), "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
    }


def _detail(r: BacktestRun) -> dict:
    return {**_summary(r), "config_snapshot": r.config_snapshot, "config_hash": r.config_hash,
            "metrics_json": r.metrics_json, "calibration_json": r.calibration_json}


async def _require_run(db, run_id) -> BacktestRun:
    run = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
           ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return run


def _require_succeeded(run: BacktestRun) -> None:
    if run.status != "succeeded":
        raise HTTPException(status_code=409, detail=f"backtest run is {run.status}")


def _curve(run: BacktestRun, name: str) -> dict:
    df = pd.read_csv(qconfig.DATA_ROOT / run.artifact_path / name)
    return {"dates": df["date"].astype(str).tolist(), "values": df["equity"].astype(float).tolist()}


def _ids(raw: str | None, what: str) -> list[UUID]:
    if not raw:
        return []
    try:
        return [UUID(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what}: not a list of UUIDs") from None


@router.post("", status_code=201)
async def create_backtest(data: CreateRunRequest, admin: AdminUser, db: DB = None):
    group = await _require_group(db, data.group_id)
    try:
        run = await create_run(db, group=group, kind=data.kind, start=data.start, end=data.end,
                               is_years=data.is_years, oos_years=data.oos_years,
                               is_sessions=data.is_sessions, oos_sessions=data.oos_sessions,
                               grid=data.grid, user_id=admin.id)
    except RunInvalid as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except RunConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _detail(run)


@router.get("")
async def list_backtests(user: CurrentUser, db: DB = None, group_id: UUID | None = None,
                         status: str | None = Query(default=None),
                         limit: int = Query(default=50, ge=1, le=200)):
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {', '.join(STATUSES)}")
    stmt = select(BacktestRun)
    if group_id is not None:
        stmt = stmt.where(BacktestRun.group_id == group_id)
    if status is not None:
        stmt = stmt.where(BacktestRun.status == status)
    rows = (await db.execute(stmt.order_by(BacktestRun.created_at.desc(), BacktestRun.id)
                             .limit(limit))).scalars().all()
    return [_summary(r) for r in rows]


@router.get("/compare")
async def compare_backtests(user: CurrentUser, db: DB = None,
                            group_ids: str | None = Query(default=None),
                            run_ids: str | None = Query(default=None),
                            start: date | None = Query(default=None)):
    gids, rids = _ids(group_ids, "group_ids"), _ids(run_ids, "run_ids")
    if not 1 <= len(gids) + len(rids) <= COMPARE_MAX_ITEMS:
        raise HTTPException(status_code=422,
                            detail=f"compare takes 1-{COMPARE_MAX_ITEMS} groups + runs")
    items = []
    for gid in gids:
        group = await _require_group(db, gid)
        equity, trades = await live_series(db, group, start)
        item = {"kind": "live", "id": str(group.id), "label": f"{group.name} (live)",
                "group_id": str(group.id), "group_name": group.name, "source": "live",
                "survivorship_biased": False, "universe_label": "live",
                "equity": {"dates": [d.isoformat() for d in equity.index],
                           "values": equity.astype(float).tolist()},
                "metrics": qmetrics.payload(equity, trades) if len(equity) >= 2 else None}
        if item["metrics"] is None:
            item["note"] = "fewer than 2 account snapshots — no live metrics yet"
        items.append(item)
    for rid in rids:
        run = await _require_run(db, rid)
        _require_succeeded(run)
        group = await _require_group(db, run.group_id)
        wf = run.kind == "walk_forward"
        items.append({
            "kind": "backtest", "id": str(run.id),
            "label": f"{group.name} ({run.kind} {run.data_start}..{run.data_end})",
            "group_id": str(group.id), "group_name": group.name,
            "source": f"backtest:{run.kind}" + (":fixed" if wf else ""),
            "survivorship_biased": run.survivorship_biased,
            "universe_label": run.universe_label,
            "equity": _curve(run, "equity_fixed.csv" if wf else "equity.csv"),
            "metrics": run.metrics_json["fixed" if wf else "summary"]})
    return {"start": _iso(start), "start_applies_to": "live", "items": items}


@router.get("/{run_id}")
async def get_backtest(run_id: UUID, user: CurrentUser, db: DB = None):
    return _detail(await _require_run(db, run_id))


@router.get("/{run_id}/equity")
async def get_backtest_equity(run_id: UUID, user: CurrentUser, db: DB = None):
    run = await _require_run(db, run_id)
    _require_succeeded(run)
    if run.kind == "walk_forward":
        return {"fixed": _curve(run, "equity_fixed.csv"),
                "selected": _curve(run, "equity_selected.csv")}
    return {"equity": _curve(run, "equity.csv")}


@router.post("/{run_id}/cancel")
async def cancel_backtest(run_id: UUID, admin: AdminUser, db: DB = None):
    run = await _require_run(db, run_id)
    if run.status in TERMINAL:
        raise HTTPException(status_code=409, detail=f"backtest run is already {run.status}")
    done = (await db.execute(
        update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "queued")
        .values(status="cancelled", finished_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False))).rowcount
    if not done:
        done = (await db.execute(
            update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "running")
            .values(cancel_requested=True)
            .execution_options(synchronize_session=False))).rowcount
    if not done:
        raise HTTPException(status_code=409, detail="backtest run finished meanwhile")
    await db.refresh(run)
    return _detail(run)
