"""P7a nightly evaluation: which price claims get an outcome and when, the
no-data grace period, the silent held-position review, and the news.evaluate
task (kill switch, advisory lock, heartbeat, routing, beat). Real PostgreSQL
(rolled back); bars, the panel's last complete session and the held set are
injected, and ``_notify`` is a recorder."""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from quant.data import calendar as qcal

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.news import evaluate
from app.modules.news.ingest import platform_source
from app.modules.news.models import (
    NewsItem, NewsItemSource, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory
)
from app.modules.news.relevance import TIER_HELD, TIER_SHORTLIST
from app.modules.simledger.models import HeartbeatRecord
from app.tasks import news_tasks
from tests._pg_rollback import pg_session_rollback  # noqa: F401

ANCHOR, END = date(2026, 10, 12), date(2026, 10, 14)
LAST = date(2026, 10, 16)
NOW = datetime(2026, 10, 17, 3, 40, tzinfo=timezone.utc)
EARLIER = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
SETTINGS = (("NEWS_PRIOR_STRENGTH", 10.0), ("NEWS_PRIOR_MEAN_DEFAULT", 0.40),
            ("NEWS_PRIOR_MIN_GLOBAL_N", 100), ("NEWS_SOURCE_MIN_N", 20),
            ("NEWS_DISABLE_BELOW_LIFT", 0.75), ("NEWS_DISABLE_CONSECUTIVE_DAYS", 3),
            ("NEWS_HIT_MIN_EXCESS_PCT", 1.0), ("NEWS_NODATA_GRACE_SESSIONS", 5))
FULL = {ANCHOR: (100.0, 101.0), date(2026, 10, 13): (101.0, 102.0), END: (103.0, 105.0)}
FLAT = {ANCHOR: (100.0, 100.0), date(2026, 10, 13): (100.0, 100.0), END: (100.0, 101.0)}


class Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, message, *, event, severity):
        self.calls.append({"text": message, "event": event, "severity": severity})
        return True


class Bars:
    def __init__(self, table: dict, *, raises=()):
        self.table = table
        self.raises = set(raises)
        self.calls = []

    def __call__(self, symbol, timeframe, end=None):
        self.calls.append(symbol)
        if symbol in self.raises:
            raise RuntimeError("bars unavailable")
        rows = self.table.get(symbol, {})
        df = pd.DataFrame({
            "ts": [pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC")
                   for d in rows],
            "open": [o for o, _ in rows.values()],
            "close": [c for _, c in rows.values()],
        })
        df.attrs["unadjusted"] = False
        return df


@pytest_asyncio.fixture
async def db(pg_session_rollback, monkeypatch):
    s = pg_session_rollback
    for key, value in SETTINGS:
        monkeypatch.setattr(settings, key, value)
    await s.execute(delete(NewsItem))
    await s.execute(delete(NewsSourceScoreHistory))
    src = NewsSource(id=uuid4(), kind="author", name=f"benzinga/ZQXP7 {uuid4().hex[:8]}",
                     parent_id=(await platform_source(s)).id)
    s.add(src)
    await s.flush()
    s.info["source"] = src
    return s


async def _claim(db, symbol="ZQXPA", *, claim_type="price", evaluate_after=END,
                 review_status="none", origin="live") -> NewsItemSymbol:
    item = NewsItem(id=uuid4(), origin=origin, headline=f"ZQXP7 {uuid4().hex}",
                    headline_norm=f"zqxp7 {uuid4().hex}", published_at=EARLIER)
    db.add(item)
    await db.flush()
    db.add(NewsItemSource(id=uuid4(), news_id=item.id, source_id=db.info["source"].id,
                          provider="alpaca", external_id=f"zqxp7-{uuid4().hex}",
                          published_at=EARLIER))
    row = NewsItemSymbol(
        id=uuid4(), news_id=item.id, symbol=symbol, claim_type=claim_type,
        claim_text="FDA approval" if claim_type == "non_price" else None, direction="up",
        probability=70, expected_move_pct=2, horizon_days=3, anchor_session=ANCHOR,
        evaluate_after=evaluate_after, suggested_action="review", action_reason="p=70, held",
        review_status=review_status, review_at=None if review_status == "none" else EARLIER,
        reviewed_at=EARLIER if review_status in ("done", "dismissed") else None)
    db.add(row)
    await db.flush()
    return row


