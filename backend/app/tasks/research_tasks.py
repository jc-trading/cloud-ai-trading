"""research.dispatch — every 60 s on the research worker (queue ``research``):
reap orphaned runs, claim the oldest queued backtest run and execute it.

The research worker is ONE process with concurrency 1, so any row still
``running`` when dispatch starts belongs to a worker that died mid-run (OOM,
restart) — the reaper fails it instead of re-running it. ``finish`` only
touches a row that is still ``running``, the second guard should that
invariant ever break. No new run is claimed in the evening blackout, when
signal_cycle and universe_sync want the VM's memory.
"""

from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timezone
from datetime import time as dtime

from celery import shared_task
from sqlalchemy import func, select, text, update

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.backtests.models import BacktestRun
from app.tasks.quant_tasks import _run_async

logger = logging.getLogger(__name__)

RESEARCH_CLAIM_BLACKOUT_UTC = (dtime(21, 15), dtime(23, 30))
PROGRESS_EVERY_S = 10.0
ORPHAN_ERROR = ("orphaned: worker restarted mid-run "
                "(check docker inspect cat_celery_research for OOMKilled)")

_CLAIM = text(
    "UPDATE backtest_runs SET status = 'running', started_at = now() "
    "WHERE id = (SELECT id FROM backtest_runs WHERE status = 'queued' "
    "ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id")


async def reap_orphans(db) -> int:
    res = await db.execute(
        update(BacktestRun).where(BacktestRun.status == "running")
        .values(status="failed", finished_at=func.now(), error=ORPHAN_ERROR)
        .execution_options(synchronize_session=False))
    return res.rowcount


async def claim_next(db):
    row = (await db.execute(_CLAIM)).first()
    return row[0] if row else None


async def load_run(db, run_id) -> BacktestRun:
    return (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()


async def write_progress(db, run_id, message: str) -> None:
    await db.execute(
        update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "running")
        .values(progress={"message": message,
                          "heartbeat_at": datetime.now(timezone.utc).isoformat()})
        .execution_options(synchronize_session=False))


async def should_stop(db, run_id) -> bool:
    row = (await db.execute(select(BacktestRun.cancel_requested, BacktestRun.status)
                            .where(BacktestRun.id == run_id))).one()
    return bool(row.cancel_requested) or row.status != "running"


async def finish(db, run_id, *, status: str, metrics_json=None, calibration_json=None,
                 artifact_path=None, error=None) -> bool:
    values = {"status": status, "finished_at": func.now()}
    for key, value in (("metrics_json", metrics_json), ("calibration_json", calibration_json),
                       ("artifact_path", artifact_path), ("error", error)):
        if value is not None:
            values[key] = value
    res = await db.execute(
        update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "running")
        .values(**values).execution_options(synchronize_session=False))
    return res.rowcount == 1


async def _tx(session_factory, fn, *a, **kw):
    async with session_factory() as db:
        r = await fn(db, *a, **kw)
        await db.commit()
        return r


def _default_runner(run: BacktestRun, *, progress, check) -> dict:
    from quant import config as qconfig
    from quant.research import group_backtest as gb

    snap = run.config_snapshot
    if snap.get("timeframe") == "1m":
        return _intraday_runner(run, progress=progress, check=check)
    gcfg = gb.GroupConfig.from_snapshot(snap)
    if run.kind == "single":
        result = gb.run_single(gcfg, run.data_start, run.data_end, check=check,
                               progress=progress)
        calibration_json = None
    else:
        spec = snap["run"]
        result = gb.run_walk_forward(gcfg, run.data_start, run.data_end,
                                     is_years=spec["is_years"], oos_years=spec["oos_years"],
                                     grid=spec["grid"], check=check, progress=progress)
        calibration_json = gb.jsonable(result["calibration"])
    rel = f"backtests/{run.id}"
    gb.write_artifacts(qconfig.DATA_ROOT / rel, result)
    return {"metrics_json": gb.jsonable(result["metrics"]),
            "calibration_json": calibration_json, "artifact_path": rel}


