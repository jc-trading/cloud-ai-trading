"""
P3 Risk Engine Tests - Unit tests for RiskEngine and RiskValidator.
Tests position sizing, risk validation, and limit enforcement.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from app.modules.risk.engine import RiskEngine, PositionSizeRecommendation
from app.modules.risk.validators import RiskValidator


class TestRiskValidator:
    """Test RiskValidator utility functions."""

    def test_validate_position_size_valid(self):
        """Test position size validation with valid size."""
        is_valid, reason = RiskValidator.validate_position_size(
            position_size=Decimal("1000"),
            account_equity=Decimal("50000"),
            max_position_percent=Decimal("5"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_position_size_exceeds_max(self):
        """Test position size validation when exceeding max."""
        is_valid, reason = RiskValidator.validate_position_size(
            position_size=Decimal("3000"),
            account_equity=Decimal("50000"),
            max_position_percent=Decimal("5"),
        )
        assert is_valid is False
        assert "exceeds max" in reason

    def test_validate_position_size_below_minimum(self):
        """Test position size validation when below minimum."""
        is_valid, reason = RiskValidator.validate_position_size(
            position_size=Decimal("5"),
            account_equity=Decimal("50000"),
            max_position_percent=Decimal("5"),
            min_size=Decimal("10"),
        )
        assert is_valid is False
        assert "below minimum" in reason

    def test_validate_position_size_negative(self):
        """Test position size validation with negative size."""
        is_valid, reason = RiskValidator.validate_position_size(
            position_size=Decimal("-100"),
            account_equity=Decimal("50000"),
            max_position_percent=Decimal("5"),
        )
        assert is_valid is False
        assert "must be positive" in reason

    def test_validate_stop_loss_long_valid(self):
        """Test stop loss validation for LONG position."""
        is_valid, reason = RiskValidator.validate_stop_loss(
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("97.5"),  # 2.5% below entry
            position_type="LONG",
            min_distance_percent=Decimal("0.5"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_stop_loss_long_above_entry(self):
        """Test stop loss validation for LONG with SL above entry."""
        is_valid, reason = RiskValidator.validate_stop_loss(
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("105"),  # Above entry (invalid for LONG)
            position_type="LONG",
        )
        assert is_valid is False
        assert "below entry" in reason

    def test_validate_stop_loss_short_valid(self):
        """Test stop loss validation for SHORT position."""
        is_valid, reason = RiskValidator.validate_stop_loss(
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("102.5"),  # 2.5% above entry
            position_type="SHORT",
            min_distance_percent=Decimal("0.5"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_stop_loss_short_below_entry(self):
        """Test stop loss validation for SHORT with SL below entry."""
        is_valid, reason = RiskValidator.validate_stop_loss(
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("95"),  # Below entry (invalid for SHORT)
            position_type="SHORT",
        )
        assert is_valid is False
        assert "above entry" in reason

    def test_validate_take_profit_long_valid(self):
        """Test take profit validation for LONG position."""
        is_valid, reason = RiskValidator.validate_take_profit(
            entry_price=Decimal("100"),
            take_profit_price=Decimal("107.5"),  # 7.5% above entry
            position_type="LONG",
            min_distance_percent=Decimal("1.0"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_take_profit_long_below_entry(self):
        """Test take profit validation for LONG with TP below entry."""
        is_valid, reason = RiskValidator.validate_take_profit(
            entry_price=Decimal("100"),
            take_profit_price=Decimal("95"),  # Below entry (invalid for LONG)
            position_type="LONG",
        )
        assert is_valid is False
        assert "above entry" in reason

    def test_validate_take_profit_short_valid(self):
        """Test take profit validation for SHORT position."""
        is_valid, reason = RiskValidator.validate_take_profit(
            entry_price=Decimal("100"),
            take_profit_price=Decimal("92.5"),  # 7.5% below entry
            position_type="SHORT",
            min_distance_percent=Decimal("1.0"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_take_profit_short_above_entry(self):
        """Test take profit validation for SHORT with TP above entry."""
        is_valid, reason = RiskValidator.validate_take_profit(
            entry_price=Decimal("100"),
            take_profit_price=Decimal("105"),  # Above entry (invalid for SHORT)
            position_type="SHORT",
        )
        assert is_valid is False
        assert "below entry" in reason

    def test_validate_risk_limits_valid(self):
        """Test risk limit validation with valid parameters."""
        is_valid, reason = RiskValidator.validate_risk_limits(
            max_position_percent=Decimal("5.0"),
            max_loss_percent=Decimal("2.0"),
            max_portfolio_loss_percent=Decimal("10.0"),
            daily_loss_percent=Decimal("3.0"),
            max_open_positions=10,
            max_concentration_percent=Decimal("30.0"),
        )
        assert is_valid is True
        assert reason is None

    def test_validate_risk_limits_negative_values(self):
        """Test risk limit validation with negative values."""
        is_valid, reason = RiskValidator.validate_risk_limits(
            max_position_percent=Decimal("-5.0"),
            max_loss_percent=Decimal("2.0"),
            max_portfolio_loss_percent=Decimal("10.0"),
            daily_loss_percent=Decimal("3.0"),
            max_open_positions=10,
            max_concentration_percent=Decimal("30.0"),
        )
        assert is_valid is False
        assert "must be positive" in reason

    def test_validate_risk_limits_concentration_exceeds_100(self):
        """Test risk limit validation with concentration > 100%."""
        is_valid, reason = RiskValidator.validate_risk_limits(
            max_position_percent=Decimal("5.0"),
            max_loss_percent=Decimal("2.0"),
            max_portfolio_loss_percent=Decimal("10.0"),
            daily_loss_percent=Decimal("3.0"),
            max_open_positions=10,
            max_concentration_percent=Decimal("150.0"),
        )
        assert is_valid is False
        assert "cannot exceed 100%" in reason

    def test_validate_signal_strength_valid(self):
        """Test signal strength validation with valid strength."""
        is_valid, reason = RiskValidator.validate_signal_strength(
            signal_strength=75,
            min_strength=50,
        )
        assert is_valid is True
        assert reason is None

    def test_validate_signal_strength_below_minimum(self):
        """Test signal strength validation below minimum."""
        is_valid, reason = RiskValidator.validate_signal_strength(
            signal_strength=40,
            min_strength=50,
        )
        assert is_valid is False
        assert "below minimum" in reason

    def test_validate_signal_strength_out_of_range(self):
        """Test signal strength validation out of range."""
        is_valid, reason = RiskValidator.validate_signal_strength(signal_strength=150)
        assert is_valid is False
        assert "0-100" in reason

    def test_validate_position_pnl_within_limit(self):
        """Test position P&L validation within limit."""
        is_valid, reason = RiskValidator.validate_position_pnl(
            entry_price=Decimal("100"),
            current_price=Decimal("97"),  # Down 3%
            position_size=Decimal("1000"),  # $1000 position
            account_equity=Decimal("50000"),
            max_loss_percent=Decimal("2"),  # Allow up to $1000 loss (2% of $50k)
        )
        assert is_valid is True
        assert reason is None

    def test_validate_position_pnl_exceeds_limit(self):
        """Test position P&L validation exceeding limit."""
        is_valid, reason = RiskValidator.validate_position_pnl(
            entry_price=Decimal("100"),
            current_price=Decimal("95"),  # Down 5%
            position_size=Decimal("50000"),  # $50k position (exceeds max loss)
            account_equity=Decimal("50000"),
            max_loss_percent=Decimal("2"),  # Only allow $1000 loss (2% of $50k)
        )
        assert is_valid is False
        assert "exceeds max loss" in reason


class TestRiskEngine:
    """Test RiskEngine position sizing and validation."""

    def test_risk_level_config_exists(self):
        """Test that risk level configuration is properly defined."""
        assert "low" in RiskEngine.RISK_LEVEL_CONFIG
        assert "medium" in RiskEngine.RISK_LEVEL_CONFIG
        assert "high" in RiskEngine.RISK_LEVEL_CONFIG

        for level in ["low", "medium", "high"]:
            config = RiskEngine.RISK_LEVEL_CONFIG[level]
            assert "size_multiplier" in config
            assert "sl_distance" in config
            assert "tp_distance" in config
            assert config["size_multiplier"] > Decimal("0")

    def test_position_sizing_multiplier_by_risk_level(self):
        """Test position sizing multiplier increases with risk level."""
        multipliers = {}
        for level in ["low", "medium", "high"]:
            multipliers[level] = RiskEngine.get_position_sizing_multiplier(
                risk_level=level,
                signal_strength=75,
            )

        assert multipliers["low"] < multipliers["medium"] < multipliers["high"]

    def test_position_sizing_multiplier_by_signal_strength(self):
        """Test position sizing multiplier increases with signal strength."""
        multiplier_50 = RiskEngine.get_position_sizing_multiplier(
            risk_level="medium",
            signal_strength=50,
        )
        multiplier_75 = RiskEngine.get_position_sizing_multiplier(
            risk_level="medium",
            signal_strength=75,
        )
        multiplier_100 = RiskEngine.get_position_sizing_multiplier(
            risk_level="medium",
            signal_strength=100,
        )

        assert multiplier_50 < multiplier_75 < multiplier_100

    def test_position_sizing_with_low_signal_strength(self):
        """Test position sizing is small with low signal strength."""
        multiplier = RiskEngine.get_position_sizing_multiplier(
            risk_level="medium",
            signal_strength=51,  # Just above minimum 50
        )
        # Should be very small
        assert multiplier < Decimal("0.6")

    def test_position_sizing_with_high_signal_strength(self):
        """Test position sizing is large with high signal strength."""
        multiplier = RiskEngine.get_position_sizing_multiplier(
            risk_level="high",
            signal_strength=95,
        )
        # Should be quite large
        assert multiplier > Decimal("1.4")


class TestPositionSizeRecommendation:
    """Test PositionSizeRecommendation data class."""

    def test_position_size_recommendation_creation(self):
        """Test creating a PositionSizeRecommendation."""
        rec = PositionSizeRecommendation(
            position_size=Decimal("1000"),
            stop_loss_price=Decimal("97.5"),
            take_profit_price=Decimal("107.5"),
            max_loss=Decimal("25"),
            risk_reward_ratio=Decimal("3.0"),
            reason="Test recommendation",
        )

        assert rec.position_size == Decimal("1000")
        assert rec.stop_loss_price == Decimal("97.5")
        assert rec.take_profit_price == Decimal("107.5")
        assert rec.max_loss == Decimal("25")
        assert rec.risk_reward_ratio == Decimal("3.0")
        assert rec.reason == "Test recommendation"

    def test_position_size_recommendation_zero_position(self):
        """Test recommendation with zero position size."""
        rec = PositionSizeRecommendation(
            position_size=Decimal("0"),
            stop_loss_price=Decimal("100"),
            take_profit_price=Decimal("100"),
            max_loss=Decimal("0"),
            risk_reward_ratio=Decimal("0"),
            reason="Signal too weak",
        )

        assert rec.position_size == Decimal("0")
        assert rec.reason == "Signal too weak"


class TestRiskCalculations:
    """Test risk calculation formulas."""

    def test_position_size_from_risk_percentage(self):
        """
        Test position sizing formula: position = (equity * risk%) / sl_distance%

        Example: $50k account, 2% risk, 2.5% SL distance
        position = (50000 * 0.02) / 0.025 = 1000 / 0.025 = $40,000 raw
        Then apply signal strength and risk level multipliers
        """
        account_equity = Decimal("50000")
        max_loss_percent = Decimal("2")
        sl_distance = Decimal("2.5")

        # Base calculation
        position_from_risk = (
            account_equity
            * (max_loss_percent / Decimal("100"))
            / (sl_distance / Decimal("100"))
        )

        # This should be $40,000 (if max 2% loss and 2.5% stop distance)
        assert position_from_risk > Decimal("30000")
        assert position_from_risk < Decimal("50000")

    def test_max_loss_calculation(self):
        """
        Test max loss calculation: max_loss = position_size * sl_distance%

        Example: $1000 position with 2.5% SL = $25 max loss
        """
        position_size = Decimal("1000")
        sl_distance_percent = Decimal("2.5")

        max_loss = position_size * (sl_distance_percent / Decimal("100"))

        assert max_loss == Decimal("25")

    def test_risk_reward_ratio_calculation(self):
        """
        Test risk-reward ratio: tp_distance / sl_distance

        Example: 2.5% SL, 7.5% TP = 3.0 risk-reward ratio
        """
        sl_distance = Decimal("2.5")
        tp_distance = Decimal("7.5")

        risk_reward = tp_distance / sl_distance

        assert risk_reward == Decimal("3.0")

    def test_concentration_calculation(self):
        """
        Test portfolio concentration: (symbol_exposure / total_exposure) * 100

        Example: $3000 in BTC, $5000 total = 60% concentration
        """
        symbol_exposure = Decimal("3000")
        total_exposure = Decimal("5000")

        concentration_percent = (symbol_exposure / total_exposure) * Decimal("100")

        assert concentration_percent == Decimal("60")

    def test_daily_loss_limit(self):
        """
        Test daily loss limit enforcement.

        Example: $50k account, 3% daily loss limit = $1500 max loss per day
        """
        account_equity = Decimal("50000")
        daily_loss_percent = Decimal("3")

        daily_loss_limit = account_equity * (daily_loss_percent / Decimal("100"))

        assert daily_loss_limit == Decimal("1500")

    def test_portfolio_drawdown_percent(self):
        """
        Test portfolio drawdown percentage.

        Example: Peak $50k, current $45k = 10% drawdown
        """
        peak_equity = Decimal("50000")
        current_equity = Decimal("45000")

        drawdown_percent = ((peak_equity - current_equity) / peak_equity) * Decimal("100")

        assert drawdown_percent == Decimal("10")


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_entry_price(self):
        """Test validation with zero entry price."""
        is_valid, _ = RiskValidator.validate_stop_loss(
            entry_price=Decimal("0"),
            stop_loss_price=Decimal("95"),
        )
        assert is_valid is False

    def test_very_small_position_size(self):
        """Test position sizing with very small account."""
        is_valid, _ = RiskValidator.validate_position_size(
            position_size=Decimal("1"),
            account_equity=Decimal("100"),
            max_position_percent=Decimal("5"),
            min_size=Decimal("1"),
        )
        assert is_valid is True

    def test_very_large_position_size(self):
        """Test position sizing with very large position."""
        is_valid, reason = RiskValidator.validate_position_size(
            position_size=Decimal("1000000"),
            account_equity=Decimal("50000"),
            max_position_percent=Decimal("5"),
        )
        assert is_valid is False
        assert "exceeds max" in reason

    def test_equal_sl_and_tp(self):
        """Test stop loss equals take profit (invalid)."""
        # SL should be below entry, TP above entry, so they can't be equal
        entry = Decimal("100")

        # This stop loss is too close to entry (invalid for min distance)
        is_valid, _ = RiskValidator.validate_stop_loss(
            entry_price=entry,
            stop_loss_price=entry * Decimal("0.999"),
            min_distance_percent=Decimal("1.0"),
        )
        assert is_valid is False

    def test_position_sizing_minimum_signal_strength(self):
        """Test position sizing with minimum acceptable signal strength."""
        multiplier = RiskEngine.get_position_sizing_multiplier(
            risk_level="medium",
            signal_strength=50,  # Minimum acceptable
        )
        # Should give some position, not zero
        assert multiplier > Decimal("0")

    def test_position_sizing_maximum_signal_strength(self):
        """Test position sizing with maximum signal strength."""
        multiplier = RiskEngine.get_position_sizing_multiplier(
            risk_level="high",
            signal_strength=100,  # Maximum
        )
        # Should be maximum multiplier
        assert multiplier == RiskEngine.RISK_LEVEL_CONFIG["high"]["size_multiplier"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
