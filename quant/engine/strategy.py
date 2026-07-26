"""StrategyEngine — pure bars -> Signal (架构铁律①: backtest & live share this).

The composite technical strategy (design §6.3), roles NOT equal-weighted:
  MA structure  -> direction gate (no long in a downtrend)
  MACD hist     -> momentum -> confidence (z-score -> logistic, §6.3)
  RSI           -> filter (deduct when overbought)
  ATR           -> volatility scale -> expected_move + stop distance

compute_signals() vectorizes the whole series (one pass per symbol for the
backtest); evaluate() wraps the last row into a Signal. Same logic path, so the
lookahead bias check (R0-8) validates both at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from quant import config
from quant.engine import indicators as ind
from quant.engine.signal import Direction, Signal


@dataclass(frozen=True)
class StrategyParams:
    ma_fast: int = config.MA_FAST
    ma_slow: int = config.MA_SLOW
    macd_fast: int = config.MACD_FAST
    macd_slow: int = config.MACD_SLOW
    macd_signal: int = config.MACD_SIGNAL
    rsi_period: int = config.RSI_PERIOD
    atr_period: int = config.ATR_PERIOD
    zscore_window: int = 60            # rolling window for MACD-hist z-score
    rsi_overbought: float = 70.0       # penalty starts here
    rsi_penalty_floor: float = 0.2     # confidence factor never below this
    stop_atr_mult: float = 2.0         # stop distance = mult * ATR (sizing/exits use it)
    expected_move_atr_mult: float = 1.0

    @property
    def warmup(self) -> int:
        """Bars needed before signals are meaningful."""
        return max(self.ma_slow, self.macd_slow + self.macd_signal,
                   self.rsi_period, self.atr_period) + self.zscore_window


def compute_signals(bars: pd.DataFrame, params: StrategyParams = StrategyParams()) -> pd.DataFrame:
    """Vectorized per-bar signal fields. Returns a frame indexed like bars with
    columns: ts, close, ma_fast, ma_slow, macd_hist, rsi, atr, direction,
    confidence, expected_move, stop_distance. Uses only past+current data."""
    if bars.empty:
        return pd.DataFrame()
    close = bars["close"].astype(float).reset_index(drop=True)
    high = bars["high"].astype(float).reset_index(drop=True)
    low = bars["low"].astype(float).reset_index(drop=True)

    ma_f = ind.sma(close, params.ma_fast)
    ma_s = ind.sma(close, params.ma_slow)
    _, _, hist = ind.macd(close, params.macd_fast, params.macd_slow, params.macd_signal)
    rsi = ind.rsi(close, params.rsi_period)
    atr = ind.atr(high, low, close, params.atr_period)

    # direction from MA structure (gate)
    up = (close > ma_s) & (ma_f > ma_s)
    down = (close < ma_s) & (ma_f < ma_s)
    direction = np.where(up, Direction.UP.value,
                         np.where(down, Direction.DOWN.value, Direction.FLAT.value))

    # confidence: MACD momentum (z-score -> logistic) filtered by RSI overbought
    momentum = ind.logistic(ind.rolling_zscore(hist, params.zscore_window))  # (0,1)
    over = (rsi - params.rsi_overbought).clip(lower=0.0)
    span = (100.0 - params.rsi_overbought)
    rsi_factor = (1.0 - over / span).clip(lower=params.rsi_penalty_floor, upper=1.0)
    confidence = (100.0 * momentum * rsi_factor)

    out = pd.DataFrame({
        "ts": bars["ts"].reset_index(drop=True),
        "close": close, "ma_fast": ma_f, "ma_slow": ma_s,
        "macd_hist": hist, "rsi": rsi, "atr": atr,
        "direction": direction,
        "confidence": confidence.where(up, 0.0),   # confidence only meaningful for longs
        "expected_move": atr * params.expected_move_atr_mult,
        "stop_distance": atr * params.stop_atr_mult,
    })
    return out


def evaluate(bars: pd.DataFrame, params: StrategyParams = StrategyParams(), *,
             symbol: str, as_of: date | None = None) -> Signal | None:
    """Pure bars -> Signal at the LAST bar. None if not enough warmup."""
    sig = compute_signals(bars, params)
    if sig.empty or len(sig) < params.warmup:
        return None
    row = sig.iloc[-1]
    if pd.isna(row["atr"]) or pd.isna(row["ma_slow"]):
        return None
    gen = as_of or bars["ts"].iloc[-1].date()
    d = Direction(row["direction"])
    reason = _reason(row, d)
    return Signal(
        symbol=symbol, direction=d,
        confidence=float(round(row["confidence"], 2)),
        expected_move=float(round(row["expected_move"], 4)),
        atr=float(round(row["atr"], 4)),
        last_close=float(row["close"]),
        generated_at=gen,
        source_model="cat.v1.composite",
        reason=reason,
        components={
            "ma_fast": float(row["ma_fast"]), "ma_slow": float(row["ma_slow"]),
            "macd_hist": float(row["macd_hist"]), "rsi": float(row["rsi"]),
            "atr": float(row["atr"]), "stop_distance": float(row["stop_distance"]),
        },
    )


def _reason(row, d: Direction) -> str:
    return (f"{d.value.upper()}: close {row['close']:.2f} vs MA20 {row['ma_slow']:.2f}, "
            f"MA5 {row['ma_fast']:.2f}; RSI {row['rsi']:.1f}; "
            f"conf {row['confidence']:.0f}; ATR {row['atr']:.2f}")
