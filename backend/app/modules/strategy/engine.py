"""
QuantStrategy Engine - applies user strategy weights to signals.
P2 Implementation: Signal weight application and position sizing.
"""

import logging
from decimal import Decimal
from typing import Any, Dict, Mapping, TypedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class StrategyDict(TypedDict, total=False):
    """Type definition for strategy configuration dictionary."""
    momentum_weight: float
    contrarian_weight: float
    macd_weight: float
    bollinger_band_weight: float
    risk_level: str  # "low", "medium", "high"
    min_confidence_threshold: int
    max_position_size: float
    stop_loss_percent: float
    take_profit_percent: float
    position_sizing: Dict
    max_positions: int


@dataclass
class StrategySignal:
    """Weighted signal result from strategy engine."""
    action: str  # BUY, SELL, HOLD
    composite_score: float  # 0-100
    position_size: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence: float


class QuantStrategyEngine:
    """
    Apply user-configured strategy weights to generated signals.

    Converts 4 raw signals into weighted composite signal based on user preferences.
    """

    RISK_LEVEL_CONFIG = {
        "low": {
            "position_multiplier": 0.5,
            "min_confidence": 75,
        },
        "medium": {
            "position_multiplier": 1.0,
            "min_confidence": 65,
        },
        "high": {
            "position_multiplier": 1.5,
            "min_confidence": 55,
        },
    }

    @staticmethod
    def _normalize_strategy(strategy: Mapping[str, Any] | Any) -> StrategyDict:
        """Return a dict-like strategy config from ORM, Pydantic, or mapping input."""
        if isinstance(strategy, dict):
            return strategy
        if hasattr(strategy, "model_dump"):
            return strategy.model_dump()
        if hasattr(strategy, "dict"):
            return strategy.dict()

        fields = StrategyDict.__annotations__.keys()
        normalized = {
            field: getattr(strategy, field)
            for field in fields
            if hasattr(strategy, field)
        }
        if hasattr(strategy, "stop_loss_pct") and "stop_loss_percent" not in normalized:
            normalized["stop_loss_percent"] = getattr(strategy, "stop_loss_pct")
        if hasattr(strategy, "take_profit_pct") and "take_profit_percent" not in normalized:
            normalized["take_profit_percent"] = getattr(strategy, "take_profit_pct")
        return normalized

    @staticmethod
    def validate_weights(
        momentum_weight: float,
        contrarian_weight: float,
        macd_weight: float,
        bb_weight: float,
    ) -> bool:
        """Validate that weights sum to approximately 1.0."""
        total = momentum_weight + contrarian_weight + macd_weight + bb_weight
        return abs(total - 1.0) < 0.01

    @staticmethod
    def apply_strategy(
        strategy: Mapping[str, Any] | Any,
        signals: Dict[str, Dict],
        current_price: Decimal,
    ) -> StrategySignal:
        """
        Apply strategy weights to 4 signals and generate composite signal.

        Args:
            strategy: Strategy configuration dictionary with weights and risk params
            signals: Dict with keys ['momentum', 'contrarian', 'macd', 'bollinger_band']
                    Each value: {'type': str, 'strength': float, 'confidence': float}
            current_price: Current market price

        Returns:
            StrategySignal with weighted action and position sizing
        """

        strategy_config = QuantStrategyEngine._normalize_strategy(strategy)

        # Extract weights from strategy
        momentum_w = strategy_config.get("momentum_weight", 0.25)
        contrarian_w = strategy_config.get("contrarian_weight", 0.20)
        macd_w = strategy_config.get("macd_weight", 0.25)
        bb_w = strategy_config.get("bollinger_band_weight", 0.30)

        # 1. Validate weights sum to 1.0
        if not QuantStrategyEngine.validate_weights(momentum_w, contrarian_w, macd_w, bb_w):
            logger.warning(f"Strategy weights don't sum to 1.0, normalizing")
            total = momentum_w + contrarian_w + macd_w + bb_w
            momentum_w /= total
            contrarian_w /= total
            macd_w /= total
            bb_w /= total

        # 2. Apply weights to signal strengths
        momentum_strength = signals.get("momentum", {}).get("strength", 50) * momentum_w
        contrarian_strength = signals.get("contrarian", {}).get("strength", 50) * contrarian_w
        macd_strength = signals.get("macd", {}).get("strength", 50) * macd_w
        bb_strength = signals.get("bollinger_band", {}).get("strength", 50) * bb_w

        # 3. Calculate composite score (0-100)
        composite_score = (
            momentum_strength +
            contrarian_strength +
            macd_strength +
            bb_strength
        )

        # 4. Get strategy risk config
        risk_level = strategy_config.get("risk_level", "medium")
        risk_config = QuantStrategyEngine.RISK_LEVEL_CONFIG.get(
            risk_level,
            QuantStrategyEngine.RISK_LEVEL_CONFIG["medium"]
        )

        # 5. Determine action based on composite score
        action = QuantStrategyEngine._determine_action(composite_score)

        # 6. Check confidence threshold
        min_confidence = strategy_config.get("min_confidence_threshold", risk_config["min_confidence"])
        confidence_distance = abs(composite_score - 50)
        # Only HOLD if signal is too weak (distance from 50 is less than threshold)
        # Example: if min_confidence=65, only hold if distance < 15 (score between 35-65)
        if confidence_distance < (min_confidence - 50):
            action = "HOLD"
            logger.info(f"Signal too weak (confidence={confidence_distance}), below threshold ({min_confidence}), setting to HOLD")

        # 7. Calculate position size
        position_size = QuantStrategyEngine._calculate_position_size(
            action=action,
            confidence=composite_score,
            max_size=Decimal(str(strategy_config.get("max_position_size", 1000))),
            risk_level=risk_level,
            risk_config=risk_config
        )

        # 8. Calculate stop loss and take profit
        sl_pct = Decimal(str(
            strategy_config.get("stop_loss_percent", strategy_config.get("stop_loss_pct", 2.5))
        ))
        tp_pct = Decimal(str(
            strategy_config.get("take_profit_percent", strategy_config.get("take_profit_pct", 5.0))
        ))

        stop_loss = current_price * (1 - sl_pct / 100)
        take_profit = current_price * (1 + tp_pct / 100)

        logger.info(
            f"Strategy applied: action={action}, composite={composite_score:.1f}, "
            f"position={position_size}, SL={stop_loss:.2f}, TP={take_profit:.2f}"
        )

        return StrategySignal(
            action=action,
            composite_score=composite_score,
            position_size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(100, confidence_distance)
        )

    @staticmethod
    def _determine_action(composite_score: float) -> str:
        """
        Determine action based on composite score.

        0-30: STRONG_SELL
        30-40: SELL
        40-60: HOLD
        60-70: BUY
        70-100: STRONG_BUY
        """
        if composite_score >= 70:
            return "STRONG_BUY"
        elif composite_score >= 60:
            return "BUY"
        elif composite_score >= 40:
            return "HOLD"
        elif composite_score >= 30:
            return "SELL"
        else:
            return "STRONG_SELL"

    @staticmethod
    def _calculate_position_size(
        action: str,
        confidence: float,
        max_size: Decimal,
        risk_level: str,
        risk_config: Dict,
    ) -> Decimal:
        """
        Calculate position size based on action, confidence, and risk level.

        Position size formula:
        base_size = max_size * position_multiplier * (confidence / 100)
        """

        # Skip position sizing for HOLD
        if action == "HOLD":
            return Decimal("0")

        # Calculate confidence multiplier (0.5 to 1.5 range)
        confidence_multiplier = 0.5 + (abs(confidence - 50) / 100)

        # Get risk multiplier
        position_multiplier = risk_config.get("position_multiplier", 1.0)

        # Final position size
        position_size = (
            max_size *
            Decimal(str(position_multiplier)) *
            Decimal(str(confidence_multiplier))
        )

        return min(position_size, max_size)

    @staticmethod
    def compare_strategies(
        strategies: list,
        backtest_results: Dict[str, Dict]
    ) -> list[Dict]:
        """
        Compare multiple strategies based on backtest results.

        Returns sorted list of strategies with metrics.
        """
        comparison = []

        for strategy in strategies:
            strategy_config = QuantStrategyEngine._normalize_strategy(strategy)
            strategy_id = strategy_config.get("id")
            results = backtest_results.get(str(strategy_id), {})

            comparison.append({
                "id": strategy_id,
                "name": strategy_config.get("name"),
                "win_rate": results.get("win_rate", 0),
                "profit_factor": results.get("profit_factor", 0),
                "sharpe_ratio": results.get("sharpe_ratio", 0),
                "max_drawdown": results.get("max_drawdown", 0),
                "total_return": results.get("total_return", 0),
            })

        # Sort by Sharpe ratio (risk-adjusted return)
        comparison.sort(key=lambda x: x["sharpe_ratio"], reverse=True)

        return comparison
