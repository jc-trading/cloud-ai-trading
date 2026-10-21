"""High-probability news alerts (P6b): freshly extracted claims on held or
shortlisted names, capped per UTC day. A row is stamped only after the message
went out, so a failed send is retried on the next tick."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import settings
from app.modules.news.models import NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
from app.tasks.quant_tasks import _notify

EVENT = "news_high_probability"
_FRESH = timedelta(hours=2)


async def maybe_notify(db, *, now: datetime) -> int:
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (await db.execute(
        select(func.count()).select_from(NewsItemSymbol)
        .where(NewsItemSymbol.notified_at >= day_start)
    )).scalar()
    room = settings.NEWS_NOTIFY_DAILY_MAX - sent_today
    if room <= 0:
        return 0

    candidates = (await db.execute(
        select(NewsItemSymbol, NewsItem)
        .join(NewsItem, NewsItem.id == NewsItemSymbol.news_id)
        .where(NewsItemSymbol.notified_at.is_(None),
               NewsItemSymbol.probability >= settings.NEWS_NOTIFY_MIN_PROB,
               NewsItemSymbol.relevance.in_(("held", "shortlist")),
               NewsItemSymbol.suggested_action.in_(("buy", "sell", "review")),
               NewsItem.origin == "live",
               NewsItem.extracted_at >= now - _FRESH)
        .order_by(NewsItemSymbol.probability.desc())
        .limit(room)
    )).all()

    sent = 0
    for row, item in candidates:
        src = (await db.execute(
            select(NewsSource.name, NewsSource.lift, NewsSource.n_evaluated, NewsItemSource.url)
            .join(NewsSource, NewsSource.id == NewsItemSource.source_id)
            .where(NewsItemSource.news_id == item.id)
            .order_by(NewsItemSource.fetched_at, NewsItemSource.id)
            .limit(1)
        )).first()
        if src is None:
            continue
        lift = f"{src.lift:.2f}" if src.lift is not None else "unverified"
        text = (f"📰 {row.symbol} {row.direction} ≥{float(row.expected_move_pct):g}% in "
                f"{row.horizon_days}d · p={row.probability} · {row.suggested_action} · "
                f"src {src.name} {lift} (n={src.n_evaluated})\n"
                f"{item.headline[:160]}\n{src.url or ''}")
        severity = "warning" if row.relevance == "held" and row.direction == "down" else "info"
        if await _notify(text, event=EVENT, severity=severity):
            row.notified_at = now
            sent += 1
    await db.commit()
    return sent
