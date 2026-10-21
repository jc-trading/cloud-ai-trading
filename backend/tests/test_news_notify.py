"""P6b high-probability news alerts against real PostgreSQL (rolled back):
thresholds, freshness, the daily cap, and stamping only after a sent message.
``_notify`` is replaced by a recorder; nothing is ever sent."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.news import notify
from app.modules.news.models import NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
from tests._pg_rollback import pg_session_rollback  # noqa: F401


class Recorder:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def __call__(self, message, *, event, severity):
        self.calls.append({"text": message, "event": event, "severity": severity})
        return self.ok


@pytest_asyncio.fixture
async def db(pg_session_rollback, monkeypatch):
    s = pg_session_rollback
    await s.execute(text("UPDATE news_items SET extracted_at = extracted_at - interval '3 days' "
                         "WHERE extracted_at IS NOT NULL"))
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")
    for key, value in (("NEWS_NOTIFY_MIN_PROB", 80), ("NEWS_NOTIFY_DAILY_MAX", 10)):
        monkeypatch.setattr(settings, key, value)
    source = NewsSource(id=uuid4(), kind="feed", name=f"ZZTEST feed {uuid4().hex[:8]}",
                        lift=Decimal("1.3"), n_evaluated=25)
    s.add(source)
    await s.flush()
    s.info["source"] = source
    return s


def _install(monkeypatch, ok=True) -> Recorder:
    rec = Recorder(ok)
    monkeypatch.setattr(notify, "_notify", rec)
    return rec


async def _claim(db, *, probability=85, relevance="shortlist", action="review", direction="up",
                 origin="live", extracted_ago=timedelta(minutes=10), with_source=True):
    now = datetime.now(timezone.utc)
    item = NewsItem(id=uuid4(), origin=origin, headline=f"ZZTEST {uuid4().hex}",
                    headline_norm=f"zztest {uuid4().hex}", published_at=now - timedelta(hours=1),
                    status="extracted", extracted_at=now - extracted_ago)
    db.add(item)
    await db.flush()
    if with_source:
        db.add(NewsItemSource(id=uuid4(), news_id=item.id, source_id=db.info["source"].id,
                              provider="alpaca", external_id=f"zztest-{uuid4().hex}",
                              url="https://example.com/n/1", published_at=item.published_at))
    row = NewsItemSymbol(id=uuid4(), news_id=item.id, symbol="ZQXA", relevance=relevance,
                         claim_type="price", direction=direction, probability=probability,
                         expected_move_pct=Decimal("2.50"), horizon_days=5,
                         anchor_session=now.date(), evaluate_after=now.date(),
                         suggested_action=action,
                         review_at=now if action == "review" else None,
                         review_status="open" if action == "review" else "none")
    db.add(row)
    await db.flush()
    return item, row


async def _notified_today(db) -> int:
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (await db.execute(select(func.count()).select_from(NewsItemSymbol)
                             .where(NewsItemSymbol.notified_at >= day))).scalar()


async def _run(db):
    return await notify.maybe_notify(db, now=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_n1_qualifying_claim_is_sent_and_stamped(db, monkeypatch):
    item, row = await _claim(db)
    rec = _install(monkeypatch)
    assert await _run(db) == 1
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert (call["event"], call["severity"]) == ("news_high_probability", "info")
    src = db.info["source"].name
    assert call["text"] == (f"📰 ZQXA up ≥2.5% in 5d · p=85 · review · src {src} 1.30 (n=25)\n"
                            f"{item.headline[:160]}\nhttps://example.com/n/1")
    await db.refresh(row)
    assert row.notified_at is not None


@pytest.mark.asyncio
async def test_n2_held_and_down_is_a_warning(db, monkeypatch):
    await _claim(db, relevance="held", direction="down")
    rec = _install(monkeypatch)
    assert await _run(db) == 1 and rec.calls[0]["severity"] == "warning"


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [
    {"probability": 79},
    {"relevance": "pool"},
    {"origin": "backfill"},
    {"extracted_ago": timedelta(hours=3)},
    {"action": "ignore", "probability": 95},
], ids=["n3_low_probability", "n4_pool", "n5_backfill", "n6_stale", "n9_ignore"])
async def test_not_sent(db, monkeypatch, over):
    await _claim(db, **over)
    rec = _install(monkeypatch)
    assert await _run(db) == 0 and rec.calls == []


@pytest.mark.asyncio
async def test_n7_failed_send_is_not_stamped_and_retried(db, monkeypatch):
    _, row = await _claim(db)
    rec = _install(monkeypatch, ok=False)
    assert await _run(db) == 0
    await db.refresh(row)
    assert row.notified_at is None
    assert await _run(db) == 0
    assert len(rec.calls) == 2


@pytest.mark.asyncio
async def test_n8_daily_cap(db, monkeypatch):
    monkeypatch.setattr(settings, "NEWS_NOTIFY_DAILY_MAX", await _notified_today(db) + 2)
    for p in (81, 90, 85):
        await _claim(db, probability=p)
    rec = _install(monkeypatch)
    assert await _run(db) == 2
    assert [c["text"].split(" · ")[1] for c in rec.calls] == ["p=90", "p=85"]
    assert await _run(db) == 0 and len(rec.calls) == 2


@pytest.mark.asyncio
async def test_item_without_source_is_skipped_not_raised(db, monkeypatch):
    _, orphan = await _claim(db, probability=95, with_source=False)
    _, row = await _claim(db, probability=85)
    rec = _install(monkeypatch)
    assert await _run(db) == 1 and len(rec.calls) == 1
    await db.refresh(orphan)
    await db.refresh(row)
    assert orphan.notified_at is None and row.notified_at is not None
