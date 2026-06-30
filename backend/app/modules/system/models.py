"""System monitoring models for database storage."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import String, DateTime, Integer, JSON, Index, func, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemLog(Base):
    """System and application logs for real-time monitoring."""

    __tablename__ = "system_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Log categorization
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # market_data, trading, schedule, system
    level: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # INFO, WARNING, ERROR, DEBUG, CRITICAL
    message: Mapped[str] = mapped_column(Text(), nullable=False)

    # Metadata for filtering and analysis
    task_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )  # e.g., "generate_trading_signals", "collect_market_data"
    symbol: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )  # e.g., "BTCUSDT", "ETHUSDT"
    signal_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g., "STRONG_BUY", "SELL"
    status: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # started, completed, failed
    duration_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSON(), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_system_logs_timestamp", "timestamp"),
        Index("ix_system_logs_category_timestamp", "category", "timestamp"),
        Index("ix_system_logs_task_name_timestamp", "task_name", "timestamp"),
    )


class SystemMetric(Base):
    """Historical system metrics for trends and analytics."""

    __tablename__ = "system_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # System-wide metrics
    cpu_percent: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=2), nullable=False
    )
    memory_percent: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=2), nullable=False
    )
    disk_percent: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=2), nullable=False
    )

    # Load average
    load_average_1: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True
    )
    load_average_5: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True
    )
    load_average_15: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True
    )

    # Per-container metrics (JSON for flexibility)
    container_metrics: Mapped[dict | None] = mapped_column(
        JSON(), nullable=True
    )

    # Task health status
    task_health: Mapped[dict | None] = mapped_column(
        JSON(), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_system_metrics_timestamp", "timestamp"),
    )


class TaskStatus(Base):
    """Real-time status of background tasks and processes."""

    __tablename__ = "task_status"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Task identification
    task_name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )  # e.g., "generate_trading_signals"
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # Status tracking
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # online, offline, running, idle, failed
    is_healthy: Mapped[bool] = mapped_column(
        default=True, nullable=False
    )  # True if online and no recent errors

    # Timing information
    last_execution_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_execution_duration_ms: Mapped[int | None] = mapped_column(
        Integer(), nullable=True
    )
    next_execution_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_message: Mapped[str | None] = mapped_column(
        Text(), nullable=True
    )

    # Statistics
    total_executions: Mapped[int] = mapped_column(Integer(), default=0)
    failed_executions: Mapped[int] = mapped_column(Integer(), default=0)
    success_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True
    )  # percentage

    # Schedule information (Celery Beat)
    schedule_interval: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # e.g., "60" for 60 seconds
    schedule_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # periodic, crontab, schedule

    # Extra metadata
    task_metadata: Mapped[dict | None] = mapped_column(JSON(), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_task_status_updated_at", "updated_at"),
    )
