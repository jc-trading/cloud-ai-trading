"""Portfolio risk tracker - P3 Phase 3B."""

import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional
from statistics import stdev, mean

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from .models import PositionMetric, DrawdownRecord, RiskLimit
from app.modules.trading.models import Position, PortfolioStats

logger = logging.getLogger(__name__)


class PortfolioRiskTracker:
    """Track and calculate portfolio risk metrics in real-time."""

    @staticmethod
    async def update_position_metrics(
        session: AsyncSession,
        position_id: str,
        current_price: Decimal,
    ) -> PositionMetric:
        """
        Update metrics for a single position.

        Args:
            session: Database session
            position_id: Position ID
            current_price: Current market price

        Returns:
            Updated PositionMetric
        """
        # Get position
        stmt = select(Position).where(Position.id == position_id)
        result = await session.execute(stmt)
        position = result.scalar_one_or_none()

        if not position or position.status != "open":
            raise ValueError(f"Position {position_id} not found or closed")

        # Calculate metrics
        entry_price = Decimal(position.entry_price)
        quantity = Decimal(position.quantity)
        position_value = quantity

        # P&L calculation
        price_return = (current_price - entry_price) / entry_price
        current_pnl = position_value * price_return
        pnl_percent = float(price_return * Decimal("100"))

        # Max Favorable/Adverse Excursion
        # Get existing metric to check MFE/MAE history
        stmt = select(PositionMetric).where(
            PositionMetric.position_id == position_id
        ).order_by(PositionMetric.recorded_at.desc()).limit(1)
        result = await session.execute(stmt)
        prev_metric = result.scalar_one_or_none()

        if prev_metric and position.position_type == "LONG":
            # For LONG: MFE is highest price, MAE is lowest price
            max_fav = max(prev_metric.max_favorable_excursion, current_pnl)
            max_adv = min(prev_metric.max_adverse_excursion, current_pnl)
        elif prev_metric and position.position_type == "SHORT":
            # For SHORT: opposite
            max_fav = max(prev_metric.max_favorable_excursion, -current_pnl)
            max_adv = min(prev_metric.max_adverse_excursion, -current_pnl)
        else:
            max_fav = max(current_pnl, Decimal("0"))
            max_adv = min(current_pnl, Decimal("0"))

        # Days in trade
        days_in_trade = (datetime.now(timezone.utc) - position.entry_date).days

        # Position size as % of account (would need account_equity)
        # For now, store as None - will be calculated in portfolio tracker
        position_size_percent = None

        # Create or update metric
        metric = PositionMetric(
            position_id=position_id,
            current_pnl=current_pnl,
            pnl_percent=Decimal(str(pnl_percent)),
            max_favorable_excursion=max_fav,
            max_adverse_excursion=max_adv,
            current_price=current_price,
            days_in_trade=days_in_trade,
            position_size_percent=position_size_percent,
        )

        session.add(metric)
        await session.flush()

        logger.info(
            f"Position {position_id} metrics updated: "
            f"price=${current_price}, pnl=${current_pnl} ({pnl_percent:.2f}%)"
        )

        return metric

    @staticmethod
    async def calculate_portfolio_metrics(
        session: AsyncSession,
        watchlist_id: str,
        current_prices: Dict[str, Decimal],
        initial_capital: Decimal,
    ) -> Dict:
        """
        Calculate all portfolio-level risk metrics.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            current_prices: Dict of {symbol: current_price}
            initial_capital: Starting account equity

        Returns:
            Dict with all portfolio metrics
        """
        # Get all positions
        stmt = select(Position).where(Position.watchlist_id == watchlist_id)
        result = await session.execute(stmt)
        all_positions = result.scalars().all()

        open_positions = [p for p in all_positions if p.status == "open"]
        closed_positions = [p for p in all_positions if p.status == "closed"]

        # Calculate P&L
        unrealized_pnl = Decimal("0")
        for position in open_positions:
            current_price = current_prices.get(position.symbol, position.entry_price)
            price_return = (Decimal(current_price) - Decimal(position.entry_price)) / Decimal(position.entry_price)
            unrealized_pnl += Decimal(position.quantity) * price_return

        realized_pnl = Decimal("0")
        for position in closed_positions:
            if position.exit_price:
                realized_pnl += (Decimal(position.exit_price) - Decimal(position.entry_price)) * Decimal(position.quantity)

        total_pnl = realized_pnl + unrealized_pnl
        current_equity = initial_capital + total_pnl

        # Calculate drawdown
        max_drawdown_percent = Decimal("0")
        current_drawdown_percent = Decimal("0")

        if current_equity < initial_capital:
            current_drawdown_percent = ((initial_capital - current_equity) / initial_capital) * Decimal("100")

        # Get historical peak equity (would come from previous records)
        stmt = select(func.max(DrawdownRecord.peak_equity)).where(
            DrawdownRecord.watchlist_id == watchlist_id
        )
        result = await session.execute(stmt)
        peak_equity = result.scalar() or initial_capital
        peak_equity = Decimal(str(peak_equity))

        if current_equity < peak_equity:
            max_drawdown_percent = ((peak_equity - current_equity) / peak_equity) * Decimal("100")

        # Calculate win rate and profit factor
        winning_trades = len([p for p in closed_positions if p.realized_pnl > Decimal("0")])
        losing_trades = len([p for p in closed_positions if p.realized_pnl < Decimal("0")])
        total_trades = len(closed_positions)

        win_rate = (Decimal(winning_trades) / Decimal(total_trades) * Decimal("100")) if total_trades > 0 else Decimal("0")

        # Profit factor = sum(wins) / sum(losses)
        total_wins = sum([p.realized_pnl for p in closed_positions if p.realized_pnl > Decimal("0")])
        total_losses = abs(sum([p.realized_pnl for p in closed_positions if p.realized_pnl < Decimal("0")]))
        profit_factor = (total_wins / total_losses) if total_losses > Decimal("0") else Decimal("0")

        # Calculate Sharpe ratio (simplified)
        sharpe_ratio = await PortfolioRiskTracker._calculate_sharpe_ratio(closed_positions, initial_capital)

        # Calculate VaR (simplified)
        var_95 = await PortfolioRiskTracker._calculate_var(closed_positions, confidence=Decimal("0.95"))

        # Calculate concentration
        concentration_percent = Decimal("0")
        total_position_value = Decimal("0")
        max_position_value = Decimal("0")

        for position in open_positions:
            position_value = Decimal(position.quantity)
            total_position_value += position_value
            if position_value > max_position_value:
                max_position_value = position_value

        if total_position_value > Decimal("0"):
            concentration_percent = (max_position_value / total_position_value) * Decimal("100")

        return {
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": total_pnl,
            "current_equity": current_equity,
            "peak_equity": peak_equity,
            "max_drawdown_percent": float(max_drawdown_percent),
            "current_drawdown_percent": float(current_drawdown_percent),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "sharpe_ratio": float(sharpe_ratio),
            "var_95": var_95,
            "concentration_percent": float(concentration_percent),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "open_positions": len(open_positions),
        }

    @staticmethod
    async def record_drawdown(
        session: AsyncSession,
        watchlist_id: str,
        risk_limit_id: str,
        metrics: Dict,
    ) -> DrawdownRecord:
        """
        Record portfolio drawdown snapshot.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            risk_limit_id: Risk limit ID
            metrics: Portfolio metrics dict

        Returns:
            DrawdownRecord
        """
        record = DrawdownRecord(
            watchlist_id=watchlist_id,
            risk_limit_id=risk_limit_id,
            peak_equity=metrics["peak_equity"],
            trough_equity=metrics["current_equity"],
            current_equity=metrics["current_equity"],
            max_drawdown_percent=Decimal(str(metrics["max_drawdown_percent"])),
            current_drawdown_percent=Decimal(str(metrics["current_drawdown_percent"])),
            unrealized_pnl=metrics["unrealized_pnl"],
            realized_pnl=metrics["realized_pnl"],
            total_pnl=metrics["total_pnl"],
        )

        session.add(record)
        await session.flush()

        logger.info(
            f"Drawdown recorded for {watchlist_id}: "
            f"equity=${metrics['current_equity']}, "
            f"drawdown={metrics['current_drawdown_percent']:.2f}%"
        )

        return record

    @staticmethod
    async def _calculate_sharpe_ratio(
        positions: List[Position],
        initial_capital: Decimal,
        risk_free_rate: Decimal = Decimal("0.0001"),
    ) -> Decimal:
        """
        Calculate Sharpe ratio from trade returns.

        Args:
            positions: List of closed positions
            initial_capital: Initial account equity
            risk_free_rate: Risk-free rate (default 0.01%)

        Returns:
            Sharpe ratio (returns/volatility)
        """
        if len(positions) < 2:
            return Decimal("0")

        # Calculate returns per trade
        returns = []
        for position in positions:
            if position.exit_price and position.exit_price > 0:
                return_pct = ((Decimal(position.exit_price) - Decimal(position.entry_price)) /
                             Decimal(position.entry_price))
                returns.append(float(return_pct))

        if len(returns) < 2:
            return Decimal("0")

        try:
            avg_return = Decimal(str(mean(returns)))
            std_dev = Decimal(str(stdev(returns)))

            if std_dev > Decimal("0"):
                sharpe = (avg_return - risk_free_rate) / std_dev
                return sharpe
        except Exception as e:
            logger.warning(f"Sharpe calculation error: {e}")

        return Decimal("0")

    @staticmethod
    async def _calculate_var(
        positions: List[Position],
        confidence: Decimal = Decimal("0.95"),
    ) -> Decimal:
        """
        Calculate Value at Risk (VaR) at given confidence level.

        Args:
            positions: List of closed positions
            confidence: Confidence level (0.95 for 95%)

        Returns:
            VaR as Decimal
        """
        if len(positions) < 2:
            return Decimal("0")

        # Calculate P&L for each position
        pnls = []
        for position in positions:
            if position.exit_price:
                pnl = (Decimal(position.exit_price) - Decimal(position.entry_price)) * Decimal(position.quantity)
                pnls.append(float(pnl))

        if not pnls:
            return Decimal("0")

        # Simple VaR: use percentile method
        pnls_sorted = sorted(pnls)
        var_index = int(len(pnls_sorted) * (1 - float(confidence)))
        var_index = max(0, var_index)

        return Decimal(str(pnls_sorted[var_index]))

    @staticmethod
    async def update_portfolio_stats(
        session: AsyncSession,
        watchlist_id: str,
        metrics: Dict,
    ) -> PortfolioStats:
        """
        Update PortfolioStats record with latest metrics.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            metrics: Portfolio metrics dict

        Returns:
            Updated PortfolioStats
        """
        # Get or create portfolio stats
        stmt = select(PortfolioStats).where(PortfolioStats.watchlist_id == watchlist_id)
        result = await session.execute(stmt)
        stats = result.scalar_one_or_none()

        if not stats:
            stats = PortfolioStats(watchlist_id=watchlist_id)

        # Update metrics
        stats.current_value = metrics["current_equity"]
        stats.unrealized_pnl = metrics["unrealized_pnl"]
        stats.realized_pnl = metrics["realized_pnl"]
        stats.total_return_percent = (
            (metrics["total_pnl"] / Decimal("100000")) * Decimal("100") if metrics["total_pnl"] != 0 else Decimal("0")
        )
        stats.win_rate = Decimal(str(metrics["win_rate"]))
        stats.max_drawdown = Decimal(str(metrics["max_drawdown_percent"]))
        stats.total_trades = metrics["total_trades"]
        stats.winning_trades = metrics["winning_trades"]
        stats.losing_trades = metrics["losing_trades"]
        stats.last_updated = datetime.now(timezone.utc)

        session.add(stats)
        await session.flush()

        logger.info(
            f"Portfolio stats updated for {watchlist_id}: "
            f"pnl=${metrics['total_pnl']}, "
            f"win_rate={metrics['win_rate']:.1f}%"
        )

        return stats