def _held(*symbols, tier=TIER_HELD):
    async def relevance(_db):
        return {s: (tier, "held" if tier == TIER_HELD else "shortlist") for s in symbols}
    return relevance


async def _run(db, monkeypatch, *, last=LAST, bars=None, relevance=None) -> SimpleNamespace:
    rec = Recorder()
    bars = bars or Bars({"ZQXPA": FULL, "SPY": FLAT})
    monkeypatch.setattr(evaluate, "_notify", rec)
    out = await evaluate.run_evaluation(db, now=NOW, latest_complete=lambda: last,
                                        bars_fn=bars, sectors_fn=dict,
                                        relevance_fn=relevance or _held())
    return SimpleNamespace(out=out, rec=rec, bars=bars)


async def _outcomes(db) -> list[NewsOutcome]:
    return list((await db.execute(select(NewsOutcome)
                                  .execution_options(populate_existing=True))).scalars())


async def _fresh(db, row: NewsItemSymbol) -> NewsItemSymbol:
    return (await db.execute(select(NewsItemSymbol).where(NewsItemSymbol.id == row.id)
                             .execution_options(populate_existing=True))).scalar_one()


@pytest.mark.asyncio
async def test_v1_no_complete_session_skips(db, monkeypatch):
    await _claim(db)
    rec = Recorder()
    bars = Bars({})
    monkeypatch.setattr(evaluate, "_notify", rec)
    out = await evaluate.run_evaluation(db, now=NOW, latest_complete=lambda: None,
                                        bars_fn=bars, sectors_fn=dict, relevance_fn=_held())
    assert out == {"last": None, "skipped": "no complete session"}
    assert await _outcomes(db) == [] and bars.calls == []


@pytest.mark.asyncio
async def test_v2_horizon_not_ended_is_not_evaluated(db, monkeypatch):
    await _claim(db, evaluate_after=date(2026, 10, 19))
    run = await _run(db, monkeypatch)
    assert run.out["price_due"] == 0 and await _outcomes(db) == []


@pytest.mark.asyncio
async def test_v3_non_price_claims_are_left_for_p7b(db, monkeypatch):
    await _claim(db, claim_type="non_price")
    run = await _run(db, monkeypatch)
    assert run.out["price_due"] == 0 and await _outcomes(db) == []


@pytest.mark.asyncio
async def test_scored_outcome_and_source_score(db, monkeypatch):
    row = await _claim(db)
    await _claim(db, "ZQXPB")
    bars = Bars({"ZQXPA": FULL, "ZQXPB": FULL, "SPY": FLAT})
    run = await _run(db, monkeypatch, bars=bars)
    assert (run.out["price_due"], run.out["scored"], run.out["no_data"]) == (2, 2, 0)
    o = next(o for o in await _outcomes(db) if o.news_symbol_id == row.id)
    assert (o.status, o.method, o.scoring_eligible, o.hit) == ("scored", "price", True, True)
    assert (float(o.anchor_price), float(o.end_price), float(o.excess_move_pct)) == (
        100.0, 105.0, 4.0)
    assert (o.anchor_session, o.end_session) == (ANCHOR, END)
    assert sorted(bars.calls) == ["SPY", "ZQXPA", "ZQXPB"]
    src = (await db.execute(select(NewsSource).where(NewsSource.id == db.info["source"].id)
                            .execution_options(populate_existing=True))).scalar_one()
    assert (src.n_evaluated, src.hits) == (2, 2)


