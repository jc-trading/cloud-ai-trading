"""Trading service for signals, positions, and portfolio summaries."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, TradingError
from app.modules.trading.models import Position, PortfolioStats, TradingSignal
from app.modules.trading.schemas import TradeCreate, TradeFilter
from app.modules.watchlist.models import Watchlist


def _to_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _position_side(position: Position) -> str:
    return "sell" if position.position_type.upper() == "SHORT" else "buy"


def _position_exit_or_current(position: Position) -> Decimal:
    return Decimal(position.exit_price or position.entry_price)


def _position_pnl(position: Position) -> Decimal:
    current_price = _position_exit_or_current(position)
    entry_price = Decimal(position.entry_price)
    quantity = Decimal(position.quantity)
    if position.position_type.upper() == "SHORT":
        return (entry_price - current_price) * quantity
    return (current_price - entry_price) * quantity


def _position_response(position: Position) -> dict:
    current_price = _position_exit_or_current(position)
    pnl = _position_pnl(position)
    entry_value = Decimal(position.entry_price) * Decimal(position.quantity)
    pnl_percentage = (pnl / entry_value * Decimal("100")) if entry_value else Decimal("0")

    return {
        "id": position.id,
        "watchlist_id": position.watchlist_id,
        "symbol": position.symbol,
        "side": _position_side(position),
        "quantity": _to_float(position.quantity),
        "price": _to_float(position.entry_price),
        "entry_price": _to_float(position.entry_price),
        "current_price": _to_float(current_price),
        "status": position.status,
        "position_type": position.position_type,
        "pnl": _to_float(pnl),
        "pnl_percentage": _to_float(pnl_percentage),
        "return_pct": _to_float(pnl_percentage),
        "opened_at": position.entry_date,
        "timestamp": position.entry_date,
        "closed_at": position.exit_date,
        "created_at": position.created_at,
    }


class TradingService:
    """Service methods backed by the current trading models."""

    @staticmethod
    async def _user_watchlist_ids(db: AsyncSession, user_id: UUID) -> list[UUID]:
        result = await db.execute(select(Watchlist.id).where(Watchlist.user_id == user_id))
        return list(result.scalars().all())

    @staticmethod
    async def _default_watchlist(db: AsyncSession, user_id: UUID) -> Watchlist:
        result = await db.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user_id)
            .order_by(Watchlist.created_at.asc())
            .limit(1)
        )
        watchlist = result.scalar_one_or_none()
        if not watchlist:
            raise TradingError("Create a watchlist before placing simulated trades")
        return watchlist

    @staticmethod
    async def place_trade(db: AsyncSession, user_id: UUID, data: TradeCreate) -> dict:
        """Create a simulated position from an order request."""
        if data.trading_mode == "live":
            raise TradingError("Live trading is not implemented for the current position model")
        if data.price is None:
            raise TradingError("A price is required to create a simulated position")

        watchlist = await TradingService._default_watchlist(db, user_id)
        position = Position(
            watchlist_id=watchlist.id,
            symbol=data.symbol,
            entry_price=data.price,
            quantity=data.quantity,
            entry_date=datetime.now(timezone.utc),
            position_type="SHORT" if data.side == "sell" else "LONG",
            status="open",
        )
        db.add(position)
        await db.flush()
        await db.refresh(position)
        return _position_response(position)

    @staticmethod
    async def get_trades(db: AsyncSession, user_id: UUID, filters: TradeFilter) -> list[dict]:
        """List the user's positions using the existing trades route."""
        watchlist_ids = await TradingService._user_watchlist_ids(db, user_id)
        if not watchlist_ids:
            return []

        stmt = select(Position).where(Position.watchlist_id.in_(watchlist_ids))
        if filters.symbol:
            stmt = stmt.where(Position.symbol == filters.symbol)
        if filters.status:
            stmt = stmt.where(Position.status == filters.status)

        stmt = stmt.order_by(desc(Position.entry_date)).offset(filters.offset).limit(filters.limit)
        result = await db.execute(stmt)
        positions = result.scalars().all()

        responses = [_position_response(position) for position in positions]
        if filters.side:
            responses = [trade for trade in responses if trade["side"] == filters.side]
        return responses

    @staticmethod
    async def get_trade(db: AsyncSession, user_id: UUID, trade_id: UUID) -> dict:
        """Get a specific user-owned position."""
        watchlist_ids = await TradingService._user_watchlist_ids(db, user_id)
        result = await db.execute(
            select(Position).where(
                Position.id == trade_id,
                Position.watchlist_id.in_(watchlist_ids),
            )
        )
        position = result.scalar_one_or_none()
        if not position:
            raise NotFoundException("Trade")
        return _position_response(position)

    @staticmethod
    async def close_trade(db: AsyncSession, user_id: UUID, trade_id: UUID) -> dict:
        """Close a simulated position at its current stored price."""
        watchlist_ids = await TradingService._user_watchlist_ids(db, user_id)
        result = await db.execute(
            select(Position).where(
                Position.id == trade_id,
                Position.watchlist_id.in_(watchlist_ids),
            )
        )
        position = result.scalar_one_or_none()
        if not position:
            raise NotFoundException("Trade")
        if position.status == "closed":
            return _position_response(position)

        position.exit_price = position.entry_price
        position.exit_date = datetime.now(timezone.utc)
        position.status = "closed"
        await db.flush()
        await db.refresh(position)
        return _position_response(position)

    @staticmethod
    async def get_simulate_portfolio(db: AsyncSession, user_id: UUID) -> dict:
        """Summarize positions for all of the user's watchlists."""
        watchlist_ids = await TradingService._user_watchlist_ids(db, user_id)
        if not watchlist_ids:
            return {
                "user_id": user_id,
                "current_balance": 0.0,
                "total_invested": 0.0,
                "current_value": 0.0,
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "total_return_percent": 0.0,
                "total_trades": 0,
                "open_trades": 0,
                "closed_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "positions": [],
            }

        result = await db.execute(select(Position).where(Position.watchlist_id.in_(watchlist_ids)))
        positions = result.scalars().all()

        position_responses = [_position_response(position) for position in positions]
        open_positions = [position for position in position_responses if position["status"] == "open"]
        closed_positions = [position for position in position_responses if position["status"] == "closed"]

        total_invested = sum(
            _to_float(position.entry_price) * _to_float(position.quantity)
            for position in positions
        )
        unrealized_pnl = sum(position["pnl"] for position in open_positions)
        realized_pnl = sum(position["pnl"] for position in closed_positions)
        total_pnl = unrealized_pnl + realized_pnl
        win_count = sum(1 for position in closed_positions if position["pnl"] > 0)
        loss_count = sum(1 for position in closed_positions if position["pnl"] < 0)
        closed_count = len(closed_positions)
        win_rate = (win_count / closed_count * 100) if closed_count else 0.0
        current_value = total_invested + total_pnl
        total_return_percent = (total_pnl / total_invested * 100) if total_invested else 0.0

        return {
            "user_id": user_id,
            "current_balance": current_value,
            "balance": current_value,
            "total_invested": total_invested,
            "current_value": current_value,
            "total_pnl": total_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_return_percent": total_return_percent,
            "total_trades": len(position_responses),
            "open_trades": len(open_positions),
            "closed_trades": closed_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "positions": open_positions,
        }

    @staticmethod
    async def reset_simulate_portfolio(db: AsyncSession, user_id: UUID) -> dict:
        """Clear user positions and portfolio stats."""
        watchlist_ids = await TradingService._user_watchlist_ids(db, user_id)
        if watchlist_ids:
            await db.execute(delete(Position).where(Position.watchlist_id.in_(watchlist_ids)))
            await db.execute(delete(PortfolioStats).where(PortfolioStats.watchlist_id.in_(watchlist_ids)))
            await db.flush()
        return await TradingService.get_simulate_portfolio(db, user_id)

    @staticmethod
    async def get_trade_summary(
        db: AsyncSession, user_id: UUID, trading_mode: str | None = None
    ) -> dict:
        """Get summary statistics for user's simulated positions."""
        portfolio = await TradingService.get_simulate_portfolio(db, user_id)
        return {
            "total_trades": portfolio["total_trades"],
            "open_trades": portfolio["open_trades"],
            "total_pnl": portfolio["total_pnl"],
            "win_rate": portfolio["win_rate"],
            "best_trade_pnl": None,
            "worst_trade_pnl": None,
        }

    @staticmethod
    async def get_signals(db: AsyncSession, user_id: UUID, limit: int = 50) -> list[TradingSignal]:
        """Get recent trade signals for the user's watchlists."""
        stmt = (
            select(TradingSignal)
            .join(Watchlist, TradingSignal.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == user_id)
            .order_by(desc(TradingSignal.signal_timestamp), desc(TradingSignal.created_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_signals_by_symbol(
        db: AsyncSession, user_id: UUID, symbol: str, limit: int = 50
    ) -> list[TradingSignal]:
        """Get recent trade signals for one symbol."""
        stmt = (
            select(TradingSignal)
            .join(Watchlist, TradingSignal.watchlist_id == Watchlist.id)
            .where(func.lower(TradingSignal.symbol) == symbol.lower())
            .where(Watchlist.user_id == user_id)
            .order_by(desc(TradingSignal.signal_timestamp), desc(TradingSignal.created_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_signal(
        db: AsyncSession,
        user_id: UUID,
        symbol: str,
        signal_type: str,
        action: str,
        confidence: int,
        meta_data: dict | None = None,
    ) -> TradingSignal:
        """Create a basic signal on the user's default watchlist."""
        watchlist = await TradingService._default_watchlist(db, user_id)
        signal = TradingSignal(
            watchlist_id=watchlist.id,
            symbol=symbol,
            signal_type=signal_type,
            signal_strength=Decimal(confidence),
            confidence=Decimal(confidence),
            indicators_used=meta_data,
            recommendation=action,
            strategy="manual",
            signal_timestamp=datetime.now(timezone.utc),
        )
        db.add(signal)
        await db.flush()
        await db.refresh(signal)
        return signal
