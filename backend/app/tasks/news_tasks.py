"""news.cycle — every 15 min on the bulk worker (queue ``news``): poll Alpaca
news from the stored cursor and ingest it. No LLM in P6a.

The cursor is ``max(published_at)`` of stored Alpaca rows (not heartbeat meta,
which is overwritten every tick), re-read with an overlap; the fetch is
oldest-first so a truncated tick resumes where it stopped. Never raises.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import func, select, text

from app.celery_database import CeleryAsyncSessionLocal, celery_engine
from app.config import settings
from app.modules.news import alpaca_news, evaluate, extract, notify as notify_mod
from app.modules.news.ingest import COUNT_KEYS, ingest_alpaca
from app.modules.news.models import NewsItem, NewsItemSource
from app.modules.news.relevance import relevance_map
from app.tasks.quant_tasks import _beat, _run_async

logger = logging.getLogger(__name__)

NEWS_LOCK_KEY = 7310601


async def _news_cycle() -> str:
    try:
        now = datetime.now(timezone.utc)
        if not settings.NEWS_ENABLED:
            async with CeleryAsyncSessionLocal() as db:
                await _beat(db, "news_cycle", disabled=True)
                await db.commit()
            return "disabled"

        # why a dedicated connection + xact lock: the Celery engine is NullPool,
        # so every commit on the work session closes its connection and would
        # drop a session-level advisory lock with it.
        async with celery_engine.connect() as lock_conn:
            got = (await lock_conn.execute(text("select pg_try_advisory_xact_lock(:k)"),
                                           {"k": NEWS_LOCK_KEY})).scalar()
            if not got:
                return "busy"
            async with CeleryAsyncSessionLocal() as db:
                last = (await db.execute(
                    select(func.max(NewsItemSource.published_at))
                    .where(NewsItemSource.provider == "alpaca")
                )).scalar()
                if last is None:
                    start = now - timedelta(hours=settings.NEWS_BACKFILL_HOURS)
                else:
                    start = last - timedelta(minutes=settings.NEWS_POLL_OVERLAP_MINUTES)
                bootstrap_at = (await db.execute(
                    select(func.min(NewsItem.discovered_at)))).scalar() or now

                items, truncated, fetch_error = await asyncio.to_thread(
                    alpaca_news.fetch, start, now,
                    max_pages=settings.NEWS_MAX_PAGES_PER_TICK,
                    timeout=settings.NEWS_HTTP_TIMEOUT_SECONDS)
                if items:
                    relevance = await relevance_map(db)
                    counts = await ingest_alpaca(db, items, bootstrap_at=bootstrap_at,
                                                 now=now, relevance=relevance)
                    await db.commit()
                else:
                    counts = dict.fromkeys(COUNT_KEYS, 0)

                try:
                    ext = await extract.run_extraction(db, now=now)
                except Exception:
                    logger.exception("news extraction failed")
                    await db.rollback()
                    ext = {"llm": "error"}
                try:
                    sent = await notify_mod.maybe_notify(db, now=datetime.now(timezone.utc))
                except Exception:
                    logger.exception("news notify failed")
                    await db.rollback()
                    sent = 0

                await _beat(db, "news_cycle", **counts, truncated=truncated,
                            fetch_error=fetch_error, bootstrap_at=bootstrap_at.isoformat(),
                            extract=ext, notified=sent)
                await db.commit()
        return "ok"
    except Exception:
        logger.exception("news.cycle failed")
        return "error"


@shared_task(name="news.cycle", soft_time_limit=600, time_limit=660)
def news_cycle():
    return _run_async(_news_cycle())


async def _news_evaluate() -> str:
    try:
        now = datetime.now(timezone.utc)
        if not settings.NEWS_ENABLED:
            async with CeleryAsyncSessionLocal() as db:
                await _beat(db, "news_evaluate", disabled=True)
                await db.commit()
            return "disabled"

        async with celery_engine.connect() as lock_conn:
            got = (await lock_conn.execute(text("select pg_try_advisory_xact_lock(:k)"),
                                           {"k": NEWS_LOCK_KEY})).scalar()
            if not got:
                return "busy"
            async with CeleryAsyncSessionLocal() as db:
                try:
                    counts = await evaluate.run_evaluation(db, now=now)
                except Exception:
                    logger.exception("news evaluation failed")
                    await db.rollback()
                    counts = {"error": "evaluation failed"}
                await _beat(db, "news_evaluate", **counts)
                await db.commit()
        return "error" if "error" in counts else "ok"
    except Exception:
        logger.exception("news.evaluate failed")
        return "error"


@shared_task(name="news.evaluate", soft_time_limit=1200, time_limit=1260)
def news_evaluate():
    return _run_async(_news_evaluate())
