"""
Admin service: system overview.

NOTE: the old per-trade and activity-log tables were dropped in migration
006_drop_old_trade_tables. Dashboard stats are rebuilt from the models that
still exist (User, ExchangeConnection, AIAnalysisResult). Activity logs are
not implemented because their table no longer exists.
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.analysis.models import AIAnalysisResult
from app.modules.exchange.models import ExchangeConnection


class AdminService:
    """Admin service - dashboard overview from currently-existing models."""

    @staticmethod
    async def get_dashboard_stats(db: AsyncSession) -> dict:
        """Get admin dashboard overview stats."""
        # User counts by role
        user_counts = await db.execute(
            select(User.role, func.count(User.id)).group_by(User.role)
        )
        users_by_role = {r[0].value: r[1] for r in user_counts.all()}
        total_users = sum(users_by_role.values())

        # Exchange connections
        conn_count = await db.execute(select(func.count(ExchangeConnection.id)))
        total_connections = conn_count.scalar() or 0

        # AI analysis stats
        analysis_count = await db.execute(select(func.count(AIAnalysisResult.id)))
        total_analyses = analysis_count.scalar() or 0

        cost_result = await db.execute(
            select(func.coalesce(func.sum(AIAnalysisResult.api_cost), 0))
        )
        total_api_cost = float(cost_result.scalar() or 0)

        return {
            "total_users": total_users,
            "users_by_role": users_by_role,
            "total_connections": total_connections,
            "total_analyses": total_analyses,
            "total_api_cost": total_api_cost,
        }
