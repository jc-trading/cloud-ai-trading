"""
Pydantic schemas for admin endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class DashboardStats(BaseModel):
    total_users: int
    users_by_role: dict
    total_trades: int
    live_trades: int
    simulate_trades: int
    total_connections: int
    total_analyses: int
    total_api_cost: float


class ActivityLogResponse(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    action: str
    description: Optional[str]
    metadata: Optional[dict] = Field(None, alias="meta_data")
    ip_address: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True, "populate_by_name": True}
