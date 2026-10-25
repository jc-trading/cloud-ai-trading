"""P5b-2 intraday.nightly: the done short-circuit (session, done and group set),
the watch rows each group's shortlist becomes, the warmup pairs (never the
session itself), backfill only when there is something to fetch, one
fail-closed alert per session, the advisory lock and the beat wiring. Fake
registry, shortlist, backfill and notify; the heartbeat lives in the
rolled-back PG session."""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models_registry  # noqa: F401
from quant.data import eod_correction
from quant.data.registry import IntradayGroupRow, WatchRow
from quant.data.screen import PanelIncomplete
from quant.engine.strategies import M1_ORB_KEY, get_spec

from app.modules.simledger.models import HeartbeatRecord
from app.tasks import intraday_tasks
from tests._pg_rollback import pg_session_rollback  # noqa: F401

# Saturday 03:00 UTC: the last completed session D is Friday 2026-10-23, the
# next session S is Monday 2026-10-26, and D's previous session is 2026-10-22.
NOW = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
D_MINUS_1, D, S = date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)
SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}


def _group(name):
    return IntradayGroupRow(group_id=uuid4(), name=name, strategy_key=M1_ORB_KEY,
                            params=get_spec(M1_ORB_KEY).default_params(), screener=SCREENER)


class FakeRegistry:
    def __init__(self, groups=(), rows=None):
        self.groups = list(groups)
        self.rows = dict(rows or {})
        self.replaced: list = []

    def active_1m_groups(self):
        return list(self.groups)

    def replace_watch_rows(self, session, group_id, rows):
        self.replaced.append((session, group_id, list(rows)))
        name = next(g.name for g in self.groups if g.group_id == group_id)
        kept = [r for r in self.rows.get(session, []) if r.group_id != group_id]
        self.rows[session] = kept + [WatchRow(group_id, name, sym, rank, reason)
                                     for sym, rank, _score, reason in rows]
        return len(rows)

    def intraday_watch_rows(self, session):
        return list(self.rows.get(session, []))


class FakeShortlist:
    def __init__(self, ranked=None, raises=None):
        self.ranked = ranked or {}
        self.raises = raises or {}
        self.calls: list = []

    def __call__(self, spec, cfg, session):
        self.calls.append(session)
        n = len(self.calls)
        if n in self.raises:
            raise self.raises[n]
        return list(self.ranked.get(n, self.ranked.get("*", [])))


class FakeBackfill:
    def __init__(self, **over):
        self.over = over
        self.calls: list = []

    def __call__(self, pairs, *, registry, deadline_s):
        self.calls.append((set(pairs), deadline_s))
        stats = {"done": True, "written": [], "skipped": 0, "empty": [],
                 "iex_uncorrected": [], "failed": [], "remaining": 0, "requests": 0,
                 "fail_closed": None}
        stats.update(self.over)
        return stats


class FakeNotify:
    def __init__(self):
        self.calls: list = []

    async def __call__(self, message, *, event, severity):
        self.calls.append((message, event, severity))
        return True


@pytest_asyncio.fixture
async def run(pg_session_rollback):
    db = pg_session_rollback

    async def _run(registry, *, shortlist=None, backfill=None, notify=None, now=NOW):
        return await intraday_tasks.run_nightly(
            db, now=now, registry=registry, shortlist=shortlist or FakeShortlist(),
            backfill=backfill or FakeBackfill(), notify=notify or FakeNotify())

    _run.db = db
    return _run


async def _beat(db):
    return (await db.execute(select(HeartbeatRecord).where(
        HeartbeatRecord.name == intraday_tasks.BEAT_NAME))).scalar_one_or_none()


async def _meta(db) -> dict:
    return (await _beat(db)).meta


@pytest.mark.asyncio
async def test_t1_no_groups_is_done_without_backfill(run):
    backfill = FakeBackfill()
    assert await run(FakeRegistry(), backfill=backfill) == "done"
    meta = await _meta(run.db)
    assert meta["groups"] == {} and meta["done"] is True
    assert (meta["session"], meta["built_from"]) == (S.isoformat(), D.isoformat())
    assert meta["backfill"]["pairs"] == 0 and meta["backfill"]["fail_closed"] is None
    assert backfill.calls == []