@pytest.mark.asyncio
async def test_v4_missing_bar_waits_out_the_grace_period(db, monkeypatch):
    await _claim(db)
    bars = Bars({"ZQXPA": {ANCHOR: (100.0, 101.0)}, "SPY": FLAT})
    early = await _run(db, monkeypatch, last=END, bars=bars)
    assert (early.out["deferred"], early.out["no_data"]) == (1, 0)
    assert await _outcomes(db) == []

    before_grace = qcal.advance(END, 4, unit="sessions")
    still = await _run(db, monkeypatch, last=before_grace, bars=bars)
    assert still.out["deferred"] == 1 and await _outcomes(db) == []

    late = await _run(db, monkeypatch, last=qcal.advance(END, 5, unit="sessions"), bars=bars)
    assert (late.out["deferred"], late.out["no_data"]) == (0, 1)
    [o] = await _outcomes(db)
    assert (o.status, o.reason, o.hit, o.score) == ("no_data", "no end bar", None, None)
    src = (await db.execute(select(NewsSource).where(NewsSource.id == db.info["source"].id)
                            .execution_options(populate_existing=True))).scalar_one()
    assert (src.n_evaluated, late.out["global_n"]) == (0, 0)


@pytest.mark.asyncio
async def test_v5_held_done_claim_is_reopened_silently(db, monkeypatch):
    row = await _claim(db, review_status="done")
    run = await _run(db, monkeypatch, relevance=_held("ZQXPA"))
    fresh = await _fresh(db, row)
    assert (fresh.review_status, fresh.reviewed_at, fresh.review_at) == ("open", None, NOW)
    assert fresh.action_reason == "horizon ended: hit excess +4.0% vs claim up 2% · held"
    assert run.out["held_reviews"] == 1 and run.rec.calls == []


@pytest.mark.asyncio
async def test_v5_no_data_reason(db, monkeypatch):
    row = await _claim(db, review_status="dismissed")
    bars = Bars({"ZQXPA": FULL})
    await _run(db, monkeypatch, last=qcal.advance(END, 5, unit="sessions"), bars=bars,
               relevance=_held("ZQXPA"))
    fresh = await _fresh(db, row)
    assert fresh.review_status == "open"
    assert fresh.action_reason == "horizon ended: no price data (no SPY bar) · held"


@pytest.mark.asyncio
async def test_v6_open_or_not_held_rows_are_left_alone(db, monkeypatch):
    already = await _claim(db, review_status="open")
    shortlisted = await _claim(db, "ZQXPB", review_status="done")
    bars = Bars({"ZQXPA": FULL, "ZQXPB": FULL, "SPY": FLAT})
    relevance = _held("ZQXPA")

    async def mixed(_db):
        return {**await relevance(_db), "ZQXPB": (TIER_SHORTLIST, "shortlist")}

    run = await _run(db, monkeypatch, bars=bars, relevance=mixed)
    a, b = await _fresh(db, already), await _fresh(db, shortlisted)
    assert (a.review_status, a.review_at, a.action_reason) == ("open", EARLIER, "p=70, held")
    assert (b.review_status, b.reviewed_at, b.action_reason) == ("done", EARLIER, "p=70, held")
    assert run.out["held_reviews"] == 0 and run.out["scored"] == 2


@pytest.mark.asyncio
async def test_v7_bars_failure_becomes_no_data_after_grace(db, monkeypatch):
    await _claim(db)
    await _claim(db, "ZQXPB")
    bars = Bars({"ZQXPB": FULL, "SPY": FLAT}, raises={"ZQXPA"})
    run = await _run(db, monkeypatch, last=qcal.advance(END, 5, unit="sessions"), bars=bars)
    assert (run.out["no_data"], run.out["scored"]) == (1, 1)
    reasons = sorted((o.status, o.reason) for o in await _outcomes(db))
    assert reasons == [("no_data", "no anchor bar"), ("scored", None)]


@asynccontextmanager
async def _other_connection():
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def task(pg_session_rollback, monkeypatch):
    db = pg_session_rollback
    calls = []

    @asynccontextmanager
    async def session_local():
        yield db

    async def run_evaluation(_db, *, now):
        calls.append(now)
        return {"last": "2026-10-16", "scored": 0}

    monkeypatch.setattr(news_tasks, "CeleryAsyncSessionLocal", session_local)
    monkeypatch.setattr(news_tasks.evaluate, "run_evaluation", run_evaluation)
    monkeypatch.setattr(news_tasks.settings, "NEWS_ENABLED", True)
    return SimpleNamespace(db=db, calls=calls)


async def _meta(db) -> dict:
    return (await db.execute(select(HeartbeatRecord.meta)
                             .where(HeartbeatRecord.name == "news_evaluate"))).scalar_one()


