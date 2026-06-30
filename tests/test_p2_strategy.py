"""
P2 Strategy Tests - Unit tests for QuantStrategy engine and backtester.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from types import SimpleNamespace
from pydantic import BaseModel

from app.modules.strategy.engine import QuantStrategyEngine, StrategySignal
from app.modules.analysis.backtester import StrategyBacktester, BacktestTrade, BacktestResult


class TestQuantStrategyEngine:
    """Test QuantStrategyEngine functionality."""

    def test_confidence_threshold_uses_distance_from_neutral(self):
        """A 75 score with min_confidence=65 is 25 points from neutral and should trade."""
        strategy = {
            "momentum_weight": 1.0,
            "contrarian_weight": 0.0,
            "macd_weight": 0.0,
            "bollinger_band_weight": 0.0,
            "risk_level": "medium",
            "min_confidence_threshold": 65,
            "max_position_size": 1000,
        }
        signals = {
            "momentum": {"strength": 75, "confidence": 80},
        }

        result = QuantStrategyEngine.apply_strategy(
            strategy=strategy,
            signals=signals,
            current_price=Decimal("100"),
        )

        assert abs(result.composite_score - 50) == 25
        assert result.action == "STRONG_BUY"
        assert result.action != "HOLD"

    def test_apply_strategy_accepts_pydantic_strategy_config(self):
        """Strategy access is normalized before engine code reads fields."""

        class StrategyConfig(BaseModel):
            momentum_weight: float = 1.0
            contrarian_weight: float = 0.0
            macd_weight: float = 0.0
            bollinger_band_weight: float = 0.0
            risk_level: str = "medium"
            min_confidence_threshold: int = 65
            max_position_size: float = 1000
            stop_loss_percent: float = 2.5
            take_profit_percent: float = 5.0

        result = QuantStrategyEngine.apply_strategy(
            strategy=StrategyConfig(),
            signals={"momentum": {"strength": 75, "confidence": 80}},
            current_price=Decimal("100"),
        )

        assert result.action == "STRONG_BUY"
        assert result.stop_loss == Decimal("97.5")
        assert result.take_profit == Decimal("105.0")

    def test_validate_weights_valid(self):
        """Test weight validation with valid weights."""
        assert QuantStrategyEngine.validate_weights(0.25, 0.20, 0.25, 0.30) is True
        assert QuantStrategyEngine.validate_weights(0.5, 0.2, 0.15, 0.15) is True

    def test_validate_weights_invalid(self):
        """Test weight validation with invalid weights."""
        assert QuantStrategyEngine.validate_weights(0.25, 0.25, 0.25, 0.26) is False  # Sum = 1.01 (exceeds tolerance)
        assert QuantStrategyEngine.validate_weights(0.5, 0.5, 0.5, 0.5) is False  # Sum = 2.0

    def test_apply_strategy_all_buy_signals(self):
        """Test strategy with all BUY signals."""
        strategy = {
            "momentum_weight": 0.25,
            "contrarian_weight": 0.20,
            "macd_weight": 0.25,
            "bollinger_band_weight": 0.30,
            "risk_level": "medium",
            "min_confidence_threshold": 65,
            "max_position_size": 1000,
            "stop_loss_percent": 2.5,
            "take_profit_percent": 5.0,
        }

        signals = {
            "momentum": {"strength": 80, "confidence": 85},
            "contrarian": {"strength": 75, "confidence": 80},
            "macd": {"strength": 85, "confidence": 90},
            "bollinger_band": {"strength": 80, "confidence": 85},
        }

        result = QuantStrategyEngine.apply_strategy(
            strategy=strategy,
            signals=signals,
            current_price=Decimal("100"),
        )

        assert isinstance(result, StrategySignal)
        assert result.action == "STRONG_BUY"
        assert result.composite_score > 70
        assert result.position_size > 0
        assert result.stop_loss == Decimal("97.5")  # 100 * (1 - 0.025)
        assert result.take_profit == Decimal("105.0")  # 100 * (1 + 0.05)

    def test_apply_strategy_all_sell_signals(self):
        """Test strategy with all SELL signals."""
        strategy = {
            "momentum_weight": 0.25,
            "contrarian_weight": 0.20,
            "macd_weight": 0.25,
            "bollinger_band_weight": 0.30,
            "risk_level": "medium",
            "min_confidence_threshold": 65,
            "max_position_size": 1000,
            "stop_loss_percent": 2.5,
            "take_profit_percent": 5.0,
        }

        signals = {
            "momentum": {"strength": 20, "confidence": 85},
            "contrarian": {"strength": 25, "confidence": 80},
            "macd": {"strength": 15, "confidence": 90},
            "bollinger_band": {"strength": 20, "confidence": 85},
        }

        result = QuantStrategyEngine.apply_strategy(
            strategy=strategy,
            signals=signals,
            current_price=Decimal("100"),
        )

        assert result.action == "STRONG_SELL"
        assert result.composite_score < 30
        assert result.position_size > 0

    def test_apply_strategy_mixed_signals(self):
        """Test strategy with mixed signals (some BUY, some SELL)."""
        strategy = {
            "momentum_weight": 0.25,
            "contrarian_weight": 0.20,
            "macd_weight": 0.25,
            "bollinger_band_weight": 0.30,
            "risk_level": "medium",
            "min_confidence_threshold": 65,
            "max_position_size": 1000,
            "stop_loss_percent": 2.5,
            "take_profit_percent": 5.0,
        }

        signals = {
            "momentum": {"strength": 75, "confidence": 85},
            "contrarian": {"strength": 35, "confidence": 80},
            "macd": {"strength": 65, "confidence": 90},
            "bollinger_band": {"strength": 50, "confidence": 85},
        }

        result = QuantStrategyEngine.apply_strategy(
            strategy=strategy,
            signals=signals,
            current_price=Decimal("100"),
        )

        assert result.action == "HOLD"  # Mixed signals
        assert 40 < result.composite_score < 60

    def test_apply_strategy_below_confidence_threshold(self):
        """Test strategy with signals below confidence threshold."""
        strategy = {
            "momentum_weight": 0.25,
            "contrarian_weight": 0.20,
            "macd_weight": 0.25,
            "bollinger_band_weight": 0.30,
            "risk_level": "medium",
            "min_confidence_threshold": 85,  # High threshold
            "max_position_size": 1000,
            "stop_loss_percent": 2.5,
            "take_profit_percent": 5.0,
        }

        signals = {
            "momentum": {"strength": 60, "confidence": 80},
            "contrarian": {"strength": 55, "confidence": 75},
            "macd": {"strength": 62, "confidence": 80},
            "bollinger_band": {"strength": 58, "confidence": 75},
        }

        result = QuantStrategyEngine.apply_strategy(
            strategy=strategy,
            signals=signals,
            current_price=Decimal("100"),
        )

        assert result.action == "HOLD"  # Below threshold

    def test_apply_strategy_different_risk_levels(self):
        """Test position sizing with different risk levels."""
        base_signals = {
            "momentum": {"strength": 75, "confidence": 85},
            "contrarian": {"strength": 70, "confidence": 80},
            "macd": {"strength": 80, "confidence": 90},
            "bollinger_band": {"strength": 75, "confidence": 85},
        }

        positions = {}
        for risk_level in ["low", "medium", "high"]:
            strategy = {
                "momentum_weight": 0.25,
                "contrarian_weight": 0.20,
                "macd_weight": 0.25,
                "bollinger_band_weight": 0.30,
                "risk_level": risk_level,
                "min_confidence_threshold": 65,
                "max_position_size": 1000,
                "stop_loss_percent": 2.5,
                "take_profit_percent": 5.0,
            }

            result = QuantStrategyEngine.apply_strategy(
                strategy=strategy,
                signals=base_signals,
                current_price=Decimal("100"),
            )
            positions[risk_level] = result.position_size

        # High risk should have largest position
        assert positions["high"] > positions["medium"] > positions["low"]

    def test_determine_action(self):
        """Test action determination based on composite score."""
        assert QuantStrategyEngine._determine_action(85) == "STRONG_BUY"
        assert QuantStrategyEngine._determine_action(65) == "BUY"
        assert QuantStrategyEngine._determine_action(50) == "HOLD"
        assert QuantStrategyEngine._determine_action(35) == "SELL"
        assert QuantStrategyEngine._determine_action(15) == "STRONG_SELL"

    def test_calculate_position_size(self):
        """Test position size calculation."""
        risk_config = QuantStrategyEngine.RISK_LEVEL_CONFIG["medium"]

        size = QuantStrategyEngine._calculate_position_size(
            action="BUY",
            confidence=75,
            max_size=Decimal("1000"),
            risk_level="medium",
            risk_config=risk_config,
        )

        assert size > 0
        assert size <= Decimal("1000")

        # HOLD should return 0
        hold_size = QuantStrategyEngine._calculate_position_size(
            action="HOLD",
            confidence=50,
            max_size=Decimal("1000"),
            risk_level="medium",
            risk_config=risk_config,
        )
        assert hold_size == Decimal("0")


class TestStrategyBacktester:
    """Test StrategyBacktester functionality."""

    def test_empty_backtest_result(self):
        """Test empty backtest result."""
        result = StrategyBacktester._empty_result()

        assert result.total_trades == 0
        assert result.winning_trades == 0
        assert result.losing_trades == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.max_drawdown == 0.0
        assert result.total_return == 0.0

    def test_calculate_single_winning_trade(self):
        """Test P&L calculation for winning trade.

        quantity=1000 means $1000 position value
        10% return = 1000 * 0.10 = $100 P&L
        """
        trade = StrategyBacktester._calculate_trade(
            entry_price=Decimal("100"),
            exit_price=Decimal("110"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            quantity=Decimal("1000"),  # Position value in quote currency
            signal_type="BUY",
        )

        assert trade.pnl == Decimal("100")  # 1000 * (110-100)/100 = 100
        assert trade.pnl_pct == 10.0
        assert trade.is_win is True

    def test_calculate_single_losing_trade(self):
        """Test P&L calculation for losing trade.

        quantity=1000 means $1000 position value
        -5% return = 1000 * (-0.05) = -$50 P&L
        """
        trade = StrategyBacktester._calculate_trade(
            entry_price=Decimal("100"),
            exit_price=Decimal("95"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            quantity=Decimal("1000"),  # Position value in quote currency
            signal_type="BUY",
        )

        assert trade.pnl == Decimal("-50")  # 1000 * (95-100)/100 = -50
        assert trade.pnl_pct == -5.0
        assert trade.is_win is False

    def test_calculate_metrics_winning_trades(self):
        """Test metrics calculation with winning trades."""
        trades = [
            BacktestTrade(
                entry_price=Decimal("100"),
                exit_price=Decimal("110"),
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                quantity=Decimal("10"),
                pnl=Decimal("100"),
                pnl_pct=10.0,
                signal_type="BUY",
                is_win=True,
            ),
            BacktestTrade(
                entry_price=Decimal("110"),
                exit_price=Decimal("115"),
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                quantity=Decimal("10"),
                pnl=Decimal("50"),
                pnl_pct=4.5,
                signal_type="BUY",
                is_win=True,
            ),
        ]

        metrics = StrategyBacktester._calculate_metrics(trades)

        assert metrics["total_trades"] == 2
        assert metrics["winning_trades"] == 2
        assert metrics["losing_trades"] == 0
        assert metrics["win_rate"] == 100.0
        assert metrics["sharpe_ratio"] > 0

    def test_calculate_metrics_total_return_uses_initial_capital(self):
        """A $250 gain on $5000 capital is 5%, not the legacy pnl/100 value."""
        trades = [
            BacktestTrade(
                entry_price=Decimal("100"),
                exit_price=Decimal("105"),
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                quantity=Decimal("5000"),
                pnl=Decimal("250"),
                pnl_pct=5.0,
                signal_type="BUY",
                is_win=True,
            ),
        ]

        metrics = StrategyBacktester._calculate_metrics(
            trades,
            initial_capital=Decimal("5000"),
        )

        assert metrics["total_return"] == 5.0
        assert metrics["total_return"] != 2.5

    def test_calculate_metrics_with_losses(self):
        """Test metrics calculation with mixed trades."""
        trades = [
            BacktestTrade(
                entry_price=Decimal("100"),
                exit_price=Decimal("110"),
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                quantity=Decimal("10"),
                pnl=Decimal("100"),
                pnl_pct=10.0,
                signal_type="BUY",
                is_win=True,
            ),
            BacktestTrade(
                entry_price=Decimal("110"),
                exit_price=Decimal("100"),
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                quantity=Decimal("10"),
                pnl=Decimal("-100"),
                pnl_pct=-9.1,
                signal_type="BUY",
                is_win=False,
            ),
        ]

        metrics = StrategyBacktester._calculate_metrics(trades)

        assert metrics["total_trades"] == 2
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 1
        assert metrics["win_rate"] == 50.0
        assert metrics["profit_factor"] == 1.0  # Equal wins and losses

    def test_position_size_from_strategy(self):
        """Test position size extraction from strategy as absolute quote currency.

        With 5% of 10000 initial capital = 500 in quote currency
        """
        strategy = {
            "position_sizing": {
                "type": "fixed_percentage",
                "value": 5.0,
            }
        }

        size = StrategyBacktester._position_size_from_strategy(
            strategy,
            available_capital=Decimal("10000")
        )
        assert size == Decimal("500.0")  # 5% of 10000

    def test_percentage_position_size_drives_currency_pnl(self):
        """5% of $10000 opens a $500 position, so a 10% price move earns $50."""
        strategy = {
            "position_sizing": {
                "type": "fixed_percentage",
                "value": 5.0,
            }
        }

        position_size = StrategyBacktester._position_size_from_strategy(
            strategy,
            available_capital=Decimal("10000"),
        )
        trade = StrategyBacktester._calculate_trade(
            entry_price=Decimal("200"),
            exit_price=Decimal("220"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            quantity=position_size,
            signal_type="BUY",
        )

        assert position_size == Decimal("500.0")
        assert trade.pnl == Decimal("50.00")
        assert trade.pnl != Decimal("100")

    def test_stop_loss_closes_before_later_sell_signal(self):
        """A later SELL cannot override a stop loss hit on the next candle."""
        start = datetime(2026, 1, 1, 0, 0, 0)
        strategy = {
            "position_sizing": {"type": "fixed_percentage", "value": 10.0},
            "stop_loss_percent": 5.0,
            "take_profit_percent": 10.0,
        }
        signals = [
            SimpleNamespace(
                signal_type="BUY",
                price=Decimal("100"),
                created_at=start,
            ),
            SimpleNamespace(
                signal_type="SELL",
                price=Decimal("120"),
                created_at=start + timedelta(minutes=2),
            ),
        ]
        candles = [
            SimpleNamespace(
                close=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                created_at=start,
            ),
            SimpleNamespace(
                close=Decimal("96"),
                high=Decimal("97"),
                low=Decimal("94"),
                created_at=start + timedelta(minutes=1),
            ),
            SimpleNamespace(
                close=Decimal("120"),
                high=Decimal("121"),
                low=Decimal("119"),
                created_at=start + timedelta(minutes=2),
            ),
        ]

        trades = StrategyBacktester._simulate_trades(
            historical_signals=signals,
            historical_candles=candles,
            strategy=strategy,
            initial_capital=Decimal("10000"),
        )

        assert len(trades) == 1
        assert trades[0].exit_price == Decimal("95.0")
        assert trades[0].exit_time == start + timedelta(minutes=1)
        assert trades[0].pnl == Decimal("-50.00")

    def test_take_profit_closes_before_later_sell_signal(self):
        """A take profit hit closes at the configured level before later signals."""
        start = datetime(2026, 1, 1, 0, 0, 0)
        strategy = {
            "position_sizing": {"type": "fixed_percentage", "value": 10.0},
            "stop_loss_percent": 5.0,
            "take_profit_percent": 10.0,
        }
        signals = [
            SimpleNamespace(
                signal_type="BUY",
                price=Decimal("100"),
                created_at=start,
            ),
            SimpleNamespace(
                signal_type="SELL",
                price=Decimal("120"),
                created_at=start + timedelta(minutes=2),
            ),
        ]
        candles = [
            SimpleNamespace(
                close=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                created_at=start,
            ),
            SimpleNamespace(
                close=Decimal("109"),
                high=Decimal("111"),
                low=Decimal("99"),
                created_at=start + timedelta(minutes=1),
            ),
            SimpleNamespace(
                close=Decimal("120"),
                high=Decimal("121"),
                low=Decimal("119"),
                created_at=start + timedelta(minutes=2),
            ),
        ]

        trades = StrategyBacktester._simulate_trades(
            historical_signals=signals,
            historical_candles=candles,
            strategy=strategy,
            initial_capital=Decimal("10000"),
        )

        assert len(trades) == 1
        assert trades[0].exit_price == Decimal("110.0")
        assert trades[0].exit_time == start + timedelta(minutes=1)
        assert trades[0].pnl == Decimal("100.00")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
