"""Risk validation utilities - P3 Phase 3A."""

import logging
from decimal import Decimal
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class RiskValidator:
    """Validate risk parameters and constraints."""

    @staticmethod
    def validate_position_size(
        position_size: Decimal,
        account_equity: Decimal,
        max_position_percent: Decimal,
        min_size: Decimal = Decimal("10"),
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate position size against limits.

        Args:
            position_size: Proposed position size in quote currency
            account_equity: Total account equity
            max_position_percent: Maximum position as % of account
            min_size: Minimum position size

        Returns:
            (is_valid, error_message)
        """
        if position_size <= Decimal("0"):
            return False, "Position size must be positive"

        if position_size < min_size:
            return False, f"Position size ${position_size} below minimum ${min_size}"

        max_allowed = account_equity * (max_position_percent / Decimal("100"))
        if position_size > max_allowed:
            return False, f"Position size ${position_size} exceeds max ${max_allowed} ({max_position_percent}% of equity)"

        return True, None

    @staticmethod
    def validate_stop_loss(
        entry_price: Decimal,
        stop_loss_price: Decimal,
        position_type: str = "LONG",
        min_distance_percent: Decimal = Decimal("0.5"),
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate stop loss placement.

        Args:
            entry_price: Entry price of position
            stop_loss_price: Proposed stop loss price
            position_type: LONG or SHORT
            min_distance_percent: Minimum SL distance as % of entry price

        Returns:
            (is_valid, error_message)
        """
        if entry_price <= Decimal("0"):
            return False, "Entry price must be positive"

        if stop_loss_price <= Decimal("0"):
            return False, "Stop loss price must be positive"

        if position_type.upper() == "LONG":
            if stop_loss_price >= entry_price:
                return False, f"For LONG: SL ${stop_loss_price} must be below entry ${entry_price}"

            distance_percent = ((entry_price - stop_loss_price) / entry_price) * Decimal("100")
            if distance_percent < min_distance_percent:
                return False, f"SL distance {distance_percent:.2f}% below minimum {min_distance_percent}%"

        elif position_type.upper() == "SHORT":
            if stop_loss_price <= entry_price:
                return False, f"For SHORT: SL ${stop_loss_price} must be above entry ${entry_price}"

            distance_percent = ((stop_loss_price - entry_price) / entry_price) * Decimal("100")
            if distance_percent < min_distance_percent:
                return False, f"SL distance {distance_percent:.2f}% below minimum {min_distance_percent}%"

        return True, None

    @staticmethod
    def validate_take_profit(
        entry_price: Decimal,
        take_profit_price: Decimal,
        position_type: str = "LONG",
        min_distance_percent: Decimal = Decimal("1.0"),
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate take profit placement.

        Args:
            entry_price: Entry price of position
            take_profit_price: Proposed take profit price
            position_type: LONG or SHORT
            min_distance_percent: Minimum TP distance as % of entry price

        Returns:
            (is_valid, error_message)
        """
        if entry_price <= Decimal("0"):
            return False, "Entry price must be positive"

        if take_profit_price <= Decimal("0"):
            return False, "Take profit price must be positive"

        if position_type.upper() == "LONG":
            if take_profit_price <= entry_price:
                return False, f"For LONG: TP ${take_profit_price} must be above entry ${entry_price}"

            distance_percent = ((take_profit_price - entry_price) / entry_price) * Decimal("100")
            if distance_percent < min_distance_percent:
                return False, f"TP distance {distance_percent:.2f}% below minimum {min_distance_percent}%"

        elif position_type.upper() == "SHORT":
            if take_profit_price >= entry_price:
                return False, f"For SHORT: TP ${take_profit_price} must be below entry ${entry_price}"

            distance_percent = ((entry_price - take_profit_price) / entry_price) * Decimal("100")
            if distance_percent < min_distance_percent:
                return False, f"TP distance {distance_percent:.2f}% below minimum {min_distance_percent}%"

        return True, None

    @staticmethod
    def validate_risk_limits(
        max_position_percent: Decimal,
        max_loss_percent: Decimal,
        max_portfolio_loss_percent: Decimal,
        daily_loss_percent: Decimal,
        max_open_positions: int,
        max_concentration_percent: Decimal,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate risk limit parameters for consistency.

        Args:
            max_position_percent: Max position as % of portfolio
            max_loss_percent: Max loss per position as % of account
            max_portfolio_loss_percent: Max portfolio loss as % of account
            daily_loss_percent: Max daily loss as % of account
            max_open_positions: Maximum concurrent open positions
            max_concentration_percent: Max concentration in single asset

        Returns:
            (is_valid, error_message)
        """
        # Check all limits are positive
        if max_position_percent <= Decimal("0"):
            return False, "max_position_percent must be positive"
        if max_loss_percent <= Decimal("0"):
            return False, "max_loss_percent must be positive"
        if max_portfolio_loss_percent <= Decimal("0"):
            return False, "max_portfolio_loss_percent must be positive"
        if daily_loss_percent <= Decimal("0"):
            return False, "daily_loss_percent must be positive"
        if max_open_positions <= 0:
            return False, "max_open_positions must be positive"
        if max_concentration_percent <= Decimal("0"):
            return False, "max_concentration_percent must be positive"

        # Check portfolio loss is stricter than daily loss
        if daily_loss_percent > max_portfolio_loss_percent:
            logger.warning(
                f"Daily loss limit {daily_loss_percent}% > portfolio loss limit {max_portfolio_loss_percent}% "
                "Portfolio limit should be higher"
            )

        # Check concentration limit is reasonable
        if max_concentration_percent > Decimal("100"):
            return False, "max_concentration_percent cannot exceed 100%"

        return True, None

    @staticmethod
    def validate_signal_strength(
        signal_strength: int,
        min_strength: int = 50,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate trading signal strength.

        Args:
            signal_strength: Signal strength 0-100
            min_strength: Minimum required strength

        Returns:
            (is_valid, error_message)
        """
        if signal_strength < 0 or signal_strength > 100:
            return False, f"Signal strength must be 0-100, got {signal_strength}"

        if signal_strength < min_strength:
            return False, f"Signal strength {signal_strength} below minimum {min_strength}"

        return True, None

    @staticmethod
    def validate_position_pnl(
        entry_price: Decimal,
        current_price: Decimal,
        position_size: Decimal,
        account_equity: Decimal,
        max_loss_percent: Decimal,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that position loss doesn't exceed limit.

        Args:
            entry_price: Position entry price
            current_price: Current price
            position_size: Position size in quote currency
            account_equity: Total account equity
            max_loss_percent: Maximum loss as % of account

        Returns:
            (is_valid, error_message)
        """
        if entry_price <= Decimal("0"):
            return False, "Entry price must be positive"

        price_return = (current_price - entry_price) / entry_price
        pnl = position_size * price_return

        max_loss = account_equity * (max_loss_percent / Decimal("100"))

        if pnl < -max_loss:
            return False, f"Position loss ${abs(pnl)} exceeds max loss ${max_loss} ({max_loss_percent}%)"

        return True, None
