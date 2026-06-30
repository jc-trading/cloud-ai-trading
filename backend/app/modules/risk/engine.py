"""Risk management engine - P3 Phase 3A."""

import logging
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from .validators import RiskValidator
from .models import RiskLimit
from app.modules.trading.models import Position

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeRecommendation:
    """Position sizing recommendation from RiskEngine."""

    position_size: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    max_loss: Decimal
    risk_reward_ratio: Decimal
    reason: str


class RiskEngine:
    """Calculate position sizes and validate risk constraints."""

    # Risk level multipliers for position sizing
    RISK_LEVEL_CONFIG = {
        "low": {
            "size_multiplier": Decimal("0.5"),
            "sl_distance": Decimal("2.0"),
            "tp_distance": Decimal("5.0"),
        },
        "medium": {
            "size_multiplier": Decimal("1.0"),
            "sl_distance": Decimal("2.5"),
            "tp_distance": Decimal("7.5"),
        },
        "high": {
            "size_multiplier": Decimal("1.5"),
            "sl_distance": Decimal("3.0"),
            "tp_distance": Decimal("10.0"),
        },
    }

    @staticmethod
    async def calculate_position_size(
        session: AsyncSession,
        watchlist_id: str,
        symbol: str,
        entry_price: Decimal,
        signal_strength: int,
        account_equity: Decimal,
        current_positions: Dict[str, Decimal],
    ) -> PositionSizeRecommendation:
        """
        Calculate optimal position size based on risk parameters.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            symbol: Trading symbol
            entry_price: Entry price
            signal_strength: Signal strength 0-100
            account_equity: Total account equity
            current_positions: Dict of {symbol: position_value}

        Returns:
            PositionSizeRecommendation with size, SL, TP
        """
        # Get risk limits
        risk_limits = await RiskEngine._get_risk_limits(session, watchlist_id)
        if not risk_limits:
            return PositionSizeRecommendation(
                position_size=Decimal("0"),
                stop_loss_price=entry_price,
                take_profit_price=entry_price,
                max_loss=Decimal("0"),
                risk_reward_ratio=Decimal("0"),
                reason="No risk limits configured",
            )

        # Validate signal strength
        is_valid, reason = RiskValidator.validate_signal_strength(
            signal_strength,
            min_strength=int(risk_limits.min_signal_strength),
        )
        if not is_valid:
            return PositionSizeRecommendation(
                position_size=Decimal("0"),
                stop_loss_price=entry_price,
                take_profit_price=entry_price,
                max_loss=Decimal("0"),
                risk_reward_ratio=Decimal("0"),
                reason=reason or "Signal strength too low",
            )

        # Calculate base position size
        risk_config = RiskEngine.RISK_LEVEL_CONFIG.get(
            risk_limits.risk_level, RiskEngine.RISK_LEVEL_CONFIG["medium"]
        )

        # Fixed risk percentage approach:
        # position_size = (account_equity * max_loss_percent / 100) / (sl_distance / 100)
        max_loss_percent = risk_limits.max_loss_per_trade_percent
        sl_distance_percent = risk_config["sl_distance"]
        tp_distance_percent = risk_config["tp_distance"]

        # Base position sizing from risk percentage
        max_position_from_risk = (
            account_equity
            * (max_loss_percent / Decimal("100"))
            / (sl_distance_percent / Decimal("100"))
        )

        # Apply signal strength multiplier
        signal_multiplier = Decimal(signal_strength) / Decimal("100")
        position_size = max_position_from_risk * signal_multiplier * risk_config["size_multiplier"]

        # Apply max position size limit (% of portfolio)
        max_position_from_limit = (
            account_equity * (risk_limits.max_position_size_percent / Decimal("100"))
        )
        position_size = min(position_size, max_position_from_limit)

        # Calculate SL and TP prices
        stop_loss_price = entry_price * (Decimal("1") - sl_distance_percent / Decimal("100"))
        take_profit_price = entry_price * (Decimal("1") + tp_distance_percent / Decimal("100"))

        # Calculate actual max loss and risk-reward ratio
        max_loss = position_size * (sl_distance_percent / Decimal("100"))
        risk_reward = tp_distance_percent / sl_distance_percent

        return PositionSizeRecommendation(
            position_size=position_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            max_loss=max_loss,
            risk_reward_ratio=risk_reward,
            reason="Position size calculated successfully",
        )

    @staticmethod
    async def validate_new_position(
        session: AsyncSession,
        watchlist_id: str,
        symbol: str,
        position_size: Decimal,
        account_equity: Decimal,
        current_positions: Dict[str, Decimal],
    ) -> Tuple[bool, str]:
        """
        Validate if new position can be opened given current constraints.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            symbol: Trading symbol
            position_size: Proposed position size
            account_equity: Total account equity
            current_positions: Dict of {symbol: position_value}

        Returns:
            (is_allowed, reason)
        """
        # Get risk limits
        risk_limits = await RiskEngine._get_risk_limits(session, watchlist_id)
        if not risk_limits or not risk_limits.enabled:
            return False, "Risk limits not configured or disabled"

        # Check 1: Individual position size limit
        max_position = account_equity * (risk_limits.max_position_size_percent / Decimal("100"))
        if position_size > max_position:
            return False, f"Position ${position_size} exceeds max ${max_position}"

        # Check 2: Open positions limit
        open_positions = await RiskEngine._count_open_positions(session, watchlist_id)
        if open_positions >= risk_limits.max_open_positions:
            return False, f"Already have {open_positions} open positions (max: {risk_limits.max_open_positions})"

        # Check 3: Portfolio concentration limit
        current_symbol_exposure = current_positions.get(symbol, Decimal("0"))
        total_exposure = sum(current_positions.values())
        new_total_exposure = total_exposure + position_size

        if new_total_exposure > Decimal("0"):
            new_concentration = (current_symbol_exposure + position_size) / new_total_exposure * Decimal("100")
            max_concentration = risk_limits.max_concentration_percent

            if new_concentration > max_concentration:
                return False, f"New concentration {new_concentration:.1f}% exceeds max {max_concentration}%"

        # Check 4: Daily loss limit (check if exceeded)
        daily_pnl = await RiskEngine._calculate_daily_pnl(session, watchlist_id)
        daily_loss_limit = account_equity * (risk_limits.daily_loss_limit_percent / Decimal("100"))

        if daily_pnl < -daily_loss_limit:
            return False, f"Daily loss ${abs(daily_pnl)} exceeds limit ${daily_loss_limit}"

        return True, "Position allowed"

    @staticmethod
    async def check_portfolio_limits(
        session: AsyncSession,
        watchlist_id: str,
        account_equity: Decimal,
        current_pnl: Decimal,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if portfolio limits have been exceeded.

        Args:
            session: Database session
            watchlist_id: Watchlist ID
            account_equity: Total account equity
            current_pnl: Current total P&L

        Returns:
            (limits_ok, alert_message)
        """
        risk_limits = await RiskEngine._get_risk_limits(session, watchlist_id)
        if not risk_limits or not risk_limits.enabled:
            return True, None

        # Check portfolio loss limit
        portfolio_loss_limit = account_equity * (risk_limits.max_portfolio_loss_percent / Decimal("100"))
        if current_pnl < -portfolio_loss_limit:
            return False, f"Portfolio loss ${abs(current_pnl)} exceeds limit ${portfolio_loss_limit}"

        # Check daily loss limit
        daily_pnl = await RiskEngine._calculate_daily_pnl(session, watchlist_id)
        daily_loss_limit = account_equity * (risk_limits.daily_loss_limit_percent / Decimal("100"))
        if daily_pnl < -daily_loss_limit:
            return False, f"Daily loss ${abs(daily_pnl)} exceeds limit ${daily_loss_limit}"

        # Check consecutive losses
        consecutive_losses = await RiskEngine._count_consecutive_losses(session, watchlist_id)
        if consecutive_losses >= risk_limits.max_consecutive_losses:
            return False, f"Consecutive losses {consecutive_losses} reached limit {risk_limits.max_consecutive_losses}"

        return True, None

    # Private helper methods

    @staticmethod
    async def _get_risk_limits(
        session: AsyncSession,
        watchlist_id: str,
    ) -> Optional[RiskLimit]:
        """Get risk limits configuration for watchlist."""
        stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _count_open_positions(
        session: AsyncSession,
        watchlist_id: str,
    ) -> int:
        """Count currently open positions."""
        stmt = select(func.count(Position.id)).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "open",
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

    @staticmethod
    async def _calculate_daily_pnl(
        session: AsyncSession,
        watchlist_id: str,
    ) -> Decimal:
        """Calculate P&L from positions closed today."""
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import and_

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        stmt = select(func.sum(
            (Position.exit_price - Position.entry_price) * Position.quantity
        )).where(
            and_(
                Position.watchlist_id == watchlist_id,
                Position.status == "closed",
                Position.exit_date >= today,
            )
        )

        result = await session.execute(stmt)
        pnl = result.scalar()
        return Decimal(str(pnl)) if pnl else Decimal("0")

    @staticmethod
    async def _count_consecutive_losses(
        session: AsyncSession,
        watchlist_id: str,
    ) -> int:
        """Count consecutive closed positions with losses."""
        stmt = select(Position).where(
            Position.watchlist_id == watchlist_id,
            Position.status == "closed",
        ).order_by(Position.exit_date.desc()).limit(10)

        result = await session.execute(stmt)
        positions = result.scalars().all()

        consecutive = 0
        for position in positions:
            if position.exit_price and position.exit_price < position.entry_price:
                consecutive += 1
            else:
                break

        return consecutive

    @staticmethod
    def get_position_sizing_multiplier(
        risk_level: str,
        signal_strength: int,
    ) -> Decimal:
        """
        Get position sizing multiplier for given risk level and signal strength.

        Args:
            risk_level: "low", "medium", or "high"
            signal_strength: 0-100

        Returns:
            Multiplier for base position size
        """
        risk_config = RiskEngine.RISK_LEVEL_CONFIG.get(risk_level, RiskEngine.RISK_LEVEL_CONFIG["medium"])
        signal_multiplier = Decimal(signal_strength) / Decimal("100")
        return risk_config["size_multiplier"] * signal_multiplier