@pytest.mark.asyncio
async def test_t2_same_session_done_same_groups_short_circuits(run, monkeypatch):
    registry = FakeRegistry([_group("zz-a")])
    shortlist = FakeShortlist({"*": [("AAA", 0.02)]})
    assert await run(registry, shortlist=shortlist) == "done"
    stamped = (await _beat(run.db)).last_beat_at

    def boom(*a, **kw):
        raise AssertionError("build_watch must not run")

    monkeypatch.setattr(intraday_tasks, "build_watch", boom)
    assert await run(registry, shortlist=shortlist) == "done"
    assert shortlist.calls == [S]
    assert (await _beat(run.db)).last_beat_at == stamped


@pytest.mark.asyncio
async def test_t3_a_changed_group_set_rebuilds(run):
    a = _group("zz-a")
    shortlist = FakeShortlist({"*": [("AAA", 0.02)]})
    assert await run(FakeRegistry([a]), shortlist=shortlist) == "done"
    registry = FakeRegistry([a, _group("zz-b")])
    assert await run(registry, shortlist=shortlist) == "done"
    assert shortlist.calls == [S, S, S]
    assert sorted((await _meta(run.db))["groups"]) == ["zz-a", "zz-b"]


@pytest.mark.asyncio
async def test_t4_panel_incomplete_is_pending(run):
    registry = FakeRegistry([_group("zz-a")])
    shortlist = FakeShortlist(raises={1: PanelIncomplete("panel short")})
    assert await run(registry, shortlist=shortlist) == "pending"
    meta = await _meta(run.db)
    assert meta["groups"] == {"zz-a": {"status": "pending", "n": 0}}
    assert meta["done"] is False
    assert registry.replaced == []


@pytest.mark.asyncio
async def test_t5_remaining_pairs_keep_it_pending(run):
    registry = FakeRegistry([_group("zz-a")])
    shortlist = FakeShortlist({"*": [("AAA", 0.02)]})
    backfill = FakeBackfill(remaining=1, done=False)
    assert await run(registry, shortlist=shortlist, backfill=backfill) == "pending"
    meta = await _meta(run.db)
    assert meta["done"] is False and meta["backfill"]["remaining"] == 1
    assert meta["groups"]["zz-a"] == {"status": "ok", "n": 1}


@pytest.mark.asyncio
async def test_t6_fail_closed_alerts_once_per_session(run):
    registry = FakeRegistry([_group("zz-a")])
    shortlist = FakeShortlist({"*": [("AAA", 0.02)]})
    backfill = FakeBackfill(fail_closed="disk", done=False)
    notify = FakeNotify()
    assert await run(registry, shortlist=shortlist, backfill=backfill, notify=notify) == "pending"
    assert notify.calls == [(
        f"⛔ intraday.nightly fail-closed (disk): 1min warmup backfill for {S} stopped",
        "data_fail_closed", "critical")]
    meta = await _meta(run.db)
    assert meta["alerted"] == S.isoformat() and meta["backfill"]["fail_closed"] == "disk"

    assert await run(registry, shortlist=shortlist, backfill=backfill, notify=notify) == "pending"
    assert len(notify.calls) == 1
    assert len(backfill.calls) == 2
    assert (await _meta(run.db))["alerted"] == S.isoformat()


@pytest.mark.asyncio
async def test_t7_rows_rank_score_and_reason(run):
    g = _group("zz-a")
    registry = FakeRegistry([g])
    shortlist = FakeShortlist({"*": [("AAA", 0.0123456789), ("BBB", 0.01), ("CCC", 0.009)]})
    assert await run(registry, shortlist=shortlist) == "done"
    assert registry.replaced == [(S, g.group_id, [
        ("AAA", 1, 0.012346, "ret_std(5)=0.0123 rank 1"),
        ("BBB", 2, 0.01, "ret_std(5)=0.0100 rank 2"),
        ("CCC", 3, 0.009, "ret_std(5)=0.0090 rank 3"),
    ])]
    meta = await _meta(run.db)
    assert meta["groups"] == {"zz-a": {"status": "ok", "n": 3}} and meta["symbols"] == 3


