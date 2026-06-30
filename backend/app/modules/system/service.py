"""Service layer for system monitoring business logic."""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func, delete

from app.config import settings
from app.modules.system.models import SystemLog, SystemMetric, TaskStatus
from app.modules.system.metrics import SystemMetrics
from app.modules.system.docker_stats import DockerStats
from app.modules.system.celery_health import CeleryHealthCheck

logger = logging.getLogger(__name__)


class SystemMonitoringService:
    """Service for managing system monitoring operations."""

    @staticmethod
    async def create_log(
        db: AsyncSession,
        category: str,
        level: str,
        message: str,
        task_name: Optional[str] = None,
        symbol: Optional[str] = None,
        signal_type: Optional[str] = None,
        status: Optional[str] = None,
        duration_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SystemLog:
        """Create a new system log entry."""
        log = SystemLog(
            timestamp=datetime.now(timezone.utc),
            category=category,
            level=level,
            message=message,
            task_name=task_name,
            symbol=symbol,
            signal_type=signal_type,
            status=status,
            duration_ms=duration_ms,
            event_metadata=metadata,
        )
        db.add(log)
        await db.flush()
        return log

    @staticmethod
    async def get_logs(
        db: AsyncSession,
        limit: int = 100,
        offset: int = 0,
        category: Optional[str] = None,
        level: Optional[str] = None,
        task_name: Optional[str] = None,
        minutes: int = 60,
    ) -> tuple[List[SystemLog], int]:
        """Get system logs with optional filtering.

        Args:
            db: Database session
            limit: Maximum number of logs to return
            offset: Number of logs to skip
            category: Filter by category
            level: Filter by level
            task_name: Filter by task name
            minutes: Get logs from the last N minutes

        Returns:
            Tuple of (logs list, total count)
        """
        # Build filter conditions
        filters = [
            SystemLog.timestamp >= datetime.now(timezone.utc) - timedelta(minutes=minutes)
        ]

        if category:
            filters.append(SystemLog.category == category)
        if level:
            filters.append(SystemLog.level == level)
        if task_name:
            filters.append(SystemLog.task_name == task_name)

        # Count total
        count_stmt = select(func.count()).select_from(SystemLog).where(and_(*filters))
        result = await db.execute(count_stmt)
        total = result.scalar_one()

        # Get paginated results
        stmt = (
            select(SystemLog)
            .where(and_(*filters))
            .order_by(desc(SystemLog.timestamp))
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        logs = result.scalars().all()

        return logs, total

    @staticmethod
    async def get_latest_metrics(db: AsyncSession) -> Optional[SystemMetric]:
        """Get the latest system metrics snapshot."""
        stmt = (
            select(SystemMetric)
            .order_by(desc(SystemMetric.timestamp))
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def save_metrics(
        db: AsyncSession,
        cpu_percent: Optional[float] = None,
        memory_percent: Optional[float] = None,
        disk_percent: Optional[float] = None,
        load_avg_1: Optional[Decimal] = None,
        load_avg_5: Optional[Decimal] = None,
        load_avg_15: Optional[Decimal] = None,
        container_metrics: Optional[Dict[str, Any]] = None,
        task_health: Optional[Dict[str, Any]] = None,
    ) -> SystemMetric:
        """Save system metrics snapshot."""
        metric = SystemMetric(
            timestamp=datetime.now(timezone.utc),
            cpu_percent=cpu_percent or Decimal("0"),
            memory_percent=memory_percent or Decimal("0"),
            disk_percent=disk_percent or Decimal("0"),
            load_average_1=load_avg_1,
            load_average_5=load_avg_5,
            load_average_15=load_avg_15,
            container_metrics=container_metrics,
            task_health=task_health,
        )
        db.add(metric)
        await db.flush()
        return metric

    @staticmethod
    async def collect_all_metrics(db: AsyncSession, save: bool = True) -> Dict[str, Any]:
        """Collect all system metrics.

        When save=False, this method is safe for read endpoints because it does
        not insert a new historical metrics row.
        """
        try:
            # Collect system metrics
            system_metrics, docker_stats, celery_status = await asyncio.gather(
                asyncio.to_thread(SystemMetrics.get_all_metrics),
                asyncio.to_thread(DockerStats.get_all_stats),
                asyncio.to_thread(CeleryHealthCheck.get_all_status),
            )

            # Prepare metrics for database
            cpu_percent = system_metrics.get("cpu", {}).get("percent") if system_metrics.get("cpu") else None
            memory_percent = system_metrics.get("memory", {}).get("percent") if system_metrics.get("memory") else None
            disk_percent = system_metrics.get("disk", {}).get("percent") if system_metrics.get("disk") else None

            load_avg = system_metrics.get("cpu", {}).get("load_average", {}) if system_metrics.get("cpu") else {}
            load_avg_1 = load_avg.get("1min") if load_avg else None
            load_avg_5 = load_avg.get("5min") if load_avg else None
            load_avg_15 = load_avg.get("15min") if load_avg else None

            # Convert to Decimal for database
            if cpu_percent:
                cpu_percent = Decimal(str(cpu_percent))
            if memory_percent:
                memory_percent = Decimal(str(memory_percent))
            if disk_percent:
                disk_percent = Decimal(str(disk_percent))
            if load_avg_1:
                load_avg_1 = Decimal(str(load_avg_1))
            if load_avg_5:
                load_avg_5 = Decimal(str(load_avg_5))
            if load_avg_15:
                load_avg_15 = Decimal(str(load_avg_15))

            metric = None
            if save:
                metric = await SystemMonitoringService.save_metrics(
                    db,
                    cpu_percent=cpu_percent,
                    memory_percent=memory_percent,
                    disk_percent=disk_percent,
                    load_avg_1=load_avg_1,
                    load_avg_5=load_avg_5,
                    load_avg_15=load_avg_15,
                    container_metrics=docker_stats.get("containers") if docker_stats else None,
                    task_health=celery_status,
                )
                await db.commit()

            return {
                "metric_id": str(metric.id) if metric else None,
                "timestamp": metric.timestamp if metric else datetime.now(timezone.utc),
                "system_metrics": system_metrics,
                "docker_stats": docker_stats,
                "celery_status": celery_status,
            }
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
            await db.rollback()
            raise

    @staticmethod
    async def get_task_statuses(db: AsyncSession) -> List[TaskStatus]:
        """Get all task statuses."""
        stmt = select(TaskStatus).order_by(TaskStatus.task_name)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_task_status(db: AsyncSession, task_name: str) -> Optional[TaskStatus]:
        """Get status for a specific task."""
        stmt = select(TaskStatus).where(TaskStatus.task_name == task_name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def sync_all_task_statuses(db: AsyncSession) -> Dict[str, str]:
        """Sync all task statuses from Celery."""
        task_statuses = await CeleryHealthCheck.sync_task_statuses(db)
        await db.commit()
        return task_statuses

    @staticmethod
    async def check_system_health(db: AsyncSession) -> Dict[str, Any]:
        """Check overall system health and return alerts."""
        try:
            system_metrics = await asyncio.to_thread(SystemMetrics.get_all_metrics)

            alerts = []

            # Check CPU
            if system_metrics.get("cpu"):
                cpu_percent = system_metrics["cpu"].get("percent")
                if cpu_percent and cpu_percent > settings.SYSTEM_CPU_WARNING_THRESHOLD:
                    alerts.append({
                        "alert_type": "cpu_high",
                        "severity": "warning",
                        "message": f"High CPU usage: {cpu_percent:.1f}%",
                        "current_value": cpu_percent,
                        "threshold": settings.SYSTEM_CPU_WARNING_THRESHOLD,
                    })

            # Check Memory
            if system_metrics.get("memory"):
                memory_percent = system_metrics["memory"].get("percent")
                if memory_percent and memory_percent > settings.SYSTEM_MEMORY_WARNING_THRESHOLD:
                    alerts.append({
                        "alert_type": "memory_high",
                        "severity": "warning",
                        "message": f"High memory usage: {memory_percent:.1f}%",
                        "current_value": memory_percent,
                        "threshold": settings.SYSTEM_MEMORY_WARNING_THRESHOLD,
                    })

            # Check Disk
            if system_metrics.get("disk"):
                disk_percent = system_metrics["disk"].get("percent")
                if disk_percent and disk_percent > settings.SYSTEM_DISK_CRITICAL_THRESHOLD:
                    alerts.append({
                        "alert_type": "disk_high",
                        "severity": "critical",
                        "message": f"High disk usage: {disk_percent:.1f}%",
                        "current_value": disk_percent,
                        "threshold": settings.SYSTEM_DISK_CRITICAL_THRESHOLD,
                    })

            # Check Task Health
            task_statuses = await SystemMonitoringService.get_task_statuses(db)
            failed_tasks = [t for t in task_statuses if not t.is_healthy]

            if failed_tasks:
                for task in failed_tasks:
                    alerts.append({
                        "alert_type": "task_failed",
                        "severity": "critical",
                        "message": f"Task '{task.task_name}' is unhealthy: {task.status}",
                        "current_value": task.status,
                        "threshold": None,
                    })

            # Determine overall health
            is_healthy = len(alerts) == 0

            return {
                "is_healthy": is_healthy,
                "alerts": alerts,
                "alert_count": len(alerts),
                "critical_alerts": len([a for a in alerts if a["severity"] == "critical"]),
                "warning_alerts": len([a for a in alerts if a["severity"] == "warning"]),
                "timestamp": datetime.now(timezone.utc),
            }
        except Exception as e:
            logger.error(f"Error checking system health: {e}")
            return {
                "is_healthy": False,
                "alerts": [{
                    "alert_type": "system_error",
                    "severity": "critical",
                    "message": f"Error checking system health: {str(e)}",
                    "current_value": None,
                    "threshold": None,
                }],
                "alert_count": 1,
                "critical_alerts": 1,
                "warning_alerts": 0,
                "timestamp": datetime.now(timezone.utc),
            }

    @staticmethod
    async def cleanup_old_logs(db: AsyncSession, days: int = 30) -> int:
        """Delete logs older than specified days."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(SystemLog).where(SystemLog.created_at < cutoff_date)
        result = await db.execute(stmt)
        await db.commit()
        count = result.rowcount or 0
        logger.info(f"Deleted {count} system logs older than {days} days")
        return count

    @staticmethod
    async def cleanup_old_metrics(db: AsyncSession, days: int = 30) -> int:
        """Delete metrics older than specified days."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(SystemMetric).where(SystemMetric.created_at < cutoff_date)
        result = await db.execute(stmt)
        await db.commit()
        count = result.rowcount or 0
        logger.info(f"Deleted {count} system metrics older than {days} days")
        return count
