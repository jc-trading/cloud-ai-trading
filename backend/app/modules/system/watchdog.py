"""Pipeline watchdog — detects a dead/wedged Celery worker and alerts.

Born from the 2026-07-01 incident: both worker children froze mid-task, the
main process still answered `celery inspect ping`, Beat kept publishing, and
for 3 days every surface looked green (containers Up, /api/health 200) while
10.5k tasks piled up and zero decisions were written. `ping` proves the main
process is alive, not that work is being consumed — so this watchdog runs in
the BACKEND process (which survives a wedged worker) and checks the two
signals that cannot lie:

  1. Broker queue depth — Beat publishes ~145 tasks/hour, so a stalled worker
     pushes the `celery` list past QUEUE_DEPTH_ALERT within ~1.5h.
  2. Decision freshness — crypto analysis writes an ai_analysis_results row
     every cycle for every watched symbol, 24/7. If watchlist items exist but
     no row has appeared for DECISION_STALE_SECONDS, the pipeline is dead even
     if the queue looks empty (e.g. tasks consumed but all failing).

Alerts go to Telegram (already wired for signals/orders) with a per-check
cooldown, and are always logged at ERROR for the container logs.
"""

import asyncio
import logging
import time

from sqlalchemy import func, select

from app.config import settings
from app.modules.notifications import TelegramNotifier

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300  # every 5 minutes
QUEUE_DEPTH_ALERT = 200  # ~1.5h of Beat publishing with nothing consuming
DECISION_STALE_SECONDS = 2 * 3600  # crypto decisions normally land every 3 min
ALERT_COOLDOWN_SECONDS = 6 * 3600  # per-check re-alert throttle

_last_alert_at: dict[str, float] = {}


async def _alert(check: str, message: str) -> None:
    """Log the problem and Telegram it, at most once per cooldown per check."""
    logger.error("WATCHDOG %s: %s", check, message)
    now = time.monotonic()
    last = _last_alert_at.get(check)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert_at[check] = now
    try:
        await TelegramNotifier().send_message(f"🚨 *Pipeline watchdog — {check}*\n{message}")
    except Exception as e:  # alerting must never take the watchdog down
        logger.error("Watchdog failed to send Telegram alert: %s", e)


async def _check_queue_depth() -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.CELERY_BROKER_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        depth = await client.llen("celery")
    finally:
        await client.aclose()

    if depth > QUEUE_DEPTH_ALERT:
        await _alert(
            "queue backlog",
            f"{depth} tasks queued (alert threshold {QUEUE_DEPTH_ALERT}). "
            f"The Celery worker is likely wedged or down — check "
            f"`docker logs cat_celery_worker` and restart it.",
        )
    else:
        logger.debug("Watchdog queue depth OK: %s", depth)


async def _check_decision_freshness() -> None:
    from app.database import AsyncSessionLocal
    from app.modules.analysis.models import AIAnalysisResult
    from app.modules.watchlist.models import WatchlistItem

    async with AsyncSessionLocal() as db:
        watched = (
            await db.execute(select(func.count()).select_from(WatchlistItem))
        ).scalar() or 0
        if not watched:
            return  # nothing watched → no decisions is the expected state

        latest = (
            await db.execute(select(func.max(AIAnalysisResult.created_at)))
        ).scalar()

    if latest is None:
        return  # fresh install, nothing written yet — queue check covers a stall

    now = time.time()
    age = now - latest.timestamp()
    if age > DECISION_STALE_SECONDS:
        await _alert(
            "stale decisions",
            f"No new decision for {age / 3600:.1f}h ({watched} watched symbols; "
            f"latest row {latest:%Y-%m-%d %H:%M} UTC). The analysis pipeline "
            f"has stopped producing — check worker/beat logs.",
        )


async def run_watchdog() -> None:
    """Background loop; started from the FastAPI lifespan, cancelled on shutdown."""
    logger.info(
        "Pipeline watchdog started (every %ss: queue depth > %s, decisions stale > %ss)",
        CHECK_INTERVAL_SECONDS,
        QUEUE_DEPTH_ALERT,
        DECISION_STALE_SECONDS,
    )
    while True:
        try:
            await _check_queue_depth()
        except Exception as e:
            logger.warning("Watchdog queue check failed: %s", e)
        try:
            await _check_decision_freshness()
        except Exception as e:
            logger.warning("Watchdog freshness check failed: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
