"""Direction v3 engine modules: trend-phase classification + top/bottom zones.
Constructed series, no I/O — purity enforced alongside the other engine tests."""

import numpy as np
import pandas as pd
import pytest

from quant.engine import phase, zones


# --- phase ----------------------------------------------------------------

def _p():
    return phase.PhaseParams(ma_fast=3, ma_slow=5, slope_window=3)


def test_phase_uptrend_downtrend_range():
    up = pd.Series([10 + 0.5 * i for i in range(30)])
    down = pd.Series([40 - 0.5 * i for i in range(30)])
    flat = pd.Series([20.0 + (0.05 if i % 2 else -0.05) for i in range(30)])

    assert phase.classify(up, _p())["phase"].iloc[-1] == phase.PHASE_UP
    assert phase.classify(down, _p())["phase"].iloc[-1] == phase.PHASE_DOWN
    assert phase.classify(flat, _p())["phase"].iloc[-1] == phase.PHASE_RANGE


def test_phase_warmup_is_unknown_never_guessed():
    short = pd.Series([10.0, 11.0, 12.0])
    frame = phase.classify(short, _p())
    assert (frame["phase"] == phase.PHASE_UNKNOWN).all()


def test_phase_latest_read_has_evidence():
    up = pd.Series([10 + 0.5 * i for i in range(30)])
    read = phase.latest(up, _p())
    assert read.phase == phase.PHASE_UP
    assert read.ma_fast > read.ma_slow
    assert read.ma_slow_slope > 0
    assert "rising" in read.reason
    assert set(read.to_dict()) == {"phase", "close", "ma_fast", "ma_slow",
                                   "ma_slow_slope", "reason"}


# --- zones ----------------------------------------------------------------

def _zigzag():
    """Oscillates between ~10 and ~20 twice, then sits mid-range at 15:
    swing highs cluster ~20, swing lows cluster ~10."""
    legs = [np.linspace(10, 20, 8), np.linspace(20, 10, 8),
            np.linspace(10, 20, 8), np.linspace(20, 15, 8)]
    close = pd.Series(np.concatenate(legs))
    high = close + 0.2
    low = close - 0.2
    return high, low, close


def test_zones_nearest_bands_around_price():
    high, low, close = _zigzag()
    read = zones.compute_zones(high, low, close, atr_now=1.0,
                               params=zones.ZoneParams(swing_window=3, max_lookback=100))
    assert read.resistance is not None and read.support is not None
    # price 15: resistance band around the ~20 peaks, support around the ~10 troughs
    assert 18.5 <= read.resistance.lo <= read.resistance.hi <= 21.5
    assert 8.5 <= read.support.lo <= read.support.hi <= 11.5
    assert read.resistance.touches >= 2          # both peaks clustered into one zone
    assert read.resistance.lo > float(close.iloc[-1]) > read.support.hi


def test_zones_blue_sky_returns_none():
    # rising WITH pullbacks, closing at the all-time high: dips below give
    # support, but nothing above -> no invented resistance
    t = np.arange(40, dtype=float)
    close = pd.Series(10 + 0.4 * t - np.where(t % 7 == 3, 1.5, 0.0))
    read = zones.compute_zones(close + 0.1, close - 0.1, close, atr_now=0.5,
                               params=zones.ZoneParams(swing_window=3))
    assert read.resistance is None
    assert read.support is not None              # pullback structure below


def test_zones_recent_unconfirmed_swing_is_ignored():
    # a brand-new peak inside the last k bars must NOT create resistance yet
    k = 4
    base = list(np.linspace(10, 12, 30))
    spike = base + [15.0, 14.0]                  # peak 2 bars ago (< k) — unconfirmed
    close = pd.Series(spike)
    high = close + 0.1; low = close - 0.1
    read = zones.compute_zones(high, low, close, atr_now=0.5,
                               params=zones.ZoneParams(swing_window=k))
    if read.resistance is not None:              # any zone must come from OLD structure
        assert read.resistance.hi < 15.0


def test_zones_insufficient_history_returns_empty():
    close = pd.Series([10.0, 11.0, 10.5])
    read = zones.compute_zones(close, close, close, atr_now=1.0)
    assert read.resistance is None and read.support is None


def test_zone_read_serializes():
    high, low, close = _zigzag()
    d = zones.compute_zones(high, low, close, atr_now=1.0).to_dict()
    assert set(d) == {"resistance", "support", "swing_highs", "swing_lows"}
