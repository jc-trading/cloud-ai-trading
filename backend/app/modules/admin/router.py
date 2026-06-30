"""
Admin API routes (super_admin / admin only).
"""

from fastapi import APIRouter

from app.dependencies import AdminUser, DB
from app.modules.admin.schemas import DashboardStats
from app.modules.admin.service import AdminService

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/dashboard", response_model=DashboardStats)
async def admin_dashboard(
    admin: AdminUser,
    db: DB,
):
    """Get admin dashboard overview."""
    return await AdminService.get_dashboard_stats(db)
