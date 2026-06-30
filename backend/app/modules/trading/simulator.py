"""
Simulate trading engine: processes orders against market data without real exchange.
Uses DB-backed portfolio tracking for win rate analysis.
"""

import logging
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
# TODO: PHASE 3+ Trading module refactor
# from app.modules.trading.models import (
#     Trade, SimulatePortfolio, TradeSide, OrderType, TradeStatus, TradingModeType,
# )
# from app.modules.market.service import MarketService

settings = get_settings()
logger = logging.getLogger("cloud_ai_trading.simulator")


class SimulateEngine:
    """
    Handles simulated trading:
    - Market orders fill immediately at current price
    - Limit orders store as pending (would need a price-check loop in prod)
    - Tracks balance, PnL, win/loss in SimulatePortfolio
    """

    @staticmethod
    async def get_or_create_portfolio(
        db: AsyncSession, user_id: UUID
    ) -> SimulatePortfolio:
        """Get user's simulate portfolio, creating one if it doesn't exist."""
        result = await db.execute(
            select(SimulatePortfolio).where(SimulatePortfolio.user_id == user_id)
        )
        portfolio = result.scalar_one_or_none()
        if not portfolio:
            portfolio = SimulatePortfolio(
                user_id=user_id,
                initial_balance=settings.DEFAULT_SIMULATE_BALANCE,
                current_balance=settings.DEFAULT_SIMULATE_BALANCE,
            )
            db.add(portfolio)
            await db.flush()
            await db.refresh(portfolio)
        return portfolio

    @staticmethod
    async def execute_market_order(
        db: AsyncSession,
        user_id: UUID,
        symbol: str,
        side: TradeSide,
        quantity: float,
        strategy_id: UUID | None = None,
        ai_signal_id: UUID | None = None,
    ) -> Trade:
        """Execute a simulated market order at the current price."""
        portfolio = await SimulateEngine.get_or_create_portfolio(db, user_id)

        # Get current market price
        try:
            ticker = await MarketService.get_ticker(symbol)
            current_price = ticker["last"]
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            raise ValueError(f"Cannot get market price for {symbol}")

        # Calculate cost and fee
        cost = quantity * current_price
        fee = cost * settings.BINANCE_FEE_RATE

        # Validate balance for buys
        if side == TradeSide.BUY:
            total_cost = cost + fee
            if portfolio.current_balance < total_cost:
                raise ValueError(
                    f"Insufficient balance. Required: {total_cost:.2f}, "
                    f"Available: {float(portfolio.current_balance):.2f}"
                )
            portfolio.current_balance = float(portfolio.current_balance) - total_cost
        else:
            # For sells, add proceeds minus fee
            proceeds = cost - fee
            portfolio.current_balance = float(portfolio.current_balance) + proceeds

        # Create trade record
        trade = Trade(
            user_id=user_id,
            trading_mode=TradingModeType.SIMULATE,
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=current_price,
            filled_price=current_price,
            status=TradeStatus.FILLED,
            fee=fee,
            strategy_id=strategy_id,
            ai_signal_id=ai_signal_id,
            opened_at=datetime.now(timezone.utc),
        )
        db.add(trade)

        # Update portfolio stats
        portfolio.total_trades += 1
        portfolio.total_pnl = float(portfolio.current_balance) - float(portfolio.initial_balance)

        await db.flush()
        await db.refresh(trade)
        return trade

    @staticmethod
    async def close_position(
        db: AsyncSession, user_id: UUID, trade_id: UUID
    ) -> Trade:
        """Close an open simulated position by placing the opposite order."""
        result = await db.execute(
            select(Trade).where(
                Trade.id == trade_id,
                Trade.user_id == user_id,
                Trade.trading_mode == TradingModeType.SIMULATE,
            )
        )
        trade = result.scalar_one_or_none()
        if not trade:
            raise ValueError("Trade not found")
        if trade.closed_at is not None:
            raise ValueError("Trade already closed")

        # Get current price
        try:
            ticker = await MarketService.get_ticker(trade.symbol)
            close_price = ticker["last"]
        except Exception:
            raise ValueError(f"Cannot get market price for {trade.symbol}")

        portfolio = await SimulateEngine.get_or_create_portfolio(db, user_id)

        # Calculate PnL
        if trade.side == TradeSide.BUY:
            pnl = (close_price - float(trade.filled_price)) * float(trade.quantity)
        else:
            pnl = (float(trade.filled_price) - close_price) * float(trade.quantity)

        close_fee = float(trade.quantity) * close_price * settings.BINANCE_FEE_RATE
        pnl -= close_fee

        # Update trade
        trade.pnl = pnl
        trade.pnl_percentage = (pnl / (float(trade.filled_price) * float(trade.quantity))) * 100
        trade.fee = float(trade.fee or 0) + close_fee
        trade.closed_at = datetime.now(timezone.utc)
        trade.status = TradeStatus.FILLED

        # Update portfolio
        if trade.side == TradeSide.BUY:
            portfolio.current_balance = float(portfolio.current_balance) + (
                float(trade.quantity) * close_price - close_fee
            )
        else:
            portfolio.current_balance = float(portfolio.current_balance) - (
                float(trade.quantity) * close_price + close_fee
            )

        if pnl > 0:
            portfolio.win_count += 1
        else:
            portfolio.loss_count += 1

        portfolio.total_pnl = float(portfolio.current_balance) - float(portfolio.initial_balance)

        await db.flush()
        await db.refresh(trade)
        return trade

    @staticmethod
    async def reset_portfolio(db: AsyncSession, user_id: UUID) -> SimulatePortfolio:
        """Reset simulate portfolio to initial balance."""
        portfolio = await SimulateEngine.get_or_create_portfolio(db, user_id)
        portfolio.current_balance = settings.DEFAULT_SIMULATE_BALANCE
        portfolio.initial_balance = settings.DEFAULT_SIMULATE_BALANCE
        portfolio.total_pnl = 0.0
        portfolio.total_trades = 0
        portfolio.win_count = 0
        portfolio.loss_count = 0
        await db.flush()
        await db.refresh(portfolio)
        return portfolio
