"""Pydantic schemas for system monitoring API responses."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


# SystemLog Schemas
class SystemLogResponse(BaseModel):
    """Response schema for individual system log."""

    id: UUID
    timestamp: datetime
    category: str  # market_data, trading, schedule, system
    level: str  # INFO, WARNING, ERROR, DEBUG, CRITICAL
    message: str
    task_name: Optional[str] = None
    symbol: Optional[str] = None
    signal_type: Optional[str] = None
    status: Optional[str] = None
    duration_ms: Optional[int] = None
    event_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SystemLogListResponse(BaseModel):
    """Response schema for list of system logs with pagination."""

    logs: List[SystemLogResponse]
    total: int
    limit: int
    offset: int


# SystemMetric Schemas
class CPUMetricsResponse(BaseModel):
    """CPU metrics response."""

    percent: Optional[float] = Field(None, description="CPU usage percentage")
    cores: Optional[int] = Field(None, description="Number of CPU cores")
    load_average: Optional[Dict[str, Optional[float]]] = Field(
        None, description="Load average for 1, 5, 15 minutes"
    )


class MemoryMetricsResponse(BaseModel):
    """Memory metrics response."""

    total: Optional[int] = Field(None, description="Total memory in bytes")
    used: Optional[int] = Field(None, description="Used memory in bytes")
    available: Optional[int] = Field(None, description="Available memory in bytes")
    percent: Optional[float] = Field(None, description="Memory usage percentage")
    free: Optional[int] = Field(None, description="Free memory in bytes")


class DiskMetricsResponse(BaseModel):
    """Disk metrics response."""

    total: Optional[int] = Field(None, description="Total disk space in bytes")
    used: Optional[int] = Field(None, description="Used disk space in bytes")
    free: Optional[int] = Field(None, description="Free disk space in bytes")
    percent: Optional[float] = Field(None, description="Disk usage percentage")


class NetworkMetricsResponse(BaseModel):
    """Network I/O metrics response."""

    bytes_sent: Optional[int] = Field(None, description="Total bytes sent")
    bytes_recv: Optional[int] = Field(None, description="Total bytes received")
    packets_sent: Optional[int] = Field(None, description="Total packets sent")
    packets_recv: Optional[int] = Field(None, description="Total packets received")
    errin: Optional[int] = Field(None, description="Errors in")
    errout: Optional[int] = Field(None, description="Errors out")
    dropin: Optional[int] = Field(None, description="Dropped inbound packets")
    dropout: Optional[int] = Field(None, description="Dropped outbound packets")


class SystemMetricResponse(BaseModel):
    """Response schema for system metrics snapshot."""

    id: UUID
    timestamp: datetime
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    load_average_1: Optional[Decimal] = None
    load_average_5: Optional[Decimal] = None
    load_average_15: Optional[Decimal] = None
    container_metrics: Optional[Dict[str, Any]] = None
    task_health: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# TaskStatus Schemas
class TaskStatusResponse(BaseModel):
    """Response schema for task status."""

    id: UUID
    task_name: str
    description: Optional[str] = None
    status: str  # online, offline, running, idle, failed
    is_healthy: bool
    last_execution_time: Optional[datetime] = None
    last_execution_duration_ms: Optional[int] = None
    next_execution_time: Optional[datetime] = None
    last_error_time: Optional[datetime] = None
    last_error_message: Optional[str] = None
    total_executions: int
    failed_executions: int
    success_rate: Optional[Decimal] = None
    schedule_interval: Optional[str] = None
    schedule_type: Optional[str] = None
    task_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskStatusListResponse(BaseModel):
    """Response schema for list of task statuses."""

    tasks: List[TaskStatusResponse]
    total: int
    healthy_count: int
    unhealthy_count: int


# Comprehensive Status Responses
class HealthAlertResponse(BaseModel):
    """Response schema for health alerts."""

    alert_type: str  # cpu_high, memory_high, task_failed, etc.
    severity: str  # info, warning, critical
    message: str
    current_value: Optional[float | str] = None
    threshold: Optional[float | str] = None
    timestamp: datetime


class ComprehensiveHealthResponse(BaseModel):
    """Comprehensive system health status response."""

    timestamp: datetime
    system_metrics: Optional[SystemMetricResponse] = None
    cpu_metrics: Optional[CPUMetricsResponse] = None
    memory_metrics: Optional[MemoryMetricsResponse] = None
    disk_metrics: Optional[DiskMetricsResponse] = None
    network_metrics: Optional[NetworkMetricsResponse] = None
    task_statuses: List[TaskStatusResponse] = Field(default_factory=list)
    service_health: Optional[Dict[str, str]] = None  # {service_name: online|offline}
    alerts: List[HealthAlertResponse] = Field(default_factory=list)
    is_healthy: bool = Field(
        True, description="Overall system health status"
    )


class RealTimeMetricsResponse(BaseModel):
    """Real-time metrics response for dashboard updates."""

    timestamp: datetime
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    active_task_count: int = 0
    failed_task_count: int = 0
    scheduled_task_count: int = 0
    alerts: List[HealthAlertResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# WebSocket Message Schemas
class LogStreamMessage(BaseModel):
    """Message schema for WebSocket log streaming."""

    type: str  # "log", "status", "metric", "alert"
    data: Dict[str, Any]
    timestamp: datetime


class TaskHealthUpdate(BaseModel):
    """Task health update message for WebSocket."""

    task_name: str
    status: str  # online, offline, running, idle, failed
    is_healthy: bool
    last_execution_time: Optional[datetime] = None
    next_execution_time: Optional[datetime] = None
    updated_at: datetime
