"""
Admin service: system overview.

NOTE: the old per-trade and activity-log tables were dropped in migration
006_drop_old_trade_tables; the exchange/analysis tables followed in
017_drop_legacy_tables. Dashboard stats are rebuilt from the models that still
exist. Activity logs are not implemented because their table no longer exists.
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User


class AdminService:
    """Admin service - dashboard overview from currently-existing models."""

    @staticmethod
    async def get_dashboard_stats(db: AsyncSession) -> dict:
        """Get admin dashboard overview stats."""
        user_counts = await db.execute(
            select(User.role, func.count(User.id)).group_by(User.role)
        )
        users_by_role = {r[0].value: r[1] for r in user_counts.all()}

        return {
            "total_users": sum(users_by_role.values()),
            "users_by_role": users_by_role,
        }
