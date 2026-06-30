"""
Strategy service: CRUD for quantitative trading strategies.
"""

from uuid import UUID
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.modules.strategy.models import QuantStrategy
from app.modules.strategy.schemas import StrategyCreate, StrategyUpdate


class StrategyService:

    @staticmethod
    async def create_strategy(
        db: AsyncSession, user_id: UUID, data: StrategyCreate
    ) -> QuantStrategy:
        strategy = QuantStrategy(
            user_id=user_id,
            name=data.name,
            description=data.description,
            symbols=data.symbols,
            timeframe=data.timeframe,
            indicators_config=data.indicators_config,
            entry_conditions=data.entry_conditions,
            exit_conditions=data.exit_conditions,
            position_sizing=data.position_sizing,
            stop_loss_pct=data.stop_loss_pct,
            take_profit_pct=data.take_profit_pct,
            max_positions=data.max_positions,
            cooldown_hours=data.cooldown_hours,
        )
        db.add(strategy)
        await db.flush()
        await db.refresh(strategy)
        return strategy

    @staticmethod
    async def get_strategies(db: AsyncSession, user_id: UUID) -> list[QuantStrategy]:
        result = await db.execute(
            select(QuantStrategy)
            .where(QuantStrategy.user_id == user_id)
            .order_by(desc(QuantStrategy.created_at))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_strategy(
        db: AsyncSession, user_id: UUID, strategy_id: UUID
    ) -> QuantStrategy:
        result = await db.execute(
            select(QuantStrategy).where(
                QuantStrategy.id == strategy_id,
                QuantStrategy.user_id == user_id,
            )
        )
        strategy = result.scalar_one_or_none()
        if not strategy:
            raise NotFoundException("Strategy")
        return strategy

    @staticmethod
    async def update_strategy(
        db: AsyncSession, user_id: UUID, strategy_id: UUID, data: StrategyUpdate
    ) -> QuantStrategy:
        strategy = await StrategyService.get_strategy(db, user_id, strategy_id)
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(strategy, key, value)
        await db.flush()
        await db.refresh(strategy)
        return strategy

    @staticmethod
    async def delete_strategy(
        db: AsyncSession, user_id: UUID, strategy_id: UUID
    ) -> None:
        strategy = await StrategyService.get_strategy(db, user_id, strategy_id)
        await db.delete(strategy)

    @staticmethod
    async def toggle_active(
        db: AsyncSession, user_id: UUID, strategy_id: UUID
    ) -> QuantStrategy:
        strategy = await StrategyService.get_strategy(db, user_id, strategy_id)
        strategy.is_active = not strategy.is_active
        await db.flush()
        await db.refresh(strategy)
        return strategy

    @staticmethod
    async def get_active_strategies(db: AsyncSession, user_id: UUID) -> list[QuantStrategy]:
        """Get all active strategies for a user (used by Celery tasks)."""
        result = await db.execute(
            select(QuantStrategy).where(
                QuantStrategy.user_id == user_id,
                QuantStrategy.is_active == True,
            )
        )
        return list(result.scalars().all())
