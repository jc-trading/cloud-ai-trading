"""Technical indicators calculator using pandas and numpy."""

from decimal import Decimal
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from app.modules.market_data.schemas import OHLCVCandle as OHLCVSchema


class TechnicalIndicatorCalculator:
    """Calculate various technical indicators from OHLCV data."""

    @staticmethod
    def calculate_ema(prices: List[Decimal], period: int) -> Optional[Decimal]:
        """Calculate Exponential Moving Average (EMA)."""
        if len(prices) < period:
            return None

        prices_array = np.array([float(p) for p in prices[-period * 2:]], dtype=np.float64)
        if len(prices_array) < period:
            return None

        ema = prices_array[0]
        multiplier = 2.0 / (period + 1)

        for price in prices_array[1:]:
            ema = price * multiplier + ema * (1 - multiplier)

        return Decimal(str(round(ema, 8)))

    @staticmethod
    def calculate_rsi(prices: List[Decimal], period: int = 14) -> Optional[Decimal]:
        """Calculate Relative Strength Index (RSI)."""
        if len(prices) < period + 1:
            return None

        prices_array = np.array([float(p) for p in prices], dtype=np.float64)
        deltas = np.diff(prices_array)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return Decimal('100') if avg_gain > 0 else Decimal('0')

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return Decimal(str(round(rsi, 2)))

    @staticmethod
    def calculate_atr(highs: List[Decimal], lows: List[Decimal], closes: List[Decimal], period: int = 14) -> Optional[Decimal]:
        """Calculate Average True Range (ATR)."""
        if len(highs) < period:
            return None

        highs_array = np.array([float(h) for h in highs[-period * 2:]], dtype=np.float64)
        lows_array = np.array([float(l) for l in lows[-period * 2:]], dtype=np.float64)
        closes_array = np.array([float(c) for c in closes[-period * 2:]], dtype=np.float64)

        tr1 = highs_array - lows_array
        tr2 = np.abs(highs_array - np.roll(closes_array, 1))
        tr3 = np.abs(lows_array - np.roll(closes_array, 1))

        tr = np.maximum(tr1, np.maximum(tr2, tr3))[1:]  # Skip first NaN
        atr = np.mean(tr[-period:])

        return Decimal(str(round(atr, 8)))

    @staticmethod
    def calculate_bollinger_bands(
        prices: List[Decimal], period: int = 20, num_std: float = 2.0
    ) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        """Calculate Bollinger Bands (upper, middle, lower, width, and position)."""
        if len(prices) < period:
            return None, None, None, None, None

        prices_array = np.array([float(p) for p in prices[-period:]], dtype=np.float64)

        middle = np.mean(prices_array)
        std_dev = np.std(prices_array)

        upper = middle + (num_std * std_dev)
        lower = middle - (num_std * std_dev)
        width = upper - lower

        # Calculate position (0-100, where 50 is middle)
        if width == 0:
            position = 50.0
        else:
            position = ((float(prices[-1]) - lower) / width) * 100

        return (
            Decimal(str(round(upper, 8))),
            Decimal(str(round(middle, 8))),
            Decimal(str(round(lower, 8))),
            Decimal(str(round(width, 8))),
            Decimal(str(round(position, 2))),
        )

    @staticmethod
    def calculate_macd(
        prices: List[Decimal], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        """Calculate MACD (Moving Average Convergence Divergence)."""
        if len(prices) < slow:
            return None, None, None

        prices_array = np.array([float(p) for p in prices], dtype=np.float64)

        # Calculate EMAs
        ema_fast = TechnicalIndicatorCalculator._ema_internal(prices_array, fast)
        ema_slow = TechnicalIndicatorCalculator._ema_internal(prices_array, slow)

        if ema_fast is None or ema_slow is None:
            return None, None, None

        # MACD line
        macd_line = ema_fast - ema_slow

        # Signal line (EMA of MACD). Early windows do not have enough values
        # for the slow EMA, so only include points where both EMAs exist.
        macd_values = []
        for i in range(len(prices_array)):
            ema_fast_i = TechnicalIndicatorCalculator._ema_internal(
                prices_array[: i + 1], fast
            )
            ema_slow_i = TechnicalIndicatorCalculator._ema_internal(
                prices_array[: i + 1], slow
            )
            if ema_fast_i is not None and ema_slow_i is not None:
                macd_values.append(ema_fast_i - ema_slow_i)

        if len(macd_values) < signal:
            return None, None, None

        macd_array = np.array(macd_values, dtype=np.float64)
        signal_line = TechnicalIndicatorCalculator._ema_internal(macd_array, signal)

        if signal_line is None:
            return None, None, None

        # Histogram
        histogram = macd_line - signal_line

        return (
            Decimal(str(round(macd_line, 8))),
            Decimal(str(round(signal_line, 8))),
            Decimal(str(round(histogram, 8))),
        )

    @staticmethod
    def _ema_internal(values: np.ndarray, period: int) -> Optional[float]:
        """Internal EMA calculation for numpy arrays."""
        if len(values) < period:
            return None

        ema = np.mean(values[:period])
        multiplier = 2.0 / (period + 1)

        for i in range(period, len(values)):
            ema = values[i] * multiplier + ema * (1 - multiplier)

        return ema


class IndicatorAnalyzer:
    """Analyze indicators for trading signals and alerts."""

    @staticmethod
    def get_trend(ema_12: Optional[Decimal], ema_26: Optional[Decimal]) -> str:
        """Determine trend based on EMA crossover."""
        if ema_12 is None or ema_26 is None:
            return "unknown"

        if ema_12 > ema_26:
            return "bullish"
        elif ema_12 < ema_26:
            return "bearish"
        else:
            return "neutral"

    @staticmethod
    def is_overbought(rsi: Optional[Decimal], threshold: int = 70) -> bool:
        """Check if RSI indicates overbought condition."""
        if rsi is None:
            return False
        return rsi >= threshold

    @staticmethod
    def is_oversold(rsi: Optional[Decimal], threshold: int = 30) -> bool:
        """Check if RSI indicates oversold condition."""
        if rsi is None:
            return False
        return rsi <= threshold

    @staticmethod
    def check_bollinger_band_break(
        price: Decimal, bb_upper: Optional[Decimal], bb_lower: Optional[Decimal]
    ) -> Optional[str]:
        """Check if price breaks Bollinger Bands."""
        if bb_upper is None or bb_lower is None:
            return None

        if price > bb_upper:
            return "upper_break"
        elif price < bb_lower:
            return "lower_break"
        return None

    @staticmethod
    def check_macd_signal(
        macd: Optional[Decimal], signal: Optional[Decimal], prev_macd: Optional[Decimal], prev_signal: Optional[Decimal]
    ) -> Optional[str]:
        """Check for MACD signal line crossover."""
        if macd is None or signal is None or prev_macd is None or prev_signal is None:
            return None

        if prev_macd <= prev_signal and macd > signal:
            return "bullish_crossover"
        elif prev_macd >= prev_signal and macd < signal:
            return "bearish_crossover"

        return None
