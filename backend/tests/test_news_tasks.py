"""P6a news.cycle: cursor and origin across ticks, the advisory lock (held on
its own connection for the whole cycle), partial fetch failures and the
NEWS_ENABLED kill switch. The work session is the rolled-back PG session; the
lock goes over real connections to the same database."""

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models_registry  # noqa: F401
from app.modules.market.models import Security
from app.modules.news import ingest as ingest_mod
from app.modules.news.models import NewsItem, NewsItemSource
from app.modules.simledger.models import HeartbeatRecord
from app.tasks import news_tasks
from tests._pg_rollback import pg_session_rollback  # noqa: F401

RELEVANT = {"ZQXA": (1, "shortlist")}
COUNTED = (NewsItem, NewsItemSource)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(id_, at, symbol="ZQXA"):
    return {"id": id_, "headline": f"{symbol} {hashlib.sha256(str(id_).encode()).hexdigest()}",
            "author": f"Writer {id_}", "summary": "", "url": f"https://benzinga.com/n/{id_}",
            "symbols": [symbol], "created_at": _iso(at), "updated_at": _iso(at)}


class _ReleasesSessionLocksOnCommit:
    """The work session as NullPool sees it: every commit hands the connection
    back and closes it, so session-level advisory locks taken on it are gone."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        await self._session.commit()
        await self._session.execute(text("select pg_advisory_unlock_all()"))


class _Fetch:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, start, end, *, max_pages, timeout):
        self.calls.append({"start": start, "end": end, "max_pages": max_pages,
                           "timeout": timeout})
        return self.results.pop(0)


@pytest_asyncio.fixture
async def cycle(pg_session_rollback, monkeypatch):
    db = pg_session_rollback
    db.add(Security(code="ZQXA", asset_type="stock", tradable=True,
                    first_seen=date(2026, 1, 2), last_seen=date(2026, 10, 16)))
    await db.flush()
    db.info["baseline"] = {model: await _count(db, model) for model in COUNTED}

    @asynccontextmanager
    async def session_local():
        yield _ReleasesSessionLocksOnCommit(db)

    async def relevance(_db):
        return RELEVANT

    monkeypatch.setattr(news_tasks, "CeleryAsyncSessionLocal", session_local)
    monkeypatch.setattr(news_tasks, "relevance_map", relevance)
    monkeypatch.setattr(news_tasks.settings, "NEWS_ENABLED", True)

    def install(*results):
        fake = _Fetch(*results)
        monkeypatch.setattr(news_tasks.alpaca_news, "fetch", fake)
        return fake

    return SimpleNamespace(db=db, install=install)


@asynccontextmanager
async def _other_connection():
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


async def _try_lock(conn) -> bool:
    got = (await conn.execute(text("select pg_try_advisory_lock(:k)"),
                              {"k": news_tasks.NEWS_LOCK_KEY})).scalar()
    if got:
        await conn.execute(text("select pg_advisory_unlock(:k)"),
                           {"k": news_tasks.NEWS_LOCK_KEY})
    return got


async def _meta(db) -> dict:
    return (await db.execute(select(HeartbeatRecord.meta)
                             .where(HeartbeatRecord.name == "news_cycle"))).scalar_one()


async def _origins(db) -> list[tuple[str, str]]:
    return sorted((await db.execute(select(NewsItem.origin, NewsItem.status))).all())


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar()


async def _added(db, model) -> int:
    return await _count(db, model) - db.info["baseline"][model]


async def _start_with_no_news(db) -> None:
    """First-tick tests need an empty news table; the delete rolls back with the fixture."""
    await db.execute(delete(NewsItem))


@pytest.mark.asyncio
async def test_first_tick_is_backfill_then_live(cycle):
    await _start_with_no_news(cycle.db)
    now = datetime.now(timezone.utc)
    old = [_raw(1, now - timedelta(hours=48)), _raw(2, now - timedelta(hours=1))]
    fresh = [_raw(3, now + timedelta(minutes=5))]
    fake = cycle.install((old, False, None), (fresh, False, None))

    assert await news_tasks._news_cycle() == "ok"
    first = await _meta(cycle.db)
    assert first["bootstrap_at"] and (first["backfill"], first["live"]) == (2, 0)
    assert await _origins(cycle.db) == [("backfill", "skipped")] * 2
    assert fake.calls[0]["start"] < now - timedelta(hours=71)

    assert await news_tasks._news_cycle() == "ok"
    second = await _meta(cycle.db)
    assert (second["new"], second["live"], second["pending"]) == (1, 1, 1)
    assert await _origins(cycle.db) == [("backfill", "skipped")] * 2 + [("live", "pending")]
    overlap = timedelta(minutes=news_tasks.settings.NEWS_POLL_OVERLAP_MINUTES)
    assert fake.calls[1]["start"] == datetime.fromisoformat(old[1]["created_at"]) - overlap
    boot = (await cycle.db.execute(select(func.min(NewsItem.discovered_at)))).scalar()
    assert second["bootstrap_at"] == boot.isoformat()


@pytest.mark.asyncio
async def test_truncated_backfill_stays_backfill_on_later_ticks(cycle):
    await _start_with_no_news(cycle.db)
    now = datetime.now(timezone.utc)
    oldest = [_raw(i, now - timedelta(hours=70 - i)) for i in range(1, 4)]
    newer = [_raw(i, now - timedelta(hours=30 - i)) for i in range(4, 7)]
    cycle.install((oldest, True, None), (newer, False, None))

    assert await news_tasks._news_cycle() == "ok"
    assert (await _meta(cycle.db))["truncated"] is True
    assert await news_tasks._news_cycle() == "ok"
    second = await _meta(cycle.db)
    assert (second["backfill"], second["live"], second["truncated"]) == (3, 0, False)
    assert await _origins(cycle.db) == [("backfill", "skipped")] * 6


@pytest.mark.asyncio
async def test_busy_when_another_connection_holds_the_lock(cycle):
    fake = cycle.install(([], False, None))
    async with _other_connection() as holder:
        await holder.execute(text("select pg_advisory_lock(:k)"),
                             {"k": news_tasks.NEWS_LOCK_KEY})
        try:
            assert await news_tasks._news_cycle() == "busy"
        finally:
            await holder.execute(text("select pg_advisory_unlock(:k)"),
                                 {"k": news_tasks.NEWS_LOCK_KEY})
    assert fake.calls == []


@pytest.mark.asyncio
async def test_lock_survives_the_ingest_commit(cycle, monkeypatch):
    now = datetime.now(timezone.utc)
    cycle.install(([_raw(1, now - timedelta(hours=2))], False, None))
    probes = []
    real_beat = news_tasks._beat

    async def probing_beat(db, name, **meta):
        async with _other_connection() as probe:
            probes.append(await _try_lock(probe))
        await real_beat(db, name, **meta)

    monkeypatch.setattr(news_tasks, "_beat", probing_beat)
    assert await news_tasks._news_cycle() == "ok"
    assert (await _meta(cycle.db))["new"] == 1
    assert probes == [False]
    async with _other_connection() as probe:
        assert await _try_lock(probe) is True


@pytest.mark.asyncio
async def test_fetch_error_is_recorded_without_raising(cycle):
    cycle.install(([], True, "URLError: x"))
    assert await news_tasks._news_cycle() == "ok"
    meta = await _meta(cycle.db)
    assert meta["fetch_error"] == "URLError: x" and meta["truncated"] is True
    assert {k: meta[k] for k in ("fetched", "new", "merged", "updated")} == dict.fromkeys(
        ("fetched", "new", "merged", "updated"), 0)
    assert await _added(cycle.db, NewsItem) == 0


@pytest.mark.asyncio
async def test_partial_page_is_kept_and_not_duplicated_next_tick(cycle):
    now = datetime.now(timezone.utc)
    page1 = [_raw(1, now - timedelta(hours=3)), _raw(2, now - timedelta(hours=2))]
    page2 = [_raw(3, now - timedelta(hours=1))]
    cycle.install((page1, True, "URLError: x"), (page1 + page2, False, None))

    assert await news_tasks._news_cycle() == "ok"
    first = await _meta(cycle.db)
    assert (first["new"], first["fetch_error"]) == (2, "URLError: x")
    assert await news_tasks._news_cycle() == "ok"
    second = await _meta(cycle.db)
    assert (second["new"], second["updated"], second["fetch_error"]) == (1, 2, None)
    assert await _added(cycle.db, NewsItemSource) == 3


@pytest.mark.asyncio
async def test_disabled_skips_everything(cycle, monkeypatch):
    fake = cycle.install(([], False, None))
    monkeypatch.setattr(news_tasks.settings, "NEWS_ENABLED", False)
    assert await news_tasks._news_cycle() == "disabled"
    assert fake.calls == []
    assert await _meta(cycle.db) == {"disabled": True}


@pytest.mark.asyncio
async def test_unexpected_failure_returns_error(cycle, monkeypatch):
    cycle.install(([_raw(1, datetime.now(timezone.utc))], False, None))

    async def broken(_db):
        raise RuntimeError("boom")

    monkeypatch.setattr(news_tasks, "relevance_map", broken)
    assert await news_tasks._news_cycle() == "error"


@pytest.mark.asyncio
async def test_malformed_article_does_not_wedge_the_cycle(cycle):
    now = datetime.now(timezone.utc)
    good = [_raw(1, now - timedelta(hours=3)), _raw(2, now - timedelta(hours=2))]
    bad = {**_raw(3, now - timedelta(hours=1)), "created_at": "not a timestamp"}
    cycle.install((good[:1] + [bad] + good[1:], False, None))
    assert await news_tasks._news_cycle() == "ok"
    meta = await _meta(cycle.db)
    assert (meta["fetched"], meta["new"], meta["invalid"]) == (3, 2, 1)
    assert await _added(cycle.db, NewsItem) == 2


@pytest.mark.asyncio
async def test_database_rejected_article_does_not_wedge_the_cycle(cycle, monkeypatch):
    monkeypatch.setattr(ingest_mod, "_clean", lambda value: value)
    now = datetime.now(timezone.utc)
    bad = {**_raw(2, now - timedelta(hours=2)), "headline": "rejected\x00"}
    cycle.install(([_raw(1, now - timedelta(hours=3)), bad, _raw(3, now - timedelta(hours=1))],
                   False, None))
    assert await news_tasks._news_cycle() == "ok"
    meta = await _meta(cycle.db)
    assert (meta["fetched"], meta["new"], meta["invalid"]) == (3, 2, 1)
    assert await _added(cycle.db, NewsItem) == 2


@pytest.fixture(autouse=True)
def _no_real_llm_or_message(monkeypatch):
    async def not_sent(message, *, event, severity):
        return False

    monkeypatch.setattr(news_tasks.settings, "AI_PROVIDER", "deepseek")
    monkeypatch.setattr(news_tasks.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(news_tasks.notify_mod, "_notify", not_sent)


def _fake_steps(monkeypatch, fetch, *, extract_raises=False):
    events = []

    async def run_extraction(db, *, now):
        events.append(("extract", len(fetch.calls)))
        if extract_raises:
            raise RuntimeError("boom")
        return {"llm": "ok", "calls": 0}

    async def maybe_notify(db, *, now):
        events.append(("notify", len(fetch.calls)))
        return 2

    monkeypatch.setattr(news_tasks.extract, "run_extraction", run_extraction)
    monkeypatch.setattr(news_tasks.notify_mod, "maybe_notify", maybe_notify)
    return events


@pytest.mark.asyncio
async def test_extraction_and_notify_run_after_the_fetch(cycle, monkeypatch):
    fetch = cycle.install(([_raw(1, datetime.now(timezone.utc) - timedelta(hours=1))],
                           False, None))
    events = _fake_steps(monkeypatch, fetch)
    assert await news_tasks._news_cycle() == "ok"
    assert events == [("extract", 1), ("notify", 1)]
    meta = await _meta(cycle.db)
    assert meta["extract"] == {"llm": "ok", "calls": 0} and meta["notified"] == 2


@pytest.mark.asyncio
async def test_extraction_failure_keeps_the_ingest(cycle, monkeypatch):
    fetch = cycle.install(([_raw(1, datetime.now(timezone.utc) - timedelta(hours=1))],
                           False, None))
    events = _fake_steps(monkeypatch, fetch, extract_raises=True)
    assert await news_tasks._news_cycle() == "ok"
    meta = await _meta(cycle.db)
    assert meta["extract"] == {"llm": "error"} and meta["new"] == 1
    assert [name for name, _ in events] == ["extract", "notify"]
    assert await _added(cycle.db, NewsItem) == 1


@pytest.mark.asyncio
async def test_disabled_runs_neither_extraction_nor_notify(cycle, monkeypatch):
    fetch = cycle.install(([], False, None))
    events = _fake_steps(monkeypatch, fetch)
    monkeypatch.setattr(news_tasks.settings, "NEWS_ENABLED", False)
    assert await news_tasks._news_cycle() == "disabled"
    assert events == [] and fetch.calls == []
