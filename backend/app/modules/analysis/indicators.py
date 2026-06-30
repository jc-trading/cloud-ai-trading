"""
Technical indicators calculation using pandas and numpy.
TA-Lib can be enabled when available on the system.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cloud_ai_trading.indicators")


def calculate_indicators(candles: list[dict]) -> dict:
    """
    Calculate technical indicators from OHLCV candle data.

    Args:
        candles: List of dicts with keys: timestamp, open, high, low, close, volume

    Returns:
        dict of calculated indicator values
    """
    if len(candles) < 50:
        logger.warning(f"Insufficient candle data ({len(candles)} candles). Need at least 50.")
        return {}

    df = pd.DataFrame(candles)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    indicators = {}

    # Current price
    indicators["current_price"] = float(close.iloc[-1])

    # --- RSI (Relative Strength Index) ---
    indicators["rsi"] = round(_rsi(close, 14), 2)

    # --- MACD ---
    macd_line, macd_signal, macd_histogram = _macd(close)
    indicators["macd_line"] = round(macd_line, 6)
    indicators["macd_signal"] = round(macd_signal, 6)
    indicators["macd_histogram"] = round(macd_histogram, 6)

    # --- EMA ---
    indicators["ema20"] = round(float(close.ewm(span=20).mean().iloc[-1]), 4)
    indicators["ema50"] = round(float(close.ewm(span=50).mean().iloc[-1]), 4)

    # --- Bollinger Bands ---
    bb_middle = close.rolling(window=20).mean().iloc[-1]
    bb_std = close.rolling(window=20).std().iloc[-1]
    indicators["bb_upper"] = round(float(bb_middle + 2 * bb_std), 4)
    indicators["bb_middle"] = round(float(bb_middle), 4)
    indicators["bb_lower"] = round(float(bb_middle - 2 * bb_std), 4)

    # --- Volume ---
    indicators["volume"] = float(volume.iloc[-1])
    indicators["avg_volume"] = round(float(volume.rolling(window=20).mean().iloc[-1]), 2)

    # --- 24h Change ---
    if len(close) >= 2:
        indicators["change_24h"] = round(
            (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100, 2
        )

    # --- SMA ---
    indicators["sma20"] = round(float(close.rolling(window=20).mean().iloc[-1]), 4)
    indicators["sma50"] = round(float(close.rolling(window=50).mean().iloc[-1]), 4)

    # --- ATR (Average True Range) ---
    indicators["atr"] = round(_atr(high, low, close, 14), 4)

    # --- Stochastic ---
    stoch_k, stoch_d = _stochastic(high, low, close, 14, 3)
    indicators["stoch_k"] = round(stoch_k, 2)
    indicators["stoch_d"] = round(stoch_d, 2)

    return indicators


def _rsi(close: pd.Series, period: int = 14) -> float:
    """Calculate RSI."""
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """Calculate MACD line, signal line, and histogram."""
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line
    return (
        float(macd_line.iloc[-1]),
        float(signal_line.iloc[-1]),
        float(histogram.iloc[-1]),
    )


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Calculate Average True Range."""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(window=period).mean().iloc[-1])


def _stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3
) -> tuple:
    """Calculate Stochastic Oscillator %K and %D."""
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    stoch_k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    stoch_d = stoch_k.rolling(window=d_period).mean()
    return float(stoch_k.iloc[-1]), float(stoch_d.iloc[-1])