@pytest.mark.asyncio
async def test_t8_warmup_pairs_cover_the_two_prior_sessions_never_s(run):
    g = _group("zz-a")
    held_over = WatchRow(g.group_id, g.name, "DDD", 1, "D's list")
    registry = FakeRegistry([g], rows={D: [held_over]})
    shortlist = FakeShortlist({"*": [("AAA", 0.02), ("BBB", 0.01)]})
    backfill = FakeBackfill()
    assert await run(registry, shortlist=shortlist, backfill=backfill) == "done"
    [(pairs, deadline_s)] = backfill.calls
    assert pairs == {("AAA", D_MINUS_1), ("AAA", D), ("BBB", D_MINUS_1), ("BBB", D),
                     ("DDD", D)}
    assert not any(day == S for _, day in pairs)
    assert deadline_s == 1200
    assert (await _meta(run.db))["backfill"]["pairs"] == 5


@pytest.mark.asyncio
async def test_t11_no_completed_session_is_skipped(run, monkeypatch):
    monkeypatch.setattr(eod_correction, "last_completed_session", lambda now=None: None)
    registry = FakeRegistry([_group("zz-a")])
    shortlist = FakeShortlist({"*": [("AAA", 0.02)]})
    assert await run(registry, shortlist=shortlist) == "skipped"
    assert await _meta(run.db) == {"skipped": "no completed session"}
    assert shortlist.calls == []


@pytest.mark.asyncio
async def test_t12_one_failing_group_does_not_stop_the_next(run):
    a, b = _group("zz-a"), _group("zz-b")
    registry = FakeRegistry([a, b])
    shortlist = FakeShortlist({2: [("BBB", 0.01)]}, raises={1: RuntimeError("x" * 250)})
    assert await run(registry, shortlist=shortlist) == "pending"
    groups = (await _meta(run.db))["groups"]
    assert groups["zz-a"] == {"status": "error", "n": 0,
                              "error": f"RuntimeError: {'x' * 250}"[:200]}
    assert len(groups["zz-a"]["error"]) == 200
    assert groups["zz-b"] == {"status": "ok", "n": 1}
    assert registry.replaced == [(S, b.group_id, [("BBB", 1, 0.01, "ret_std(5)=0.0100 rank 1")])]


@asynccontextmanager
async def _other_connection():
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_t9_busy_when_another_connection_holds_the_lock(monkeypatch):
    called: list = []

    async def recorder(*a, **kw):
        called.append(kw)
        return "done"

    monkeypatch.setattr(intraday_tasks, "run_nightly", recorder)
    async with _other_connection() as holder:
        await holder.execute(text("select pg_advisory_lock(:k)"),
                             {"k": intraday_tasks.INTRADAY_LOCK_KEY})
        try:
            assert await intraday_tasks._intraday_nightly() == "busy"
        finally:
            await holder.execute(text("select pg_advisory_unlock(:k)"),
                                 {"k": intraday_tasks.INTRADAY_LOCK_KEY})
    assert called == []


def test_t10_route_beat_and_include():
    from celery.schedules import crontab

    from tasks.celery_app import celery_app
    assert celery_app.conf.task_routes["intraday.nightly"] == {"queue": "bulk"}
    entry = celery_app.conf.beat_schedule["intraday-nightly"]
    assert entry["task"] == "intraday.nightly"
    assert entry["schedule"] == crontab(minute="5,35", hour="2-13")
    assert entry["schedule"].minute == {5, 35} and entry["schedule"].hour == set(range(2, 14))
    assert entry["options"] == {"expires": 1500}
    assert "app.tasks.intraday_tasks" in celery_app.conf.include