def _intraday_runner(run: BacktestRun, *, progress, check) -> dict:
    """The 1m path of ``_default_runner``. Missing 1min data fails the run and
    leaves a need file for the backfill CLI instead of fetching anything here."""
    from quant import config as qconfig
    from quant.research import group_backtest as gb
    from quant.research import intraday_backtest as ib

    snap = run.config_snapshot
    gcfg = ib.IntradayGroupConfig.from_snapshot(snap)
    try:
        if run.kind == "single":
            result = ib.run_single_1m(gcfg, run.data_start, run.data_end, check=check,
                                      progress=progress)
            calibration_json = None
        else:
            spec = snap["run"]
            result = ib.run_walk_forward_1m(gcfg, run.data_start, run.data_end,
                                            is_sessions=spec["is_sessions"],
                                            oos_sessions=spec["oos_sessions"],
                                            grid=spec["grid"], check=check, progress=progress)
            calibration_json = gb.jsonable(result["calibration"])
    except ib.MissingIntradayData as e:
        need = qconfig.INTRADAY_ROOT / "needs" / f"bt-{run.id}.parquet"
        need.parent.mkdir(parents=True, exist_ok=True)
        ib.write_need(need, e.pairs)
        raise RuntimeError(f"missing 1min data: {len(e.pairs)} symbol-days — need file "
                           f"bt-{run.id}.parquet; run backfill then resubmit") from None
    rel = f"backtests/{run.id}"
    ib.write_artifacts_1m(qconfig.DATA_ROOT / rel, result)
    return {"metrics_json": gb.jsonable(result["metrics"]),
            "calibration_json": calibration_json, "artifact_path": rel}


def dispatch_once(*, run_async=_run_async, session_factory=None, runner=None,
                  now=lambda: datetime.now(timezone.utc), clock=time.monotonic) -> str:
    """One dispatch tick: blackout check, reap, claim, run, finish. Returns
    blackout / idle / succeeded / cancelled / failed. Progress writes and
    cancel checks are throttled to one DB round-trip per PROGRESS_EVERY_S."""
    factory = session_factory or CeleryAsyncSessionLocal

    def tx(fn, *a, **kw):
        return run_async(_tx(factory, fn, *a, **kw))

    lo, hi = RESEARCH_CLAIM_BLACKOUT_UTC
    if lo <= now().astimezone(timezone.utc).time() < hi:
        return "blackout"
    reaped = tx(reap_orphans)
    if reaped:
        logger.warning("research.dispatch: reaped %d orphaned run(s)", reaped)
    run_id = tx(claim_next)
    if run_id is None:
        return "idle"
    from quant.research.group_backtest import Cancelled

    run = tx(load_run, run_id)
    last = {"progress": None, "check": None}

    def due(key: str) -> bool:
        t = clock()
        if last[key] is None or t - last[key] >= PROGRESS_EVERY_S:
            last[key] = t
            return True
        return False

    def progress(message) -> None:
        if due("progress"):
            tx(write_progress, run_id, str(message))

    def check() -> None:
        if due("check") and tx(should_stop, run_id):
            raise Cancelled()

    try:
        out = (runner or _default_runner)(run, progress=progress, check=check)
    except Cancelled:
        tx(finish, run_id, status="cancelled")
        return "cancelled"
    except Exception:
        logger.exception("research.dispatch: run %s failed", run_id)
        tx(finish, run_id, status="failed", error=traceback.format_exc()[-2000:])
        return "failed"
    tx(finish, run_id, status="succeeded", **out)
    return "succeeded"


@shared_task(name="research.dispatch", acks_late=False, soft_time_limit=6 * 3600,
             time_limit=6 * 3600 + 600)
def dispatch():
    return dispatch_once()
