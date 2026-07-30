"""Trend-phase classification — pure functions (Direction v3 拍板 2026-07-30).

The v3 platform answers "这只股票处于什么阶段" for every watched name: uptrend /
downtrend / range, from MA structure + slope only (deterministic, no LLM, fully
backtestable — phase accuracy is scored against forward returns in R0-9).

Rules (classic MA-structure definition, evidence returned alongside the label):
  up    : close > slow MA, fast MA > slow MA, slow MA rising over slope_window
  down  : close < slow MA, fast MA < slow MA, slow MA falling
  range : anything else (mixed structure = consolidation)
Warmup rows (MA not yet defined) are labeled 'unknown', never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant import config
from quant.engine import indicators as ind

PHASE_UP = "up"
PHASE_DOWN = "down"
PHASE_RANGE = "range"
PHASE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PhaseParams:
    ma_fast: int = config.MA_FAST
    ma_slow: int = config.MA_SLOW
    slope_window: int = 5          # sessions the slow MA must rise/fall over
    slope_min_pct: float = 0.001   # slope dead-band as a fraction of the slow MA —
                                   # noise-sized wiggles must read as RANGE, not
                                   # as alternating up/down calls


@dataclass(frozen=True)
class PhaseRead:
    """Latest phase + the evidence that produced it (dashboard-explainable)."""
    phase: str
    close: float
    ma_fast: float
    ma_slow: float
    ma_slow_slope: float           # slow-MA change over slope_window, price units
    reason: str

    def to_dict(self) -> dict:
        return {"phase": self.phase, "close": self.close, "ma_fast": self.ma_fast,
                "ma_slow": self.ma_slow, "ma_slow_slope": self.ma_slow_slope,
                "reason": self.reason}


def classify(close: pd.Series, params: PhaseParams = PhaseParams()) -> pd.DataFrame:
    """Per-bar phase frame: columns phase, ma_fast, ma_slow, ma_slow_slope.
    Uses only past+current values — safe to read at any bar without lookahead."""
    ma_f = ind.sma(close, params.ma_fast)
    ma_s = ind.sma(close, params.ma_slow)
    slope = ma_s.diff(params.slope_window)
    band = params.slope_min_pct * ma_s     # dead-band: sub-noise slope = range

    up = (close > ma_s) & (ma_f > ma_s) & (slope > band)
    down = (close < ma_s) & (ma_f < ma_s) & (slope < -band)

    phase = pd.Series(PHASE_RANGE, index=close.index)
    phase[up] = PHASE_UP
    phase[down] = PHASE_DOWN
    phase[ma_s.isna() | slope.isna()] = PHASE_UNKNOWN
    return pd.DataFrame({"phase": phase, "ma_fast": ma_f, "ma_slow": ma_s,
                         "ma_slow_slope": slope})


def latest(close: pd.Series, params: PhaseParams = PhaseParams()) -> PhaseRead:
    """PhaseRead for the most recent bar, with a one-line reason."""
    frame = classify(close, params)
    row = frame.iloc[-1]
    c = float(close.iloc[-1])
    p = row["phase"]
    if p == PHASE_UP:
        reason = (f"close {c:.2f} above rising MA{params.ma_slow} "
                  f"({row['ma_slow']:.2f}), MA{params.ma_fast} on top")
    elif p == PHASE_DOWN:
        reason = (f"close {c:.2f} below falling MA{params.ma_slow} "
                  f"({row['ma_slow']:.2f}), MA{params.ma_fast} underneath")
    elif p == PHASE_RANGE:
        reason = "mixed MA structure — consolidation"
    else:
        reason = "not enough history yet"
    return PhaseRead(phase=str(p), close=c, ma_fast=float(row["ma_fast"]),
                     ma_slow=float(row["ma_slow"]),
                     ma_slow_slope=float(row["ma_slow_slope"]), reason=reason)
