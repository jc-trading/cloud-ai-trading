"""
Admin service: system overview, user management, activity logs.
"""

from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User, UserRole
# TODO: PHASE 3+ Admin/Trading refactor - Trade, ActivityLog models don't exist
# from app.modules.trading.models import Trade, ActivityLog, TradingModeType
from app.modules.analysis.models import AIAnalysisResult
from app.modules.exchange.models import ExchangeConnection


class AdminService:
    """Admin service - PARTIAL IMPLEMENTATION pending trading module refactor."""

    # TODO: PHASE 3+ Refactor these methods when Trade and ActivityLog models are defined
    # @staticmethod
    # async def get_dashboard_stats(db: AsyncSession) -> dict:
    #     """Get admin dashboard overview stats."""
    #     # User counts by role
    #     user_counts = await db.execute(
    #         select(User.role, func.count(User.id)).group_by(User.role)
    #     )
    #     users_by_role = {r[0].value: r[1] for r in user_counts.all()}
    #     total_users = sum(users_by_role.values())
    #
    #     # Trade counts (commented - Trade model doesn't exist)
    #     # trade_count = await db.execute(select(func.count(Trade.id)))
    #     # total_trades = trade_count.scalar() or 0
    #
    #     # Exchange connections
    #     conn_count = await db.execute(select(func.count(ExchangeConnection.id)))
    #     total_connections = conn_count.scalar() or 0
    #
    #     # AI analysis stats
    #     analysis_count = await db.execute(select(func.count(AIAnalysisResult.id)))
    #     total_analyses = analysis_count.scalar() or 0
    #
    #     cost_result = await db.execute(
    #         select(func.coalesce(func.sum(AIAnalysisResult.api_cost), 0))
    #     )
    #     total_api_cost = float(cost_result.scalar() or 0)
    #
    #     return {
    #         "total_users": total_users,
    #         "users_by_role": users_by_role,
    #         "total_trades": 0,  # TODO
    #         "live_trades": 0,   # TODO
    #         "simulate_trades": 0,  # TODO
    #         "total_connections": total_connections,
    #         "total_analyses": total_analyses,
    #         "total_api_cost": total_api_cost,
    #     }
    #
    # @staticmethod
    # async def get_activity_logs(
    #     db: AsyncSession, limit: int = 100, user_id: UUID | None = None
    # ) -> list[ActivityLog]:
    #     """Get system activity logs - NOT IMPLEMENTED."""
    #     return []
    #
    # @staticmethod
    # async def log_activity(
    #     db: AsyncSession,
    #     action: str,
    #     user_id: UUID | None = None,
    #     description: str | None = None,
    #     metadata: dict | None = None,
    #     ip_address: str | None = None,
    # ):
    #     """Log a system activity - NOT IMPLEMENTED."""
    #     pass
