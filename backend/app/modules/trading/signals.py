"""Trading signal generation service."""

from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
import logging

from app.modules.market_data.models import TechnicalIndicator
from .models import TradingSignal

logger = logging.getLogger(__name__)


class TradingSignalGenerator:
    """Generate trading signals based on technical indicators."""

    @staticmethod
    async def generate_momentum_signal(
        session: AsyncSession,
        watchlist_id: str,
        symbol: str,
        ema_12: Decimal,
        ema_26: Decimal,
        prev_ema_12: Optional[Decimal],
        prev_ema_26: Optional[Decimal],
        signal_timestamp,
    ) -> dict:
        """
        Generate momentum signal based on EMA crossover (Golden Cross / Death Cross).

        EMA12 > EMA26 = Uptrend
        EMA12 < EMA26 = Downtrend
        Golden Cross (12 crosses above 26) = STRONG_BUY
        Death Cross (12 crosses below 26) = STRONG_SELL
        """
        ema_12 = Decimal(str(ema_12))
        ema_26 = Decimal(str(ema_26))
        prev_ema_12 = Decimal(str(prev_ema_12)) if prev_ema_12 else ema_12
        prev_ema_26 = Decimal(str(prev_ema_26)) if prev_ema_26 else ema_26

        # Calculate distance percentage
        distance = abs(ema_12 - ema_26) / ema_26 * 100 if ema_26 != 0 else Decimal(0)

        # Detect crossovers
        golden_cross = prev_ema_12 <= prev_ema_26 and ema_12 > ema_26
        death_cross = prev_ema_12 >= prev_ema_26 and ema_12 < ema_26

        # Determine signal
        if golden_cross:
            signal_type = "STRONG_BUY"
            signal_strength = Decimal("100")
            confidence = Decimal("95")
            recommendation = f"Golden Cross detected! EMA12 ({ema_12:.2f}) crossed above EMA26 ({ema_26:.2f})"
        elif death_cross:
            signal_type = "STRONG_SELL"
            signal_strength = Decimal("0")
            confidence = Decimal("95")
            recommendation = f"Death Cross detected! EMA12 ({ema_12:.2f}) crossed below EMA26 ({ema_26:.2f})"
        elif ema_12 > ema_26:
            if distance > 2:
                signal_type = "BUY"
                signal_strength = Decimal("70")
                confidence = Decimal("75")
            else:
                signal_type = "BUY"
                signal_strength = Decimal("55")
                confidence = Decimal("60")
            recommendation = f"Uptrend. EMA12 ({ema_12:.2f}) > EMA26 ({ema_26:.2f}), distance: {distance:.2f}%"
        else:
            if distance > 2:
                signal_type = "SELL"
                signal_strength = Decimal("30")
                confidence = Decimal("75")
            else:
                signal_type = "SELL"
                signal_strength = Decimal("45")
                confidence = Decimal("60")
            recommendation = f"Downtrend. EMA12 ({ema_12:.2f}) < EMA26 ({ema_26:.2f}), distance: {distance:.2f}%"

        return {
            "signal_type": signal_type,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "recommendation": recommendation,
            "indicators_used": {
                "EMA_12": float(ema_12),
                "EMA_26": float(ema_26),
                "distance_percent": float(distance)
            },
            "strategy": "MOMENTUM"
        }

    @staticmethod
    async def generate_contrarian_signal(
        rsi: Decimal,
        bb_upper: Decimal,
        bb_lower: Decimal,
        current_price: Decimal,
    ) -> dict:
        """
        Generate contrarian signal based on RSI overbought/oversold.

        RSI < 30 = Oversold (potential reversal up)
        RSI > 70 = Overbought (potential reversal down)
        RSI 30-70 = Normal
        """
        rsi = Decimal(str(rsi))

        if rsi < 30:
            signal_type = "BUY"
            signal_strength = Decimal("65")
            confidence = Decimal("70")
            recommendation = f"RSI Oversold ({rsi:.2f}). Potential reversal up."
        elif rsi > 70:
            signal_type = "SELL"
            signal_strength = Decimal("35")
            confidence = Decimal("70")
            recommendation = f"RSI Overbought ({rsi:.2f}). Potential reversal down."
        else:
            signal_type = "HOLD"
            signal_strength = Decimal("50")
            confidence = Decimal("50")
            recommendation = f"RSI Neutral ({rsi:.2f}). No clear contrarian signal."

        return {
            "signal_type": signal_type,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "recommendation": recommendation,
            "indicators_used": {
                "RSI": float(rsi),
                "BB_Upper": float(bb_upper),
                "BB_Lower": float(bb_lower)
            },
            "strategy": "CONTRARIAN"
        }

    @staticmethod
    async def generate_macd_signal(
        macd: Decimal,
        macd_signal: Decimal,
        prev_macd: Optional[Decimal],
        prev_macd_signal: Optional[Decimal],
    ) -> dict:
        """
        Generate signal based on MACD crossover detection.

        MACD > Signal Line = Bullish
        MACD < Signal Line = Bearish
        Crossover = High conviction signal
        """
        macd = Decimal(str(macd))
        macd_signal = Decimal(str(macd_signal))
        prev_macd = Decimal(str(prev_macd)) if prev_macd else macd
        prev_macd_signal = Decimal(str(prev_macd_signal)) if prev_macd_signal else macd_signal

        # Calculate distance
        distance = abs(macd - macd_signal) if macd_signal != 0 else Decimal(0)

        # Detect crossovers
        bullish_crossover = prev_macd <= prev_macd_signal and macd > macd_signal
        bearish_crossover = prev_macd >= prev_macd_signal and macd < macd_signal

        # Determine signal
        if bullish_crossover:
            signal_type = "STRONG_BUY"
            signal_strength = Decimal("100")
            confidence = Decimal("90")
            recommendation = f"MACD bullish crossover! MACD ({macd:.2f}) crossed above signal ({macd_signal:.2f})"
        elif bearish_crossover:
            signal_type = "STRONG_SELL"
            signal_strength = Decimal("0")
            confidence = Decimal("90")
            recommendation = f"MACD bearish crossover! MACD ({macd:.2f}) crossed below signal ({macd_signal:.2f})"
        elif macd > macd_signal:
            # Bullish trend
            strength_ratio = distance / abs(macd_signal) * 100 if macd_signal != 0 else Decimal(0)
            if strength_ratio > 5:
                signal_type = "BUY"
                signal_strength = Decimal("75")
                confidence = Decimal("80")
            else:
                signal_type = "BUY"
                signal_strength = Decimal("60")
                confidence = Decimal("70")
            recommendation = f"MACD bullish trend. MACD ({macd:.2f}) > signal ({macd_signal:.2f}), distance: {distance:.2f}"
        else:
            # Bearish trend
            strength_ratio = distance / abs(macd_signal) * 100 if macd_signal != 0 else Decimal(0)
            if strength_ratio > 5:
                signal_type = "SELL"
                signal_strength = Decimal("25")
                confidence = Decimal("80")
            else:
                signal_type = "SELL"
                signal_strength = Decimal("40")
                confidence = Decimal("70")
            recommendation = f"MACD bearish trend. MACD ({macd:.2f}) < signal ({macd_signal:.2f}), distance: {distance:.2f}"

        return {
            "signal_type": signal_type,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "recommendation": recommendation,
            "indicators_used": {
                "MACD": float(macd),
                "MACD_Signal": float(macd_signal),
                "distance": float(distance)
            },
            "strategy": "MACD"
        }

    @staticmethod
    async def generate_bb_breakout_signal(
        current_price: Decimal,
        bb_upper: Decimal,
        bb_middle: Decimal,
        bb_lower: Decimal,
    ) -> dict:
        """
        Generate signal based on Bollinger Band breakout.

        Price > BB_Upper = Breakout up (overbought)
        Price < BB_Lower = Breakout down (oversold)
        Price near bands = Extreme volatility
        """
        current_price = Decimal(str(current_price))
        bb_upper = Decimal(str(bb_upper))
        bb_middle = Decimal(str(bb_middle))
        bb_lower = Decimal(str(bb_lower))

        band_width = bb_upper - bb_lower
        distance_to_upper = bb_upper - current_price
        distance_to_lower = current_price - bb_lower

        # Detect breakouts
        if current_price > bb_upper:
            signal_type = "STRONG_BUY"
            signal_strength = Decimal("100")
            confidence = Decimal("85")
            recommendation = f"BB upper breakout! Price ({current_price:.2f}) > BB upper ({bb_upper:.2f})"
        elif current_price < bb_lower:
            signal_type = "STRONG_SELL"
            signal_strength = Decimal("0")
            confidence = Decimal("85")
            recommendation = f"BB lower breakout! Price ({current_price:.2f}) < BB lower ({bb_lower:.2f})"
        elif distance_to_upper < band_width * Decimal("0.1"):
            # Price very close to upper band
            signal_type = "BUY"
            signal_strength = Decimal("68")
            confidence = Decimal("75")
            recommendation = f"Price near BB upper. ({current_price:.2f}), distance: {distance_to_upper:.2f}"
        elif distance_to_lower < band_width * Decimal("0.1"):
            # Price very close to lower band
            signal_type = "SELL"
            signal_strength = Decimal("32")
            confidence = Decimal("75")
            recommendation = f"Price near BB lower. ({current_price:.2f}), distance: {distance_to_lower:.2f}"
        elif current_price > bb_middle:
            # Price in upper half
            signal_type = "HOLD"
            signal_strength = Decimal("55")
            confidence = Decimal("60")
            recommendation = f"Price in upper half of BB. ({current_price:.2f})"
        else:
            # Price in lower half
            signal_type = "HOLD"
            signal_strength = Decimal("45")
            confidence = Decimal("60")
            recommendation = f"Price in lower half of BB. ({current_price:.2f})"

        return {
            "signal_type": signal_type,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "recommendation": recommendation,
            "indicators_used": {
                "Price": float(current_price),
                "BB_Upper": float(bb_upper),
                "BB_Middle": float(bb_middle),
                "BB_Lower": float(bb_lower),
                "Band_Width": float(band_width)
            },
            "strategy": "BOLLINGER_BAND"
        }

    @staticmethod
    async def generate_composite_signal(signals: list) -> dict:
        """
        Generate composite signal by averaging multiple strategy signals.

        Weights: Momentum 50%, Contrarian 50%
        """
        if not signals:
            return {
                "signal_type": "HOLD",
                "signal_strength": Decimal("50"),
                "confidence": Decimal("50"),
                "recommendation": "No signals available for composite analysis",
                "strategy": "COMPOSITE"
            }

        # Convert signal types to numeric values for averaging
        signal_scores = {
            "STRONG_BUY": 100,
            "BUY": 70,
            "HOLD": 50,
            "SELL": 30,
            "STRONG_SELL": 0
        }

        total_strength = Decimal(0)
        total_confidence = Decimal(0)

        for signal in signals:
            score = signal_scores.get(signal.get("signal_type", "HOLD"), 50)
            total_strength += Decimal(str(score))
            total_confidence += signal.get("confidence", Decimal("50"))

        avg_strength = total_strength / len(signals)
        avg_confidence = total_confidence / len(signals)

        # Convert average back to signal type
        if avg_strength >= 85:
            signal_type = "STRONG_BUY"
        elif avg_strength >= 60:
            signal_type = "BUY"
        elif avg_strength >= 40:
            signal_type = "HOLD"
        elif avg_strength >= 20:
            signal_type = "SELL"
        else:
            signal_type = "STRONG_SELL"

        return {
            "signal_type": signal_type,
            "signal_strength": avg_strength,
            "confidence": avg_confidence,
            "recommendation": f"Composite signal from {len(signals)} strategies. Avg strength: {avg_strength:.2f}%",
            "indicators_used": {
                f"signal_{i}": sig.get("strategy") for i, sig in enumerate(signals)
            },
            "strategy": "COMPOSITE"
        }

    @staticmethod
    async def save_signal(
        session: AsyncSession,
        watchlist_id: str,
        symbol: str,
        signal_data: dict,
        signal_timestamp,
    ) -> TradingSignal:
        """Save trading signal to database.

        Uses upsert (INSERT ... ON CONFLICT DO UPDATE) to handle the case
        where the same signal already exists for the same candle timestamp,
        avoiding UniqueViolationError when the task reruns within the same minute.
        """
        stmt = (
            pg_insert(TradingSignal)
            .values(
                watchlist_id=watchlist_id,
                symbol=symbol,
                signal_type=signal_data["signal_type"],
                signal_strength=signal_data["signal_strength"],
                confidence=signal_data["confidence"],
                recommendation=signal_data["recommendation"],
                indicators_used=signal_data.get("indicators_used"),
                strategy=signal_data["strategy"],
                signal_timestamp=signal_timestamp,
            )
            .on_conflict_do_update(
                constraint="uq_signal_unique",
                set_={
                    "signal_type": signal_data["signal_type"],
                    "signal_strength": signal_data["signal_strength"],
                    "confidence": signal_data["confidence"],
                    "recommendation": signal_data["recommendation"],
                    "indicators_used": signal_data.get("indicators_used"),
                },
            )
            .returning(TradingSignal)
        )
        result = await session.execute(stmt)
        signal = result.scalar_one()
        logger.info(
            f"Signal upserted: {symbol} {signal_data['signal_type']} "
            f"(strength: {signal_data['signal_strength']}, confidence: {signal_data['confidence']})"
        )
        return signal
