"""Portfolio management service."""

from decimal import Decimal
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import logging

from .models import Position, PortfolioStats

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Manage trading positions and portfolio statistics."""

    @staticmethod
    async def add_position(
        session: AsyncSession,
        watchlist_id: str,
        symbol: str,
        entry_price: Decimal,
        quantity: Decimal,
        entry_date,
        position_type: str = "LONG",
        notes: Optional[str] = None,
    ) -> Position:
        """Add a new trading position."""
        position = Position(
            watchlist_id=watchlist_id,
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_date=entry_date,
            position_type=position_type,
            notes=notes,
            status="open",
        )
        session.add(position)
        await session.flush()
        logger.info(
            f"Position opened: {symbol} {quantity}@{entry_price} (type: {position_type})"
        )
        return position

    @staticmethod
    async def close_position(
        session: AsyncSession,
        position_id: str,
        exit_price: Decimal,
        exit_date,
    ) -> Position:
        """Close a trading position."""
        stmt = select(Position).where(Position.id == position_id)
        result = await session.execute(stmt)
        position = result.scalar_one_or_none()

        if not position:
            raise ValueError(f"Position {position_id} not found")

        if position.status == "closed":
            raise ValueError(f"Position {position_id} is already closed")

        position.exit_price = exit_price
        position.exit_date = exit_date
        position.status = "closed"

        realized_pnl = (exit_price - position.entry_price) * position.quantity

        logger.info(
            f"Position closed: {position.symbol} "
            f"(entry: {position.entry_price}, exit: {exit_price}, "
            f"pnl: {realized_pnl})"
        )
        return position

    @staticmethod
    async def calculate_unrealized_pnl(
        session: AsyncSession,
        watchlist_id: str,
        current_prices: dict,  # {"BTCUSDT": 45000, "ETHUSDT": 2500, ...}
    ) -> Decimal:
        """
        Calculate unrealized P&L for open positions.

        Args:
            session: AsyncSession
            watchlist_id: Watchlist ID
            current_prices: Dict of {symbol: current_price}

        Returns:
            Total unrealized P&L as Decimal
        """
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "open",
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()

        total_unrealized_pnl = Decimal(0)

        for position in positions:
            current_price = current_prices.get(position.symbol)
            if current_price is None:
                logger.warning(
                    f"Current price not available for {position.symbol}"
                )
                continue

            current_price = Decimal(str(current_price))
            unrealized = (current_price - position.entry_price) * position.quantity
            total_unrealized_pnl += unrealized

        return total_unrealized_pnl

    @staticmethod
    async def calculate_realized_pnl(
        session: AsyncSession,
        watchlist_id: str,
    ) -> Decimal:
        """Calculate realized P&L for closed positions."""
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "closed",
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()

        total_realized_pnl = Decimal(0)

        for position in positions:
            if position.exit_price:
                realized = (position.exit_price - position.entry_price) * position.quantity
                total_realized_pnl += realized

        return total_realized_pnl

    @staticmethod
    async def calculate_total_invested(
        session: AsyncSession,
        watchlist_id: str,
    ) -> Decimal:
        """Calculate total amount invested."""
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
        )
        result = await session.execute(stmt)
        positions = result.scalars().all()

        total_invested = Decimal(0)

        for position in positions:
            invested = position.entry_price * position.quantity
            total_invested += invested

        return total_invested

    @staticmethod
    async def calculate_win_rate(
        session: AsyncSession,
        watchlist_id: str,
    ) -> tuple:
        """
        Calculate win rate and trade counts.

        Returns:
            (win_rate: Decimal, total_trades: int, winning_trades: int, losing_trades: int)
        """
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "closed",
        )
        result = await session.execute(stmt)
        closed_positions = result.scalars().all()

        if not closed_positions:
            return Decimal(0), 0, 0, 0

        total_trades = len(closed_positions)
        winning_trades = 0
        losing_trades = 0

        for position in closed_positions:
            if position.exit_price:
                pnl = (position.exit_price - position.entry_price) * position.quantity
                if pnl > 0:
                    winning_trades += 1
                elif pnl < 0:
                    losing_trades += 1

        win_rate = (
            Decimal(winning_trades) / Decimal(total_trades) * 100
            if total_trades > 0
            else Decimal(0)
        )

        return win_rate, total_trades, winning_trades, losing_trades

    @staticmethod
    async def update_portfolio_stats(
        session: AsyncSession,
        watchlist_id: str,
        current_prices: dict,
    ) -> PortfolioStats:
        """Update or create portfolio statistics."""
        # Calculate all metrics
        total_invested = await PortfolioManager.calculate_total_invested(
            session, watchlist_id
        )
        realized_pnl = await PortfolioManager.calculate_realized_pnl(
            session, watchlist_id
        )
        unrealized_pnl = await PortfolioManager.calculate_unrealized_pnl(
            session, watchlist_id, current_prices
        )
        win_rate, total_trades, winning_trades, losing_trades = (
            await PortfolioManager.calculate_win_rate(session, watchlist_id)
        )

        current_value = total_invested + unrealized_pnl
        total_pnl = realized_pnl + unrealized_pnl

        total_return_percent = (
            (total_pnl / total_invested * 100) if total_invested > 0 else Decimal(0)
        )

        # Get or create portfolio stats
        stmt = select(PortfolioStats).where(
            PortfolioStats.watchlist_id == watchlist_id
        )
        result = await session.execute(stmt)
        stats = result.scalar_one_or_none()

        if stats is None:
            stats = PortfolioStats(watchlist_id=watchlist_id)
            session.add(stats)

        # Update stats
        stats.total_invested = total_invested
        stats.current_value = current_value
        stats.unrealized_pnl = unrealized_pnl
        stats.realized_pnl = realized_pnl
        stats.total_return_percent = total_return_percent
        stats.win_rate = win_rate
        stats.total_trades = total_trades
        stats.winning_trades = winning_trades
        stats.losing_trades = losing_trades

        await session.flush()

        logger.info(
            f"Portfolio stats updated: "
            f"invested={total_invested}, current={current_value}, "
            f"pnl={total_pnl}, return={total_return_percent:.2f}%, "
            f"win_rate={win_rate:.2f}%"
        )

        return stats

    @staticmethod
    async def get_open_positions(
        session: AsyncSession,
        watchlist_id: str,
    ) -> List[Position]:
        """Get all open positions for a watchlist."""
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "open",
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_portfolio_stats(
        session: AsyncSession,
        watchlist_id: str,
    ) -> Optional[PortfolioStats]:
        """Get portfolio stats for a watchlist."""
        stmt = select(PortfolioStats).where(
            PortfolioStats.watchlist_id == watchlist_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
