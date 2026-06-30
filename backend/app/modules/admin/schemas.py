"""
Pydantic schemas for admin endpoints.
"""

from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_users: int
    users_by_role: dict
    total_connections: int
    total_analyses: int
    total_api_cost: float
