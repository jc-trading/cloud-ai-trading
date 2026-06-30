"""
Admin API routes (super_admin / admin only).
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Query

from app.dependencies import AdminUser, DB
from app.modules.admin.schemas import DashboardStats, ActivityLogResponse
from app.modules.admin.service import AdminService

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/dashboard", response_model=DashboardStats)
async def admin_dashboard(
    admin: AdminUser,
    db: DB,
):
    """Get admin dashboard overview."""
    return await AdminService.get_dashboard_stats(db)


@router.get("/activity-logs", response_model=list[ActivityLogResponse])
async def activity_logs(
    admin: AdminUser,
    db: DB,
    user_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get system activity logs."""
    logs = await AdminService.get_activity_logs(db, limit, user_id)
    return [ActivityLogResponse.model_validate(l) for l in logs]
