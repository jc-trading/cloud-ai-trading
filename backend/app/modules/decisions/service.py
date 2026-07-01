"""
Decision feed service: reads the unified Decision rows persisted on
ai_analysis_results (see migration 010) and shapes them for the dashboard.
"""

from uuid import UUID

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analysis.models import AIAnalysisResult, AssetClass


class DecisionService:

    @staticmethod
    async def get_latest_per_symbol(
        db: AsyncSession,
        user_id: UUID,
        asset_class: AssetClass | None = None,
    ) -> list[AIAnalysisResult]:
        """Latest Decision for each tracked symbol (crypto + equity unified).

        Uses Postgres ``DISTINCT ON (symbol)`` to collapse each symbol's history
        down to its most recent row, then re-sorts newest-first for display.
        """
        query = select(AIAnalysisResult).where(
            AIAnalysisResult.user_id == user_id
        )
        if asset_class is not None:
            query = query.where(AIAnalysisResult.asset_class == asset_class)

        # DISTINCT ON needs the distinct column first in ORDER BY; created_at DESC
        # inside each symbol group picks the newest row.
        query = query.order_by(
            AIAnalysisResult.symbol,
            desc(AIAnalysisResult.created_at),
        ).distinct(AIAnalysisResult.symbol)

        result = await db.execute(query)
        rows = list(result.scalars().all())
        # Present newest-first across symbols (DISTINCT ON forced symbol-first order).
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows

    @staticmethod
    async def get_history(
        db: AsyncSession,
        user_id: UUID,
        symbol: str,
        limit: int = 100,
    ) -> list[AIAnalysisResult]:
        """Full Decision history for one symbol, newest-first."""
        result = await db.execute(
            select(AIAnalysisResult)
            .where(
                AIAnalysisResult.user_id == user_id,
                AIAnalysisResult.symbol == symbol,
            )
            .order_by(desc(AIAnalysisResult.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())
