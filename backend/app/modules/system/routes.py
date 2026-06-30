"""API routes for system monitoring."""

import logging
from typing import Optional, List
import json
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.core.security import decode_token
from app.modules.auth.rbac import has_permission
from app.modules.auth.service import AuthService
from app.modules.system.service import SystemMonitoringService
from app.modules.system.schemas import (
    SystemLogListResponse,
    SystemLogResponse,
    SystemMetricResponse,
    TaskStatusListResponse,
    TaskStatusResponse,
    ComprehensiveHealthResponse,
    RealTimeMetricsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/system",
    tags=["system-monitoring"],
)

RequireSystemPermission = Depends(require_permission("manage_system"))


def _metric_percent(metrics: dict, key: str) -> Optional[float]:
    """Return a percent value from an optional metrics section."""
    section = metrics.get(key)
    if not isinstance(section, dict):
        return None
    return section.get("percent")


@router.get("/metrics", response_model=RealTimeMetricsResponse)
async def get_metrics(
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Get current system metrics snapshot."""
    try:
        # Collect fresh metrics
        metrics_data = await SystemMonitoringService.collect_all_metrics(db, save=False)
        system_metrics = metrics_data.get("system_metrics", {})

        # Extract metrics
        cpu_percent = _metric_percent(system_metrics, "cpu")
        memory_percent = _metric_percent(system_metrics, "memory")
        disk_percent = _metric_percent(system_metrics, "disk")

        # Get task statuses
        task_statuses = await SystemMonitoringService.get_task_statuses(db)
        failed_tasks = len([t for t in task_statuses if not t.is_healthy])

        # Get celery status
        celery_status = metrics_data.get("celery_status", {})
        active = celery_status.get("active_tasks", [])
        scheduled = celery_status.get("scheduled_tasks", [])

        return RealTimeMetricsResponse(
            timestamp=metrics_data.get("timestamp"),
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            active_task_count=len(active),
            failed_task_count=failed_tasks,
            scheduled_task_count=len(scheduled),
        )
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        raise


@router.get("/logs", response_model=SystemLogListResponse)
async def get_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    category: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    task_name: Optional[str] = Query(None),
    minutes: int = Query(60, ge=1, le=1440),
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Get system logs with optional filtering."""
    try:
        logs, total = await SystemMonitoringService.get_logs(
            db,
            limit=limit,
            offset=offset,
            category=category,
            level=level,
            task_name=task_name,
            minutes=minutes,
        )

        return SystemLogListResponse(
            logs=[
                SystemLogResponse.model_validate(log) for log in logs
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        raise


@router.get("/tasks", response_model=TaskStatusListResponse)
async def get_task_statuses(
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Get all task statuses."""
    try:
        tasks = await SystemMonitoringService.get_task_statuses(db)
        healthy = len([t for t in tasks if t.is_healthy])
        unhealthy = len([t for t in tasks if not t.is_healthy])

        return TaskStatusListResponse(
            tasks=[TaskStatusResponse.model_validate(t) for t in tasks],
            total=len(tasks),
            healthy_count=healthy,
            unhealthy_count=unhealthy,
        )
    except Exception as e:
        logger.error(f"Error getting task statuses: {e}")
        raise


@router.post("/tasks/sync")
async def sync_task_statuses(
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Sync all task statuses from Celery."""
    try:
        task_statuses = await SystemMonitoringService.sync_all_task_statuses(db)
        return {
            "status": "success",
            "message": "Task statuses synced",
            "tasks": task_statuses,
        }
    except Exception as e:
        logger.error(f"Error syncing task statuses: {e}")
        raise


@router.get("/health", response_model=ComprehensiveHealthResponse)
async def get_system_health(
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Get comprehensive system health status."""
    try:
        # Get health check results
        health_check = await SystemMonitoringService.check_system_health(db)

        # Get latest metrics
        latest_metric = await SystemMonitoringService.get_latest_metrics(db)

        # Get task statuses
        task_statuses = await SystemMonitoringService.get_task_statuses(db)

        # Get service health
        from app.modules.system.docker_stats import DockerStats
        service_health = DockerStats.get_service_health()

        # Convert health alerts
        alerts = [
            {
                "alert_type": a["alert_type"],
                "severity": a["severity"],
                "message": a["message"],
                "current_value": a.get("current_value"),
                "threshold": a.get("threshold"),
                "timestamp": health_check.get("timestamp"),
            }
            for a in health_check.get("alerts", [])
        ]

        return ComprehensiveHealthResponse(
            timestamp=health_check.get("timestamp"),
            system_metrics=SystemMetricResponse.model_validate(latest_metric) if latest_metric else None,
            task_statuses=[TaskStatusResponse.model_validate(t) for t in task_statuses],
            service_health=service_health,
            alerts=alerts,
            is_healthy=health_check.get("is_healthy", False),
        )
    except Exception as e:
        logger.error(f"Error getting system health: {e}")
        raise


@router.post("/logs/cleanup")
async def cleanup_logs(
    days: int = Query(30, ge=1, le=365),
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Delete logs older than specified days."""
    try:
        count = await SystemMonitoringService.cleanup_old_logs(db, days=days)
        return {
            "status": "success",
            "message": f"Deleted {count} logs older than {days} days",
            "deleted_count": count,
        }
    except Exception as e:
        logger.error(f"Error cleaning up logs: {e}")
        raise


@router.post("/metrics/cleanup")
async def cleanup_metrics(
    days: int = Query(30, ge=1, le=365),
    _current_user=RequireSystemPermission,
    db: AsyncSession = Depends(get_db),
):
    """Delete metrics older than specified days."""
    try:
        count = await SystemMonitoringService.cleanup_old_metrics(db, days=days)
        return {
            "status": "success",
            "message": f"Deleted {count} metrics older than {days} days",
            "deleted_count": count,
        }
    except Exception as e:
        logger.error(f"Error cleaning up metrics: {e}")
        raise


# WebSocket endpoint for real-time log streaming
class LogStreamManager:
    """Manage WebSocket connections for log streaming."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove disconnected WebSocket."""
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to WebSocket: {e}")


# Create a single instance for managing connections
log_stream_manager = LogStreamManager()


async def _websocket_has_system_permission(
    websocket: WebSocket,
    db: AsyncSession,
    token: Optional[str],
) -> bool:
    """Validate WebSocket token and enforce manage_system permission."""
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return False

    try:
        payload = decode_token(token)
        if not payload or payload.get("type") != "access":
            raise ValueError("Invalid access token")

        user = await AuthService.get_user_by_id(db, UUID(payload["sub"]))
        if not user.is_active or not has_permission(user.role, "manage_system"):
            raise PermissionError("System monitoring permission required")
        return True
    except Exception as exc:
        logger.warning(f"Rejected system monitoring WebSocket: {exc}")
        await websocket.close(code=1008, reason="Unauthorized")
        return False


@router.websocket("/ws/logs")
async def websocket_log_stream(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """WebSocket endpoint for real-time log streaming."""
    if not await _websocket_has_system_permission(websocket, db, token):
        return

    await log_stream_manager.connect(websocket)

    try:
        # Send initial system status
        health_check = await SystemMonitoringService.check_system_health(db)
        initial_message = {
            "type": "connected",
            "message": "Connected to system monitoring stream",
            "is_healthy": health_check.get("is_healthy"),
            "alert_count": health_check.get("alert_count"),
        }
        await websocket.send_json(initial_message)

        # Keep connection alive and send periodic updates
        while True:
            try:
                # Wait for message from client (with timeout to allow periodic updates)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)

                # Handle client commands (e.g., filtering)
                command = json.loads(data)
                if command.get("action") == "filter":
                    # Store filter preferences if needed
                    pass

            except asyncio.TimeoutError:
                # Send periodic health update
                try:
                    health_check = await SystemMonitoringService.check_system_health(db)

                    # Get latest logs
                    logs, _ = await SystemMonitoringService.get_logs(db, limit=10)

                    # Get metrics
                    metrics_data = await SystemMonitoringService.collect_all_metrics(db, save=False)

                    update_message = {
                        "type": "update",
                        "is_healthy": health_check.get("is_healthy"),
                        "alerts": health_check.get("alerts", []),
                        "alert_count": health_check.get("alert_count"),
                        "recent_logs": [
                            {
                                "level": log.level,
                                "category": log.category,
                                "message": log.message,
                                "timestamp": log.timestamp.isoformat(),
                            }
                            for log in logs[:5]  # Send last 5 logs
                        ],
                        "metrics": {
                            "cpu_percent": metrics_data.get("system_metrics", {}).get("cpu", {}).get("percent"),
                            "memory_percent": metrics_data.get("system_metrics", {}).get("memory", {}).get("percent"),
                            "disk_percent": metrics_data.get("system_metrics", {}).get("disk", {}).get("percent"),
                        }
                    }
                    await websocket.send_json(update_message)

                except Exception as e:
                    logger.error(f"Error sending periodic update: {e}")
                    error_message = {
                        "type": "error",
                        "message": f"Error: {str(e)}",
                    }
                    await websocket.send_json(error_message)

            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                error_message = {"type": "error", "message": "Invalid JSON format"}
                await websocket.send_json(error_message)

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in log_stream_manager.active_connections:
            log_stream_manager.disconnect(websocket)
