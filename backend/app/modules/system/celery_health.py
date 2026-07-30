"""Celery task health monitoring and status tracking."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from .models import TaskStatus

logger = logging.getLogger(__name__)


def _get_celery_app():
    """Import lazily to avoid circular imports while Celery loads task modules."""
    from tasks.celery_app import celery_app

    return celery_app


class CeleryHealthCheck:
    """Monitor and check health of Celery tasks."""

    # Define expected tasks and their schedules
    EXPECTED_TASKS = {
        "collect_system_metrics": {
            "description": "Collect system metrics periodically",
            "schedule_interval": str(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS),
            "schedule_type": "periodic",
        },
        "sync_task_statuses": {
            "description": "Sync Celery task status records",
            "schedule_interval": str(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS),
            "schedule_type": "periodic",
        },
        "cleanup_old_logs": {
            "description": "Cleanup old system logs daily",
            "schedule_interval": "86400",
            "schedule_type": "periodic",
        },
        "cleanup_old_metrics": {
            "description": "Cleanup old system metrics daily",
            "schedule_interval": "86400",
            "schedule_type": "periodic",
        },
        # v3 quant pipeline (R1-2 three-tier schedule) — review #23: the health
        # view must cover the tasks that actually run this platform
        "quant.heartbeat": {
            "description": "Quant worker liveness beat",
            "schedule_interval": "60",
            "schedule_type": "periodic",
        },
        "quant.position_cycle": {
            "description": "Intraday stop checks (RTH, 5 min)",
            "schedule_interval": "300",
            "schedule_type": "periodic",
        },
        "quant.entry_cycle": {
            "description": "Post-open entry booking (both DST slots)",
            "schedule_interval": "86400",
            "schedule_type": "periodic",
        },
        "quant.signal_cycle": {
            "description": "Post-close recommendations + exits + snapshot",
            "schedule_interval": "86400",
            "schedule_type": "periodic",
        },
        "quant.telegram_poll": {
            "description": "Telegram command loop",
            "schedule_interval": "60",
            "schedule_type": "periodic",
        },
    }

    @staticmethod
    def get_celery_worker_status() -> Dict[str, any]:
        """Get Celery worker connection status."""
        try:
            # Try to ping the worker
            ping_result = _get_celery_app().control.inspect(timeout=1.0).ping()

            if ping_result:
                workers = list(ping_result.keys())
                return {
                    "is_online": True,
                    "workers": workers,
                    "worker_count": len(workers),
                    "message": f"Connected to {len(workers)} worker(s)",
                }
            else:
                return {
                    "is_online": False,
                    "workers": [],
                    "worker_count": 0,
                    "message": "No workers connected",
                }
        except Exception as e:
            logger.error(f"Error checking Celery worker status: {e}")
            return {
                "is_online": False,
                "workers": [],
                "worker_count": 0,
                "message": f"Error: {str(e)}",
            }

    @staticmethod
    def get_celery_beat_status() -> Dict[str, any]:
        """Get Celery Beat scheduler status."""
        try:
            beat_schedule = _get_celery_app().conf.beat_schedule or {}

            if beat_schedule:
                return {
                    "is_running": True,
                    "total_scheduled_tasks": len(beat_schedule),
                    "message": f"Celery Beat has {len(beat_schedule)} configured scheduled task(s)",
                }
            else:
                return {
                    "is_running": False,
                    "total_scheduled_tasks": 0,
                    "message": "No Celery Beat schedule configured",
                }
        except Exception as e:
            logger.error(f"Error checking Celery Beat status: {e}")
            return {
                "is_running": False,
                "total_scheduled_tasks": 0,
                "message": f"Error: {str(e)}",
            }

    @staticmethod
    def get_active_tasks() -> List[Dict[str, any]]:
        """Get list of currently active Celery tasks."""
        try:
            inspect = _get_celery_app().control.inspect(timeout=1.0)
            active = inspect.active()

            if not active:
                return []

            task_list = []
            for worker, tasks in active.items():
                for task in tasks:
                    task_list.append({
                        "id": task["id"],
                        "name": task["name"],
                        "worker": worker,
                        "args": task.get("args"),
                        "kwargs": task.get("kwargs"),
                        "time_start": task.get("time_start"),
                    })

            return task_list
        except Exception as e:
            logger.error(f"Error getting active tasks: {e}")
            return []

    @staticmethod
    def get_scheduled_tasks() -> List[Dict[str, any]]:
        """Get list of Celery Beat scheduled tasks."""
        try:
            inspect = _get_celery_app().control.inspect(timeout=1.0)
            scheduled = inspect.scheduled()

            if not scheduled:
                return []

            task_list = []
            for worker, tasks in scheduled.items():
                for task in tasks:
                    task_list.append({
                        "name": task["request"]["name"],
                        "worker": worker,
                        "eta": task.get("eta"),
                        "priority": task.get("priority"),
                    })

            return task_list
        except Exception as e:
            logger.error(f"Error getting scheduled tasks: {e}")
            return []

    @staticmethod
    def get_task_stats() -> Dict[str, any]:
        """Get Celery task statistics."""
        try:
            inspect = _get_celery_app().control.inspect(timeout=1.0)
            stats = inspect.stats()

            if not stats:
                return {}

            # Aggregate stats from all workers
            aggregated = {
                "pool": {},
                "total_tasks": 0,
            }

            for worker, worker_stats in stats.items():
                # Pool information
                pool = worker_stats.get("pool", {})
                aggregated["pool"][worker] = {
                    "max_concurrency": pool.get("max-concurrency"),
                    "running": pool.get("running"),
                }

            return aggregated
        except Exception as e:
            logger.error(f"Error getting task stats: {e}")
            return {}

    @staticmethod
    async def update_task_status(
        db: AsyncSession,
        task_name: str,
        status: str,
        is_healthy: bool = True,
        last_error: Optional[str] = None,
    ) -> TaskStatus:
        """Update task status in database."""
        stmt = select(TaskStatus).where(TaskStatus.task_name == task_name)
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()

        if task is None:
            # Create new task status record
            task = TaskStatus(
                task_name=task_name,
                description=CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                    "description"
                ),
                status=status,
                is_healthy=is_healthy,
                schedule_interval=CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                    "schedule_interval"
                ),
                schedule_type=CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                    "schedule_type"
                ),
            )
            db.add(task)
        else:
            # Update existing record
            task.status = status
            task.is_healthy = is_healthy
            task.updated_at = datetime.now(timezone.utc)
            task.description = CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                "description"
            )
            task.schedule_interval = CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                "schedule_interval"
            )
            task.schedule_type = CeleryHealthCheck.EXPECTED_TASKS.get(task_name, {}).get(
                "schedule_type"
            )

            if last_error:
                task.last_error_message = last_error
                task.last_error_time = datetime.now(timezone.utc)

        await db.flush()
        return task

    @staticmethod
    async def sync_task_statuses(db: AsyncSession) -> Dict[str, str]:
        """
        Sync all expected task statuses from Celery.
        Returns: {task_name: "online"|"offline"|"failed"}
        """
        worker_status = CeleryHealthCheck.get_celery_worker_status()
        beat_status = CeleryHealthCheck.get_celery_beat_status()

        # review #23: purge status rows for tasks that no longer exist —
        # zombie rows from retired pipelines pinned /system/health at critical
        from sqlalchemy import delete as sa_delete
        from app.modules.system.models import TaskStatus as _TS
        await db.execute(sa_delete(_TS).where(
            _TS.task_name.notin_(list(CeleryHealthCheck.EXPECTED_TASKS))))

        task_statuses = {}

        for task_name, task_info in CeleryHealthCheck.EXPECTED_TASKS.items():
            if not worker_status["is_online"]:
                task_status = "offline"
                is_healthy = False
            elif not beat_status["is_running"]:
                task_status = "offline"
                is_healthy = False
            else:
                beat_schedule = _get_celery_app().conf.beat_schedule or {}
                configured_tasks = {
                    entry.get("task")
                    for entry in beat_schedule.values()
                    if isinstance(entry, dict)
                }
                task_scheduled = task_name in configured_tasks or any(
                    configured_task and configured_task.endswith(task_name)
                    for configured_task in configured_tasks
                )

                if task_scheduled:
                    task_status = "online"
                    is_healthy = True
                else:
                    task_status = "offline"
                    is_healthy = False

            task_statuses[task_name] = task_status

            # Update database
            await CeleryHealthCheck.update_task_status(
                db=db,
                task_name=task_name,
                status=task_status,
                is_healthy=is_healthy,
            )

        return task_statuses

    @staticmethod
    def get_all_status() -> dict:
        """Get comprehensive Celery health status."""
        return {
            "worker": CeleryHealthCheck.get_celery_worker_status(),
            "beat": CeleryHealthCheck.get_celery_beat_status(),
            "active_tasks": CeleryHealthCheck.get_active_tasks(),
            "scheduled_tasks": CeleryHealthCheck.get_scheduled_tasks(),
            "stats": CeleryHealthCheck.get_task_stats(),
        }
