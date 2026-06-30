"""Celery tasks for system monitoring."""

import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_database import CeleryAsyncSessionLocal
from app.config import get_settings
from .celery_app import celery_app
from app.modules.system.service import SystemMonitoringService

logger = logging.getLogger(__name__)

settings = get_settings()


async def get_async_session() -> AsyncSession:
    """Get database session."""
    return CeleryAsyncSessionLocal()


def _run_async(coro):
    """Run async database work from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@celery_app.task(
    name="collect_system_metrics",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def collect_system_metrics(self):
    """Collect system metrics periodically."""
    try:
        async def _collect():
            async with CeleryAsyncSessionLocal() as db:
                return await SystemMonitoringService.collect_all_metrics(db)

        result = _run_async(_collect())

        logger.info(f"System metrics collected: {result.get('timestamp')}")
        return {
            "status": "success",
            "timestamp": str(result.get("timestamp")),
        }
    except Exception as exc:
        logger.error(f"Error collecting system metrics: {exc}")
        # Retry with exponential backoff
        raise self.retry(exc=exc)


@celery_app.task(
    name="sync_task_statuses",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def sync_task_statuses(self):
    """Sync Celery task statuses."""
    try:
        async def _sync():
            async with CeleryAsyncSessionLocal() as db:
                return await SystemMonitoringService.sync_all_task_statuses(db)

        result = _run_async(_sync())

        logger.info(f"Task statuses synced: {result}")
        return {
            "status": "success",
            "synced_tasks": list(result.keys()),
        }
    except Exception as exc:
        logger.error(f"Error syncing task statuses: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="cleanup_old_logs",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_old_logs(self):
    """Clean up old system logs."""
    try:
        async def _cleanup():
            async with CeleryAsyncSessionLocal() as db:
                return await SystemMonitoringService.cleanup_old_logs(
                    db, days=settings.SYSTEM_LOG_RETENTION_DAYS
                )

        count = _run_async(_cleanup())

        logger.info(f"Cleaned up {count} old system logs")
        return {
            "status": "success",
            "deleted_count": count,
        }
    except Exception as exc:
        logger.error(f"Error cleaning up logs: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="cleanup_old_metrics",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_old_metrics(self):
    """Clean up old system metrics."""
    try:
        async def _cleanup():
            async with CeleryAsyncSessionLocal() as db:
                return await SystemMonitoringService.cleanup_old_metrics(
                    db, days=settings.SYSTEM_METRICS_RETENTION_DAYS
                )

        count = _run_async(_cleanup())

        logger.info(f"Cleaned up {count} old system metrics")
        return {
            "status": "success",
            "deleted_count": count,
        }
    except Exception as exc:
        logger.error(f"Error cleaning up metrics: {exc}")
        raise self.retry(exc=exc)
