"""1min strategies — the opening-range breakout and its nightly shortlist rank.

Pure: frozen param dataclasses plus the per-bar score, the 1min ATR and the
shortlist ranking, no I/O. Every function reads only rows at or before the bar
it judges, so the live engine and the backtest score a minute identically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ORBSignal:
    range_minutes: int = 15
    breakout_buffer_pct: float = 0.0005
    entry_window_minutes: int = 120
    min_volume_ratio: float = 1.0
    atr_period: int = 14


@dataclass(frozen=True)
class RankParams:
    rank_window: int = 5
    watch_max: int = 10


@dataclass(frozen=True)
class ORBConfig:
    signal: ORBSignal
    rank: RankParams


def atr_window(bars: pd.DataFrame, period: int) -> float | None:
    """Wilder ATR (alpha = 1/period, seeded on the first true range) over the
    last 4*period bars; None when fewer bars exist. Equals
    ``indicators.atr`` evaluated on the same window."""
    n = 4 * period
    if len(bars) < n:
        return None
    tail = bars.iloc[-n:]
    h = tail["high"].to_numpy(dtype="float64")
    lo = tail["low"].to_numpy(dtype="float64")
    c = tail["close"].to_numpy(dtype="float64")
    tr = h - lo
    prev = c[:-1]
    tr[1:] = np.maximum.reduce([tr[1:], np.abs(h[1:] - prev), np.abs(lo[1:] - prev)])
    a = 1.0 / period
    y = tr[0]
    for x in tr[1:]:
        y = (1.0 - a) * y + a * x
    return float(y)


def orb_score(today: pd.DataFrame, t: pd.Timestamp, rth_open: pd.Timestamp,
              atr: float | None, p: ORBSignal) -> float | None:
    """Breakout strength of bar t over the opening range, in ATRs (capped at
    10), or None when t is not the first close above the range level. Rows
    after t are ignored."""
    range_end = rth_open + pd.Timedelta(minutes=p.range_minutes)
    if not range_end <= t < rth_open + pd.Timedelta(minutes=p.entry_window_minutes):
        return None
    if atr is None or atr <= 0:
        return None
    ts = today["ts"].to_numpy(dtype="datetime64[ns]").astype("int64")
    t_ns, open_ns, end_ns = t.value, rth_open.value, range_end.value
    upto = ts <= t_ns
    in_range = upto & (ts >= open_ns) & (ts < end_ns)
    if int(in_range.sum()) < math.ceil(p.range_minutes / 2):
        return None
    now = np.flatnonzero(upto & (ts == t_ns))
    if not len(now):
        return None
    high = today["high"].to_numpy(dtype="float64")
    close = today["close"].to_numpy(dtype="float64")
    level = float(high[in_range].max()) * (1.0 + p.breakout_buffer_pct)
    if (close[upto & (ts >= end_ns) & (ts < t_ns)] > level).any():
        return None
    close_t = float(close[now[0]])
    if close_t <= level:
        return None
    if p.min_volume_ratio > 0:
        volume = today["volume"].to_numpy(dtype="float64")
        if float(volume[now[0]]) < p.min_volume_ratio * float(volume[in_range].mean()):
            return None
    return min((close_t - level) / atr, 10.0)


def rank_pool(values: pd.Series, k: int) -> list[tuple[str, float]]:
    vals = values.dropna()
    ranked = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(str(sym), float(v)) for sym, v in ranked[:k]]
