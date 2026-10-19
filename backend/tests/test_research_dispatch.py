"""P3b research.dispatch: the claim / reap / finish SQL against real
PostgreSQL (rolled back), and the dispatch orchestration with fakes."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.models_registry  # noqa: F401
from quant.research.group_backtest import Cancelled

from app.modules.backtests.models import BacktestRun
from app.modules.strategy_groups.models import StrategyGroup
from app.tasks import research_tasks as rt
from tests._pg_rollback import pg_session_rollback  # noqa: F401

T0 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


async def _row(db, *, status="queued", created_at=T0, **over) -> BacktestRun:
    gid = (await db.execute(select(StrategyGroup.id).limit(1))).scalar_one()
    if status in ("succeeded", "failed", "cancelled"):
        over["finished_at"] = T0
    if status == "succeeded":
        over["metrics_json"] = {"summary": {}}
    run = BacktestRun(id=uuid4(), group_id=gid, kind="single", status=status,
                      config_snapshot={}, config_hash="x" * 64,
                      data_start=date(2024, 1, 2), data_end=date(2024, 12, 31),
                      universe_label="pit_sp500", survivorship_biased=False,
                      created_at=created_at, **over)
    db.add(run)
    await db.flush()
    return run


async def _status(db, run_id):
    return (await db.execute(select(BacktestRun.status, BacktestRun.error, BacktestRun.finished_at,
                                    BacktestRun.started_at)
                             .where(BacktestRun.id == run_id))).one()


# --- SQL (real PostgreSQL, rolled back) -------------------------------------------

@pytest.mark.asyncio
async def test_claim_takes_the_oldest_queued_and_only_one(pg_session_rollback):
    db = pg_session_rollback
    newer = await _row(db, created_at=T0 + timedelta(seconds=5))
    older = await _row(db, created_at=T0)
    assert await rt.claim_next(db) == older.id
    assert (await _status(db, older.id)).status == "running"
    assert (await _status(db, older.id)).started_at is not None
    assert (await _status(db, newer.id)).status == "queued"
    statuses = (await db.execute(select(BacktestRun.status).where(
        BacktestRun.id.in_([older.id, newer.id])))).scalars().all()
    assert sorted(statuses) == ["queued", "running"]


@pytest.mark.asyncio
async def test_claim_returns_none_without_queued_rows(pg_session_rollback):
    db = pg_session_rollback
    await _row(db, status="succeeded")
    assert (await db.execute(select(BacktestRun.id).where(
        BacktestRun.status == "queued"))).scalars().all() == []
    assert await rt.claim_next(db) is None


@pytest.mark.asyncio
async def test_reaper_fails_running_rows_and_they_are_never_reclaimed(pg_session_rollback):
    db = pg_session_rollback
    orphan = await _row(db, status="running")
    queued = await _row(db, created_at=T0 + timedelta(seconds=1))
    assert await rt.reap_orphans(db) >= 1
    st = await _status(db, orphan.id)
    assert st.status == "failed" and st.error == rt.ORPHAN_ERROR and st.finished_at is not None
    assert await rt.claim_next(db) == queued.id
    assert (await _status(db, orphan.id)).status == "failed"


@pytest.mark.asyncio
async def test_finish_does_not_overwrite_a_reaped_row(pg_session_rollback):
    db = pg_session_rollback
    run = await _row(db, status="running")
    await rt.reap_orphans(db)
    assert await rt.finish(db, run.id, status="succeeded", metrics_json={"summary": {}},
                           artifact_path="backtests/x") is False
    st = await _status(db, run.id)
    assert st.status == "failed" and st.error == rt.ORPHAN_ERROR


@pytest.mark.asyncio
async def test_finish_writes_a_running_row_once(pg_session_rollback):
    db = pg_session_rollback
    run = await _row(db, status="running")
    assert await rt.finish(db, run.id, status="succeeded", metrics_json={"summary": {"a": 1}},
                           artifact_path="backtests/x") is True
    row = (await db.execute(select(BacktestRun).where(BacktestRun.id == run.id))).scalar_one()
    await db.refresh(row)
    assert row.status == "succeeded" and row.metrics_json == {"summary": {"a": 1}}
    assert row.calibration_json is None and row.finished_at is not None
    assert await rt.finish(db, run.id, status="failed", error="late") is False


@pytest.mark.asyncio
async def test_should_stop_on_cancel_request_or_lost_running_status(pg_session_rollback):
    db = pg_session_rollback
    running = await _row(db, status="running")
    assert await rt.should_stop(db, running.id) is False
    asked = await _row(db, status="running", cancel_requested=True)
    assert await rt.should_stop(db, asked.id) is True
    gone = await _row(db, status="failed", error="x")
    assert await rt.should_stop(db, gone.id) is True


@pytest.mark.asyncio
async def test_write_progress_only_touches_running_rows(pg_session_rollback):
    db = pg_session_rollback
    running = await _row(db, status="running")
    queued = await _row(db)
    for r in (running, queued):
        await rt.write_progress(db, r.id, "features 3/10")
    rows = {r.id: r for r in (await db.execute(select(BacktestRun).where(
        BacktestRun.id.in_([running.id, queued.id])))).scalars()}
    for r in rows.values():
        await db.refresh(r)
    assert rows[running.id].progress["message"] == "features 3/10"
    assert "heartbeat_at" in rows[running.id].progress
    assert rows[queued.id].progress == {}


# --- orchestration (fakes) --------------------------------------------------------

class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


class _Fake:
    def __init__(self, monkeypatch, queued=1, stop=False):
        self.queued = [uuid4() for _ in range(queued)]
        self.running = []
        self.finished = []
        self.progress = []
        self.reaped = 0
        self.stop = stop

        async def reap(db):
            self.reaped += 1
            return 0

        async def claim(db):
            if not self.queued:
                return None
            self.running.append(self.queued.pop(0))
            return self.running[-1]

        async def load(db, run_id):
            return run_id

        async def progress(db, run_id, message):
            self.progress.append(message)

        async def should_stop(db, run_id):
            return self.stop

        async def finish(db, run_id, **kw):
            self.finished.append((run_id, kw))
            return True

        for name, fn in (("reap_orphans", reap), ("claim_next", claim), ("load_run", load),
                         ("write_progress", progress), ("should_stop", should_stop),
                         ("finish", finish)):
            monkeypatch.setattr(rt, name, fn)

    def dispatch(self, runner, **kw):
        kw.setdefault("now", lambda: T0)
        return rt.dispatch_once(run_async=asyncio.run, session_factory=_Session,
                                runner=runner, **kw)


def _never(*a, **k):
    raise AssertionError("runner must not run")


def test_idle_when_nothing_is_queued(monkeypatch):
    fake = _Fake(monkeypatch, queued=0)
    assert fake.dispatch(_never) == "idle"
    assert fake.reaped == 1 and fake.finished == []


def test_blackout_claims_nothing(monkeypatch):
    fake = _Fake(monkeypatch)
    assert fake.dispatch(_never, now=lambda: datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc)) \
        == "blackout"
    assert fake.reaped == 0 and len(fake.queued) == 1
    assert fake.dispatch(lambda run, **k: {"metrics_json": {}},
                         now=lambda: datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc)) \
        == "succeeded"


def test_success_finishes_with_the_runner_output(monkeypatch):
    fake = _Fake(monkeypatch)
    out = {"metrics_json": {"summary": {}}, "calibration_json": None,
           "artifact_path": "backtests/x"}
    assert fake.dispatch(lambda run, **k: out) == "succeeded"
    assert fake.finished == [(fake.running[0], {"status": "succeeded", **out})]


def test_cancel_request_finishes_cancelled(monkeypatch):
    fake = _Fake(monkeypatch, stop=True)

    def runner(run, *, progress, check):
        check()
        raise AssertionError("check() should have raised Cancelled")

    assert fake.dispatch(runner) == "cancelled"
    assert fake.finished == [(fake.running[0], {"status": "cancelled"})]


def test_runner_error_finishes_failed_with_traceback(monkeypatch):
    fake = _Fake(monkeypatch)

    def runner(run, **k):
        raise ValueError("boom in the backtest")

    assert fake.dispatch(runner) == "failed"
    (run_id, kw), = fake.finished
    assert kw["status"] == "failed"
    assert "ValueError: boom in the backtest" in kw["error"] and len(kw["error"]) <= 2000


def test_one_dispatch_runs_exactly_one_of_two_queued(monkeypatch):
    fake = _Fake(monkeypatch, queued=2)
    calls = []
    assert fake.dispatch(lambda run, **k: calls.append(run) or {"metrics_json": {}}) \
        == "succeeded"
    assert calls == fake.running and len(calls) == 1
    assert len(fake.queued) == 1


def test_progress_and_check_are_throttled(monkeypatch):
    fake = _Fake(monkeypatch)
    ticks = iter([0.0, 0.0, 3.0, 3.0, 9.9, 9.9, 10.0, 10.0, 15.0, 15.0, 20.5, 20.5])
    stops = []

    async def should_stop(db, run_id):
        stops.append(1)
        return False

    monkeypatch.setattr(rt, "should_stop", should_stop)

    def runner(run, *, progress, check):
        for i in range(6):
            progress(f"step {i}")
            check()
        return {"metrics_json": {}}

    assert fake.dispatch(runner, clock=lambda: next(ticks)) == "succeeded"
    assert fake.progress == ["step 0", "step 3", "step 5"]
    assert len(stops) == 3


def test_dispatch_task_does_not_ack_late():
    assert rt.dispatch.acks_late is False
    assert rt.dispatch.name == "research.dispatch"
    assert Cancelled.__name__ == "Cancelled"


@pytest.mark.asyncio
async def test_none_in_a_jsonb_column_is_sql_null_not_json_null(pg_session_rollback):
    db = pg_session_rollback
    run = await _row(db, metrics_json=None, calibration_json=None)
    nulls = (await db.execute(select(BacktestRun.metrics_json.is_(None),
                                     BacktestRun.calibration_json.is_(None))
                              .where(BacktestRun.id == run.id))).one()
    assert tuple(nulls) == (True, True)
