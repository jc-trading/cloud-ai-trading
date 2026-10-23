"""P7a source scoring and auto-disable: the Beta/lift formulas (pure), then the
nightly recompute against real PostgreSQL (rolled back) with outcomes seeded
directly. Every news item and score history row is deleted first so the global
prior only sees this test's outcomes; ``_notify`` is a recorder."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from quant.data import calendar as qcal

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.news import evaluate, scoring
from app.modules.news.ingest import platform_source
from app.modules.news.models import (
    NewsItem, NewsItemSource, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory
)
from tests._pg_rollback import pg_session_rollback  # noqa: F401

LAST = date(2026, 10, 16)
NOW = datetime(2026, 10, 17, 3, 40, tzinfo=timezone.utc)
EARLIER = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ANCHOR, END = date(2026, 10, 12), date(2026, 10, 14)
SETTINGS = (("NEWS_PRIOR_STRENGTH", 10.0), ("NEWS_PRIOR_MEAN_DEFAULT", 0.40),
            ("NEWS_PRIOR_MIN_GLOBAL_N", 100), ("NEWS_SOURCE_MIN_N", 20),
            ("NEWS_DISABLE_BELOW_LIFT", 0.75), ("NEWS_DISABLE_CONSECUTIVE_DAYS", 3),
            ("NEWS_HIT_MIN_EXCESS_PCT", 1.0), ("NEWS_NODATA_GRACE_SESSIONS", 5))


class Recorder:
    def __init__(self, db=None):
        self.db = db
        self.calls = []

    async def __call__(self, message, *, event, severity):
        call = {"text": message, "event": event, "severity": severity}
        if self.db is not None:
            call["in_transaction"] = self.db.in_transaction()
            call["enabled"] = dict((await self.db.execute(
                select(NewsSource.name, NewsSource.enabled))).all())
        self.calls.append(call)
        return True


def test_s1_posterior_and_lift():
    post = scoring.posterior(10, 20, 0.4, 10)
    assert round(post, 5) == 0.46667 and round(scoring.lift(post, 0.4), 5) == 1.16667


def test_s2_prior_mean(monkeypatch):
    for key, value in SETTINGS:
        monkeypatch.setattr(settings, key, value)
    assert scoring.prior_mean(30, 99) == 0.40
    assert scoring.prior_mean(60, 100) == 0.6
    assert scoring.prior_mean(0, 100) == 0.01


def test_s3_brier():
    assert scoring.brier([]) is None
    assert scoring.brier([(80, True), (40, False)]) == pytest.approx((0.04 + 0.16) / 2)


@pytest.mark.parametrize("over, expected", [
    ({}, True),
    ({"enabled": False}, False),
    ({"kind": "platform"}, False),
    ({"n": 19}, False),
    ({"auto_disable_after_n": 40}, False),
    ({"lifts_desc": [0.5, 0.5]}, False),
    ({"lifts_desc": [0.5, 0.9, 0.5]}, False),
    ({"lifts_desc": [0.5, 0.5, 0.5, 2.0]}, True),
], ids=["all_met", "already_disabled", "platform", "below_min_n", "own_after_n",
        "two_sessions", "one_high_session", "older_high_ignored"])
def test_s4_should_auto_disable(monkeypatch, over, expected):
    for key, value in SETTINGS:
        monkeypatch.setattr(settings, key, value)
    args = {"enabled": True, "kind": "author", "n": 20, "auto_disable_after_n": 20,
            "lifts_desc": [0.5, 0.5, 0.5], **over}
    assert scoring.should_auto_disable(**args) is expected


@pytest_asyncio.fixture
async def db(pg_session_rollback, monkeypatch):
    s = pg_session_rollback
    for key, value in SETTINGS:
        monkeypatch.setattr(settings, key, value)
    await s.execute(delete(NewsItem))
    await s.execute(delete(NewsSourceScoreHistory))
    s.info["platform"] = await platform_source(s)
    return s


async def _author(db, *, enabled=True, after_n=20) -> NewsSource:
    src = NewsSource(id=uuid4(), kind="author", name=f"benzinga/ZQXP7 {uuid4().hex[:8]}",
                     parent_id=db.info["platform"].id, auto_disable_after_n=after_n,
                     enabled=enabled, disabled_at=None if enabled else NOW,
                     disabled_by=None if enabled else "manual")
    db.add(src)
    await db.flush()
    return src


async def _seed(db, hits: list[bool], *sources: NewsSource, origin="live", probability=60):
    items = [NewsItem(id=uuid4(), origin=origin, headline=f"ZQXP7 {uuid4().hex}",
                      headline_norm=f"zqxp7 {uuid4().hex}", published_at=NOW)
             for _ in hits]
    db.add_all(items)
    await db.flush()
    symbols = []
    for item in items:
        db.add_all(NewsItemSource(id=uuid4(), news_id=item.id, source_id=src.id,
                                  provider="alpaca", external_id=f"zqxp7-{uuid4().hex}",
                                  published_at=NOW) for src in sources)
        symbols.append(NewsItemSymbol(id=uuid4(), news_id=item.id, symbol="ZQXP",
                                      claim_type="price", direction="up",
                                      probability=probability, expected_move_pct=4,
                                      horizon_days=3, anchor_session=ANCHOR, evaluate_after=END,
                                      suggested_action="ignore"))
    db.add_all(symbols)
    await db.flush()
    db.add_all(NewsOutcome(id=uuid4(), news_symbol_id=sym.id, status="scored", method="price",
                           scoring_eligible=(origin == "live"), anchor_session=ANCHOR,
                           end_session=END, anchor_price=100, end_price=105,
                           realized_move_pct=5, spy_move_pct=1, excess_move_pct=4,
                           magnitude_error_pct=0, hit=hit, score=1 if hit else 0)
               for sym, hit in zip(symbols, hits))
    await db.flush()


async def _history(db, src: NewsSource, *lifts: float):
    for back, lf in enumerate(lifts, 1):
        db.add(NewsSourceScoreHistory(id=uuid4(), source_id=src.id,
                                      as_of=qcal.advance(LAST, -back, unit="sessions"),
                                      n=20, hits=0, score=0.13, lift=lf, prior_mean=0.4))
    await db.flush()


def _no_bars(symbol, timeframe, end=None):
    df = pd.DataFrame({"ts": [], "open": [], "close": []})
    df.attrs["unadjusted"] = False
    return df


async def _no_relevance(_db):
    return {}


async def _run(db, monkeypatch, recorder=None, *, last=LAST) -> SimpleNamespace:
    rec = recorder or Recorder()
    monkeypatch.setattr(evaluate, "_notify", rec)
    out = await evaluate.run_evaluation(db, now=NOW, latest_complete=lambda: last,
                                        bars_fn=_no_bars, sectors_fn=dict,
                                        relevance_fn=_no_relevance)
    return SimpleNamespace(out=out, rec=rec)


async def _src(db, src: NewsSource) -> NewsSource:
    return (await db.execute(select(NewsSource).where(NewsSource.id == src.id)
                             .execution_options(populate_existing=True))).scalar_one()


async def _hist(db, src: NewsSource) -> list[tuple]:
    return (await db.execute(
        select(NewsSourceScoreHistory.as_of, NewsSourceScoreHistory.n,
               NewsSourceScoreHistory.hits, NewsSourceScoreHistory.score,
               NewsSourceScoreHistory.lift, NewsSourceScoreHistory.prior_mean,
               NewsSourceScoreHistory.brier)
        .where(NewsSourceScoreHistory.source_id == src.id)
        .order_by(NewsSourceScoreHistory.as_of))).all()


@pytest.mark.asyncio
async def test_s5_backfill_outcomes_do_not_count(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [True, False, False], src)
    first = await _run(db, monkeypatch)
    before = await _src(db, src)
    snapshot = (before.n_evaluated, before.hits, before.score, before.lift)
    assert snapshot[:2] == (3, 1)

    await _seed(db, [True, True], src, origin="backfill")
    second = await _run(db, monkeypatch)
    after = await _src(db, src)
    assert (after.n_evaluated, after.hits, after.score, after.lift) == snapshot
    assert second.out["global_n"] == first.out["global_n"] == 3


@pytest.mark.asyncio
async def test_s6_nineteen_misses_do_not_disable(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 19, src)
    await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    row = await _src(db, src)
    assert row.enabled is True and row.n_evaluated == 19 and float(row.lift) < 0.75
    assert run.out["auto_disabled"] == [] and run.rec.calls == []


@pytest.mark.asyncio
async def test_s7_twenty_misses_and_three_low_sessions_disable(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    row = await _src(db, src)
    reason = "lift 0.33 < 0.75 for 3 sessions (n=20)"
    assert (row.enabled, row.disabled_by, row.disabled_at, row.disabled_reason) == (
        False, "auto", NOW, reason)
    assert run.out["auto_disabled"] == [src.name]
    assert len(run.rec.calls) == 1
    call = run.rec.calls[0]
    assert (call["event"], call["severity"]) == ("source_disabled", "warning")
    assert call["text"] == (f"🔇 news sources auto-disabled (1): {src.name} — {reason}. "
                            "Shadow scoring continues; re-enable on News Sources.")


@pytest.mark.asyncio
async def test_s8_two_sessions_or_a_high_one_do_not_disable(db, monkeypatch):
    short = await _author(db)
    gap = await _author(db)
    await _seed(db, [False] * 20, short)
    await _seed(db, [False] * 20, gap)
    await _history(db, short, 0.3)
    await _history(db, gap, 1.5, 0.3)
    run = await _run(db, monkeypatch)
    assert (await _src(db, short)).enabled is True
    assert (await _src(db, gap)).enabled is True
    assert run.rec.calls == []


@pytest.mark.asyncio
async def test_s9_platform_aggregates_and_is_never_disabled(db, monkeypatch):
    platform = db.info["platform"]
    a, b = await _author(db), await _author(db)
    await _seed(db, [False] * 20, a)
    await _seed(db, [False] * 20, b)
    await _seed(db, [False] * 5, a, b)
    await _history(db, platform, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    row = await _src(db, platform)
    assert row.enabled is True and row.n_evaluated == 45 and float(row.lift) < 0.75
    assert (await _src(db, a)).n_evaluated == 25
    assert platform.name not in run.out["auto_disabled"]


@pytest.mark.asyncio
async def test_s10_own_after_n_raises_the_bar(db, monkeypatch):
    src = await _author(db, after_n=40)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    assert (await _src(db, src)).enabled is True and run.rec.calls == []


@pytest.mark.asyncio
async def test_s11_disabled_sources_are_still_scored(db, monkeypatch):
    src = await _author(db, enabled=False)
    await _seed(db, [True, False], src)
    await _run(db, monkeypatch)
    row = await _src(db, src)
    assert (row.enabled, row.n_evaluated, row.hits) == (False, 2, 1)
    assert row.score is not None and row.score_updated_at == NOW
    assert [h.as_of for h in await _hist(db, src)] == [LAST]


@pytest.mark.asyncio
async def test_s12_one_count_per_author_per_item(db, monkeypatch):
    a, b = await _author(db), await _author(db)
    await _seed(db, [True], a, a)
    await _seed(db, [False], a, b)
    await _run(db, monkeypatch)
    row_a, row_b = await _src(db, a), await _src(db, b)
    assert (row_a.n_evaluated, row_a.hits, row_b.n_evaluated, row_b.hits) == (2, 1, 1, 0)
    assert (await _src(db, db.info["platform"])).n_evaluated == 2


@pytest.mark.asyncio
async def test_s13_rerun_on_the_same_session_is_idempotent(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    rec = Recorder()
    await _run(db, monkeypatch, rec)
    first = await _hist(db, src)
    await _run(db, monkeypatch, rec)
    assert await _hist(db, src) == first and len(first) == 3
    assert len(rec.calls) == 1


@pytest.mark.asyncio
async def test_s14_one_notification_for_several_sources(db, monkeypatch):
    a, b = await _author(db), await _author(db)
    for src in (a, b):
        await _seed(db, [False] * 20, src)
        await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    assert sorted(run.out["auto_disabled"]) == sorted([a.name, b.name])
    assert len(run.rec.calls) == 1
    text = run.rec.calls[0]["text"]
    assert text.startswith("🔇 news sources auto-disabled (2): ")
    assert a.name in text and b.name in text


@pytest.mark.asyncio
async def test_s15_notify_after_the_disable_is_committed(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch, Recorder(db))
    call = run.rec.calls[0]
    assert call["in_transaction"] is False and call["enabled"][src.name] is False


@pytest.mark.asyncio
async def test_s16_zero_global_hits_floor_the_prior(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 100, src)
    run = await _run(db, monkeypatch)
    assert run.out["p0"] == 0.01 and run.out["global_n"] == 100
    row = await _src(db, src)
    post = scoring.posterior(0, 100, 0.01, 10.0)
    assert float(row.score) == round(post, 4) and float(row.lift) == round(post / 0.01, 4)


@pytest.mark.asyncio
async def test_unscored_sources_are_reset(db, monkeypatch):
    src = await _author(db)
    await db.execute(update(NewsSource).where(NewsSource.id == src.id)
                     .values(n_evaluated=7, hits=3, score=0.5, lift=1.4))
    await _run(db, monkeypatch)
    row = await _src(db, src)
    assert (row.n_evaluated, row.hits, row.score, row.lift) == (0, 0, None, None)


@pytest.mark.asyncio
async def test_x12_one_notification_caps_the_names_at_ten(db, monkeypatch):
    sources = [await _author(db) for _ in range(12)]
    for src in sources:
        await _seed(db, [False] * 20, src)
        await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    assert len(run.out["auto_disabled"]) == 12 and len(run.rec.calls) == 1
    text = run.rec.calls[0]["text"]
    assert text.startswith("🔇 news sources auto-disabled (12): ")
    assert sum(src.name in text for src in sources) == 10
    assert text.endswith("; +2 more. Shadow scoring continues; re-enable on News Sources.")


@pytest.mark.asyncio
async def test_x15_disabled_source_is_not_disabled_again(db, monkeypatch):
    src = await _author(db, enabled=False)
    await db.execute(update(NewsSource).where(NewsSource.id == src.id)
                     .values(disabled_at=EARLIER))
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    run = await _run(db, monkeypatch)
    row = await _src(db, src)
    assert (row.enabled, row.disabled_by, row.disabled_at) == (False, "manual", EARLIER)
    assert run.out["auto_disabled"] == [] and run.rec.calls == []


class _DisablesFirst:
    def __init__(self, session, source_id):
        self._session = session
        self._source_id = source_id

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def execute(self, stmt, *args, **kw):
        if "disabled_by" in str(stmt):
            await self._session.execute(
                update(NewsSource).where(NewsSource.id == self._source_id)
                .values(enabled=False, disabled_at=EARLIER, disabled_by="manual",
                        disabled_reason="by hand"))
        return await self._session.execute(stmt, *args, **kw)



@pytest.mark.asyncio
async def test_x15_disable_update_skips_a_source_disabled_meanwhile(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3, 0.3)
    rec = Recorder()
    monkeypatch.setattr(evaluate, "_notify", rec)
    out = await evaluate.run_evaluation(_DisablesFirst(db, src.id), now=NOW,
                                        latest_complete=lambda: LAST, bars_fn=_no_bars,
                                        sectors_fn=dict, relevance_fn=_no_relevance)
    row = await _src(db, src)
    assert (row.enabled, row.disabled_by, row.disabled_at, row.disabled_reason) == (
        False, "manual", EARLIER, "by hand")
    assert out["auto_disabled"] == [] and rec.calls == []


@pytest.mark.asyncio
async def test_x08_history_after_the_session_is_ignored(db, monkeypatch):
    src = await _author(db)
    await _seed(db, [False] * 20, src)
    await _history(db, src, 0.3)
    db.add(NewsSourceScoreHistory(id=uuid4(), source_id=src.id,
                                  as_of=qcal.advance(LAST, 1, unit="sessions"), n=20, hits=0,
                                  score=0.13, lift=0.3, prior_mean=0.4))
    await db.flush()
    run = await _run(db, monkeypatch)
    assert (await _src(db, src)).enabled is True and run.rec.calls == []
