"""
Strategy Backtester - evaluates strategy performance against historical signals.
P2 Implementation: Historical signal analysis and performance metrics.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.market_data.models import OHLCVCandle
from app.modules.trading.models import TradingSignal

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Record of a trade from backtest."""
    entry_price: Decimal
    exit_price: Decimal
    entry_time: datetime
    exit_time: datetime
    quantity: Decimal
    pnl: Decimal  # Profit/Loss
    pnl_pct: float  # Profit/Loss %
    signal_type: str  # BUY, SELL
    is_win: bool


@dataclass
class BacktestResult:
    """Complete backtest result with metrics."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float  # %
    profit_factor: float  # Total Wins / Total Losses
    sharpe_ratio: float  # Risk-adjusted return
    max_drawdown: float  # % decline
    total_return: float  # % total return
    trades: List[BacktestTrade]


class StrategyBacktester:
    """
    Backtest strategy performance against historical signal data.

    Evaluates how a strategy would have performed in the past based on:
    - Historical signals that were generated
    - Entry/exit prices based on market data
    - Risk parameters (stop loss, take profit)
    """

    @staticmethod
    async def backtest_strategy(
        db: AsyncSession,
        watchlist_id: str,
        symbol: str,
        strategy: Mapping[str, Any],
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal = Decimal("10000"),
    ) -> BacktestResult:
        """
        Backtest strategy against historical signals.

        Args:
            db: Database session
            watchlist_id: Watchlist ID
            symbol: Trading symbol (e.g., BTCUSDT)
            strategy: Strategy configuration with weights and risk params
            start_date: Backtest start date
            end_date: Backtest end date
            initial_capital: Starting capital for return calculation (default 10000)

        Returns:
            BacktestResult with all metrics
        """

        logger.info(
            f"Starting backtest for {symbol} from {start_date} to {end_date}"
        )

        # 1. Get historical signals and OHLCV candles for date range
        signal_stmt = (
            select(TradingSignal)
            .where(
                TradingSignal.watchlist_id == watchlist_id,
                TradingSignal.symbol == symbol,
                TradingSignal.signal_timestamp >= start_date,
                TradingSignal.signal_timestamp <= end_date,
            )
            .order_by(TradingSignal.signal_timestamp)
        )
        signal_result = await db.execute(signal_stmt)
        historical_signals = list(signal_result.scalars().all())

        # Get OHLCV candles for SL/TP checking
        candle_stmt = (
            select(OHLCVCandle)
            .where(
                OHLCVCandle.symbol == symbol,
                OHLCVCandle.open_time >= start_date,
                OHLCVCandle.open_time <= end_date,
            )
            .order_by(OHLCVCandle.open_time)
        )
        candle_result = await db.execute(candle_stmt)
        historical_candles = list(candle_result.scalars().all())

        if not historical_signals:
            logger.warning(f"No historical signals found for {symbol} in date range")
            return StrategyBacktester._empty_result()

        # 2. Simulate trades based on signals and OHLCV candles
        trades = StrategyBacktester._simulate_trades(
            historical_signals=historical_signals,
            historical_candles=historical_candles,
            strategy=strategy,
            initial_capital=initial_capital,
        )

        # 3. Calculate metrics
        metrics = StrategyBacktester._calculate_metrics(trades, initial_capital)

        logger.info(
            f"Backtest complete: {metrics['total_trades']} trades, "
            f"win_rate={metrics['win_rate']:.1f}%, "
            f"sharpe_ratio={metrics['sharpe_ratio']:.2f}"
        )

        return BacktestResult(
            total_trades=metrics["total_trades"],
            winning_trades=metrics["winning_trades"],
            losing_trades=metrics["losing_trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown=metrics["max_drawdown"],
            total_return=metrics["total_return"],
            trades=trades,
        )

    @staticmethod
    def _simulate_trades(
        historical_signals: List[Any],
        historical_candles: List[Any],
        strategy: Mapping[str, Any],
        initial_capital: Decimal = Decimal("10000"),
    ) -> List[BacktestTrade]:
        """Simulate trades with percentage sizing and candle-level SL/TP exits.

        Args:
            historical_signals: Chronologically ordered BUY/SELL signal records
            historical_candles: Chronologically ordered OHLCV candle records
            strategy: Strategy configuration with position sizing and risk params
            initial_capital: Starting capital used for percentage position sizing

        Returns:
            List of completed backtest trades
        """

        trades: List[BacktestTrade] = []
        open_position = None
        signal_idx = 0
        available_capital = Decimal(str(initial_capital))

        for candle in historical_candles:
            candle_time = StrategyBacktester._time_value(candle)

            # SL/TP is evaluated on candles after entry; stop loss wins if both
            # bounds are touched inside the same candle because intrabar order is unknown.
            if open_position and candle_time > open_position["entry_candle_time"]:
                candle_high = StrategyBacktester._decimal_value(
                    candle,
                    "high",
                    "high_price",
                    "close",
                    "close_price",
                    default=open_position["entry_price"],
                )
                candle_low = StrategyBacktester._decimal_value(
                    candle,
                    "low",
                    "low_price",
                    "close",
                    "close_price",
                    default=open_position["entry_price"],
                )

                if candle_low <= open_position["stop_loss"]:
                    trade = StrategyBacktester._calculate_trade(
                        entry_price=open_position["entry_price"],
                        exit_price=open_position["stop_loss"],
                        entry_time=open_position["entry_time"],
                        exit_time=candle_time,
                        quantity=open_position["position_size_currency"],
                        signal_type=open_position["signal_type"],
                    )
                    trades.append(trade)
                    available_capital += trade.pnl
                    logger.debug(f"Closed position on STOP LOSS hit, P&L={trade.pnl}")
                    open_position = None

                elif candle_high >= open_position["take_profit"]:
                    trade = StrategyBacktester._calculate_trade(
                        entry_price=open_position["entry_price"],
                        exit_price=open_position["take_profit"],
                        entry_time=open_position["entry_time"],
                        exit_time=candle_time,
                        quantity=open_position["position_size_currency"],
                        signal_type=open_position["signal_type"],
                    )
                    trades.append(trade)
                    available_capital += trade.pnl
                    logger.debug(f"Closed position on TAKE PROFIT hit, P&L={trade.pnl}")
                    open_position = None

            # Check for entry signals at this candle
            while signal_idx < len(historical_signals):
                signal = historical_signals[signal_idx]
                signal_time = StrategyBacktester._time_value(signal)
                if signal_time > candle_time:
                    break

                if signal.signal_type in ["STRONG_BUY", "BUY"] and not open_position:
                    # Open long position
                    entry_price = StrategyBacktester._signal_price(signal, candle)
                    position_size_currency = StrategyBacktester._position_size_from_strategy(
                        strategy,
                        available_capital,
                    )
                    open_position = {
                        "entry_price": entry_price,
                        "entry_time": signal_time,
                        "entry_candle_time": candle_time,
                        "signal_type": "BUY",
                        "position_size_currency": position_size_currency,
                        "stop_loss_pct": StrategyBacktester._strategy_decimal(
                            strategy,
                            "stop_loss_percent",
                            "stop_loss_pct",
                            default=Decimal("2.5"),
                        ),
                        "take_profit_pct": StrategyBacktester._strategy_decimal(
                            strategy,
                            "take_profit_percent",
                            "take_profit_pct",
                            default=Decimal("5.0"),
                        ),
                    }
                    # Calculate SL/TP prices
                    open_position["stop_loss"] = entry_price * (Decimal("1") - open_position["stop_loss_pct"] / Decimal("100"))
                    open_position["take_profit"] = entry_price * (Decimal("1") + open_position["take_profit_pct"] / Decimal("100"))
                    logger.debug(f"Opened position at {entry_price}, SL={open_position['stop_loss']}, TP={open_position['take_profit']}")

                elif signal.signal_type in ["STRONG_SELL", "SELL"] and open_position:
                    # Close position on sell signal
                    exit_price = StrategyBacktester._signal_price(signal, candle)
                    trade = StrategyBacktester._calculate_trade(
                        entry_price=open_position["entry_price"],
                        exit_price=exit_price,
                        entry_time=open_position["entry_time"],
                        exit_time=signal_time,
                        quantity=open_position["position_size_currency"],
                        signal_type=open_position["signal_type"],
                    )
                    trades.append(trade)
                    available_capital += trade.pnl
                    logger.debug(f"Closed position on SELL signal, P&L={trade.pnl}")
                    open_position = None

                signal_idx += 1

        # Close any remaining open position at the end of the backtest
        if open_position and historical_candles:
            last_candle = historical_candles[-1]
            exit_price = StrategyBacktester._decimal_value(
                last_candle,
                "close",
                "close_price",
                default=Decimal("0"),
            )
            trade = StrategyBacktester._calculate_trade(
                entry_price=open_position["entry_price"],
                exit_price=exit_price,
                entry_time=open_position["entry_time"],
                exit_time=StrategyBacktester._time_value(last_candle),
                quantity=open_position["position_size_currency"],
                signal_type=open_position["signal_type"],
            )
            trades.append(trade)
            logger.info(f"Closed remaining position at end of backtest, P&L={trade.pnl}")

        return trades

    @staticmethod
    def _empty_result() -> BacktestResult:
        """Return empty backtest result."""
        return BacktestResult(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            total_return=0.0,
            trades=[],
        )

    @staticmethod
    def _position_size_from_strategy(strategy: Mapping[str, Any], available_capital: Decimal = Decimal("10000")) -> Decimal:
        """Extract position size from strategy config as absolute quote-currency amount.

        Args:
            strategy: Strategy configuration
            available_capital: Capital available at entry for percentage-based sizes

        Returns:
            Position size in quote currency (absolute value, not percentage)
        """
        sizing = strategy.get("position_sizing", {})
        if sizing.get("type") == "fixed_percentage":
            # Convert percentage to absolute value
            percentage = Decimal(str(sizing.get("value", 5.0)))
            return Decimal(str(available_capital)) * percentage / Decimal("100")
        # Use max_position_size if configured, else max_positions as count
        if "max_position_size" in strategy:
            return Decimal(str(strategy.get("max_position_size", 1000)))
        return Decimal(str(strategy.get("max_positions", 1)))

    @staticmethod
    def _calculate_trade(
        entry_price: Decimal,
        exit_price: Decimal,
        entry_time: datetime,
        exit_time: datetime,
        quantity: Decimal,
        signal_type: str,
    ) -> BacktestTrade:
        """Calculate single trade P&L.

        Args:
            entry_price: Entry price in quote currency
            exit_price: Exit price in quote currency
            entry_time: Entry timestamp
            exit_time: Exit timestamp
            quantity: Position size in quote currency (absolute amount)
            signal_type: BUY or SELL
        """

        entry_price = Decimal(str(entry_price))
        exit_price = Decimal(str(exit_price))
        quantity = Decimal(str(quantity))

        if entry_price > 0:
            # Calculate return percentage on price movement
            price_return = (exit_price - entry_price) / entry_price
            # P&L is position value * price return
            pnl = quantity * price_return
            pnl_pct = float(price_return * Decimal("100"))
        else:
            pnl = Decimal("0")
            pnl_pct = 0.0

        is_win = pnl > 0

        return BacktestTrade(
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            quantity=quantity,
            pnl=pnl,
            pnl_pct=float(pnl_pct),
            signal_type=signal_type,
            is_win=is_win,
        )

    @staticmethod
    def _calculate_metrics(trades: List[BacktestTrade], initial_capital: Decimal = Decimal("10000")) -> Dict:
        """Calculate performance metrics from trades.

        Args:
            trades: List of BacktestTrade objects
            initial_capital: Starting capital for return calculation
        """

        initial_capital = Decimal(str(initial_capital))

        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "total_return": 0.0,
            }

        # Count trades
        winning_trades = [t for t in trades if t.is_win]
        losing_trades = [t for t in trades if not t.is_win]
        total_trades = len(trades)

        # Win rate
        win_rate = (
            Decimal(len(winning_trades)) / Decimal(total_trades) * Decimal("100")
            if total_trades > 0
            else Decimal("0")
        )

        # Total P&L
        total_pnl = sum((Decimal(str(t.pnl)) for t in trades), Decimal("0"))
        total_wins = sum((Decimal(str(t.pnl)) for t in winning_trades), Decimal("0"))
        total_losses = abs(sum((Decimal(str(t.pnl)) for t in losing_trades), Decimal("0")))

        # Profit factor
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0

        # Sharpe ratio (risk-adjusted return)
        pnl_list = [Decimal(str(t.pnl_pct)) for t in trades]
        if pnl_list:
            avg_pnl = sum(pnl_list, Decimal("0")) / Decimal(len(pnl_list))
            if len(pnl_list) > 1:
                variance = sum(
                    ((pnl_pct - avg_pnl) ** 2 for pnl_pct in pnl_list),
                    Decimal("0"),
                ) / Decimal(len(pnl_list) - 1)
                std_dev = variance.sqrt()
                sharpe_ratio = (avg_pnl / std_dev) if std_dev > 0 else Decimal("0")
            else:
                sharpe_ratio = avg_pnl
        else:
            sharpe_ratio = Decimal("0")

        # Max drawdown
        cumulative_pnl = Decimal("0")
        peak_pnl = Decimal("0")
        max_dd = Decimal("0")
        for trade in trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            if peak_pnl > 0:
                drawdown = (peak_pnl - cumulative_pnl) / peak_pnl
                max_dd = max(max_dd, drawdown)

        # Total return % (return on initial capital)
        if initial_capital > 0:
            total_return = float((total_pnl / initial_capital) * Decimal("100"))
        else:
            total_return = 0.0

        return {
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "sharpe_ratio": float(sharpe_ratio),
            "max_drawdown": float(max_dd) * 100,
            "total_return": total_return,
        }

    @staticmethod
    def _strategy_decimal(
        strategy: Mapping[str, Any],
        primary_key: str,
        alias_key: str,
        default: Decimal,
    ) -> Decimal:
        """Read a Decimal strategy value while supporting legacy and schema field names."""
        value = strategy.get(primary_key, strategy.get(alias_key, default))
        return Decimal(str(value))

    @staticmethod
    def _time_value(record: Any) -> datetime:
        """Extract the chronological timestamp used by backtest simulation."""
        for name in ("signal_timestamp", "open_time", "timestamp", "created_at"):
            value = getattr(record, name, None)
            if value is not None:
                return value
        raise ValueError("Backtest record does not expose a usable timestamp")

    @staticmethod
    def _decimal_value(record: Any, *names: str, default: Decimal) -> Decimal:
        """Extract the first available Decimal price from a record."""
        for name in names:
            value = getattr(record, name, None)
            if value is not None:
                return Decimal(str(value))
        return Decimal(str(default))

    @staticmethod
    def _signal_price(signal: Any, candle: Any) -> Decimal:
        """Return signal price when present, otherwise the candle close price."""
        signal_price = getattr(signal, "price", None)
        if signal_price is not None:
            return Decimal(str(signal_price))
        return StrategyBacktester._decimal_value(
            candle,
            "close",
            "close_price",
            default=Decimal("0"),
        )