@pytest.mark.asyncio
async def test_v8_task_ok_writes_the_heartbeat(task):
    assert await news_tasks._news_evaluate() == "ok"
    assert len(task.calls) == 1
    assert await _meta(task.db) == {"last": "2026-10-16", "scored": 0}


@pytest.mark.asyncio
async def test_v8_task_disabled(task, monkeypatch):
    monkeypatch.setattr(news_tasks.settings, "NEWS_ENABLED", False)
    assert await news_tasks._news_evaluate() == "disabled"
    assert task.calls == [] and await _meta(task.db) == {"disabled": True}


@pytest.mark.asyncio
async def test_v8_task_busy_when_the_news_lock_is_held(task):
    async with _other_connection() as holder:
        await holder.execute(text("select pg_advisory_lock(:k)"),
                             {"k": news_tasks.NEWS_LOCK_KEY})
        try:
            assert await news_tasks._news_evaluate() == "busy"
        finally:
            await holder.execute(text("select pg_advisory_unlock(:k)"),
                                 {"k": news_tasks.NEWS_LOCK_KEY})
    assert task.calls == []


@pytest.mark.asyncio
async def test_v8_task_failure_is_recorded(task, monkeypatch):
    async def broken(_db, *, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(news_tasks.evaluate, "run_evaluation", broken)
    assert await news_tasks._news_evaluate() == "error"
    assert await _meta(task.db) == {"error": "evaluation failed"}


def test_v9_route_and_beat():
    from tasks.celery_app import celery_app
    assert celery_app.conf.task_routes["news.evaluate"] == {"queue": "news"}
    entry = celery_app.conf.beat_schedule["news-evaluate"]
    assert entry["task"] == "news.evaluate"
    assert entry["schedule"].minute == {40} and entry["schedule"].hour == {3}
    assert entry["options"] == {"expires": 3300}


@pytest.mark.asyncio
async def test_bad_price_is_no_data_and_the_run_continues(db, monkeypatch):
    await _claim(db)
    await _claim(db, "ZQXPB")
    bars = Bars({"ZQXPA": {ANCHOR: (0.0, 1.0), END: (103.0, 105.0)}, "ZQXPB": FULL,
                 "SPY": FLAT})
    run = await _run(db, monkeypatch, last=qcal.advance(END, 5, unit="sessions"), bars=bars)
    assert (run.out["no_data"], run.out["scored"]) == (1, 1)
    reasons = sorted((o.status, o.reason) for o in await _outcomes(db))
    assert reasons == [("no_data", "bad price"), ("scored", None)]


@pytest.mark.asyncio
async def test_one_failing_row_is_skipped(db, monkeypatch):
    await _claim(db)
    await _claim(db, "ZQXPB")
    real = evaluate.outcomes.evaluate_price

    def flaky(**kw):
        if kw["prices"] is not None and kw["prices"].get(ANCHOR) == (99.0, 99.0):
            raise ZeroDivisionError("bad row")
        return real(**kw)

    monkeypatch.setattr(evaluate.outcomes, "evaluate_price", flaky)
    bars = Bars({"ZQXPA": {**FULL, ANCHOR: (99.0, 99.0)}, "ZQXPB": FULL, "SPY": FLAT})
    run = await _run(db, monkeypatch, bars=bars)
    assert (run.out["price_due"], run.out["scored"], run.out["no_data"]) == (2, 1, 0)
    [o] = await _outcomes(db)
    assert o.status == "scored"


@pytest.mark.asyncio
async def test_x09_backfill_claim_is_scored_but_not_counted(db, monkeypatch):
    await _claim(db, origin="backfill")
    run = await _run(db, monkeypatch)
    [o] = await _outcomes(db)
    assert (o.status, o.hit, o.scoring_eligible) == ("scored", True, False)
    src = (await db.execute(select(NewsSource).where(NewsSource.id == db.info["source"].id)
                            .execution_options(populate_existing=True))).scalar_one()
    assert (src.n_evaluated, src.hits, run.out["global_n"], run.out["sources_scored"]) == (
        0, 0, 0, 0)
