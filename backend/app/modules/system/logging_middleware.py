"""Logging middleware for system event capture."""

import logging
import time
from typing import Callable, Optional
from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.modules.system.service import SystemMonitoringService

logger = logging.getLogger(__name__)


class SystemLogMiddleware(BaseHTTPMiddleware):
    """Middleware to capture and log system events."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response information."""
        start_time = time.time()

        try:
            response = await call_next(request)
        except Exception as exc:
            # Log the exception
            await self._log_event(
                category="system",
                level="ERROR",
                message=f"Exception in {request.method} {request.url.path}: {str(exc)}",
                status_code=500,
                duration_ms=int((time.time() - start_time) * 1000),
            )
            raise

        # Log the request/response
        duration_ms = int((time.time() - start_time) * 1000)

        # Determine log level based on status code
        if response.status_code >= 500:
            level = "ERROR"
        elif response.status_code >= 400:
            level = "WARNING"
        else:
            level = "INFO"

        await self._log_event(
            category="system",
            level=level,
            message=f"{request.method} {request.url.path} {response.status_code}",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        return response

    @staticmethod
    async def _log_event(
        category: str,
        level: str,
        message: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ):
        """Log an event to the database."""
        try:
            async with AsyncSessionLocal() as db:
                await SystemMonitoringService.create_log(
                    db=db,
                    category=category,
                    level=level,
                    message=message,
                    status=None,
                    duration_ms=duration_ms,
                    metadata={"status_code": status_code} if status_code else None,
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Error logging event: {e}")


class TaskLoggingHandler:
    """Helper class to log task execution events."""

    @staticmethod
    async def log_task_start(
        db: AsyncSession,
        task_name: str,
        metadata: Optional[dict] = None,
    ):
        """Log task start event."""
        await SystemMonitoringService.create_log(
            db=db,
            category="schedule",
            level="INFO",
            message=f"Task '{task_name}' started",
            task_name=task_name,
            status="started",
            metadata=metadata,
        )
        await db.commit()

    @staticmethod
    async def log_task_completion(
        db: AsyncSession,
        task_name: str,
        duration_ms: int,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """Log task completion event."""
        level = "INFO" if success else "ERROR"
        status = "completed" if success else "failed"
        message = f"Task '{task_name}' {status}"
        if error_message:
            message += f": {error_message}"

        await SystemMonitoringService.create_log(
            db=db,
            category="schedule",
            level=level,
            message=message,
            task_name=task_name,
            status=status,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        await db.commit()

    @staticmethod
    async def log_trading_signal(
        db: AsyncSession,
        symbol: str,
        signal_type: str,
        metadata: Optional[dict] = None,
    ):
        """Log trading signal event."""
        await SystemMonitoringService.create_log(
            db=db,
            category="trading",
            level="INFO",
            message=f"Trading signal: {signal_type} for {symbol}",
            symbol=symbol,
            signal_type=signal_type,
            status="generated",
            metadata=metadata,
        )
        await db.commit()

    @staticmethod
    async def log_market_data_collected(
        db: AsyncSession,
        symbol: str,
        data_points: int,
        metadata: Optional[dict] = None,
    ):
        """Log market data collection event."""
        await SystemMonitoringService.create_log(
            db=db,
            category="market_data",
            level="INFO",
            message=f"Collected {data_points} data points for {symbol}",
            symbol=symbol,
            status="completed",
            metadata=metadata or {"data_points": data_points},
        )
        await db.commit()

    @staticmethod
    async def log_error(
        db: AsyncSession,
        error_type: str,
        error_message: str,
        category: str = "system",
        task_name: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """Log an error event."""
        await SystemMonitoringService.create_log(
            db=db,
            category=category,
            level="ERROR",
            message=f"{error_type}: {error_message}",
            task_name=task_name,
            status="failed",
            metadata=metadata,
        )
        await db.commit()
