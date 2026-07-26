"""Technical indicators — pure functions on price Series (design §6.3).

No I/O, no state: same input -> same output. Indicators are computed in memory
and never persisted (would drift from strategy params). Roles: MA=direction
gate, MACD=momentum, RSI=filter, ATR=volatility scale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing (EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out[avg_loss == 0] = 100.0  # no losses -> RSI 100
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Robust-ish rolling z-score (design §6.3: z-score not min-max, to resist
    outliers and avoid lookahead). Uses only past+current values in the window."""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    return (series - mean) / std.replace(0.0, np.nan)


def logistic(x: pd.Series | float) -> pd.Series | float:
    """Squash to (0, 1). Used to compress a z-score into a bounded score."""
    return 1.0 / (1.0 + np.exp(-x))
