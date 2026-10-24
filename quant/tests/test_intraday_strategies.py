"""P5a: the 1min ORB score, the Wilder 1min ATR and the shortlist rank."""

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from quant.data import calendar
from quant.engine import indicators
from quant.engine.intraday_strategies import (ORBConfig, ORBSignal, RankParams, atr_window,
                                              orb_score, rank_pool)

OPEN = calendar.rth_bounds("2026-10-21")[0]
P = ORBSignal()


def _m(i: int) -> pd.Timestamp:
    return OPEN + pd.Timedelta(minutes=i)


def _bars(rows) -> pd.DataFrame:
    """rows: (minute offset, high, close, volume)."""
    return pd.DataFrame({"ts": [_m(i) for i, *_ in rows],
                         "open": [c for *_, c, _ in rows],
                         "high": [h for _, h, _, _ in rows],
                         "low": [c - 0.2 for *_, c, _ in rows],
                         "close": [c for *_, c, _ in rows],
                         "volume": [float(v) for *_, v in rows]})


def _day(breakouts=None, range_bars=15, until=130, skip=()):
    """15 range bars (high 100.5), then quiet bars closing at 100 unless
    ``breakouts`` maps a minute to its close."""
    breakouts = breakouts or {}
    rows = [(i, 100.5, 100.0, 1000) for i in range(range_bars)]
    for i in range(15, until):
        if i in skip:
            continue
        c = breakouts.get(i, 100.0)
        rows.append((i, max(c, 100.2), c, 1000))
    return _bars(rows)


LEVEL = 100.5 * (1 + P.breakout_buffer_pct)


def test_atr_window_equals_pandas_wilder_on_the_same_window():
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.3, 100))
    high = close + rng.uniform(0, 0.5, 100)
    low = close - rng.uniform(0, 0.5, 100)
    df = pd.DataFrame({"high": high, "low": low, "close": close})
    tail = df.tail(56)
    b = float(indicators.atr(tail["high"], tail["low"], tail["close"], 14).iloc[-1])
    a = atr_window(df, 14)
    assert abs(a - b) <= 1e-12 * max(1.0, abs(b))


def test_atr_window_needs_four_periods_of_bars():
    df = pd.DataFrame({"high": np.ones(55) * 2, "low": np.ones(55), "close": np.ones(55) * 1.5})
    assert atr_window(df, 14) is None
    assert atr_window(pd.concat([df, df.tail(1)]), 14) == pytest.approx(1.0)


def test_too_few_range_bars_is_no_signal():
    seven = _bars([(i, 100.5, 100.0, 1000) for i in range(7)] + [(20, 101.0, 101.0, 1000)])
    eight = _bars([(i, 100.5, 100.0, 1000) for i in range(8)] + [(20, 101.0, 101.0, 1000)])
    assert orb_score(seven, _m(20), OPEN, 0.5, P) is None
    assert orb_score(eight, _m(20), OPEN, 0.5, P) is not None


def test_only_the_first_crossing_counts():
    day = _day({20: 101.0, 25: 101.0})
    assert orb_score(day, _m(20), OPEN, 0.5, P) == pytest.approx((101.0 - LEVEL) / 0.5)
    assert orb_score(day, _m(25), OPEN, 0.5, P) is None


def test_volume_ratio_blocks_a_thin_breakout():
    day = _day({20: 101.0})
    day.loc[day["ts"] == _m(20), "volume"] = 500.0
    assert orb_score(day, _m(20), OPEN, 0.5, P) is None


def test_zero_ratio_ignores_volume():
    day = _day({20: 101.0})
    day.loc[day["ts"] == _m(20), "volume"] = 0.0
    p = ORBSignal(min_volume_ratio=0.0)
    assert orb_score(day, _m(20), OPEN, 0.5, p) is not None


def test_entry_window_lower_edge():
    assert orb_score(_day({14 + 15: 101.0}), _m(29), OPEN, 0.5, P) is not None
    early = _day(range_bars=14)
    early = pd.concat([early, _bars([(14, 100.5, 101.0, 1000)])]).sort_values("ts")
    assert orb_score(early, _m(14), OPEN, 0.5, P) is None
    assert orb_score(_day({15: 101.0}), _m(15), OPEN, 0.5, P) is not None


def test_entry_window_upper_edge():
    assert orb_score(_day({120: 101.0}), _m(120), OPEN, 0.5, P) is None
    assert orb_score(_day({119: 101.0}), _m(119), OPEN, 0.5, P) is not None


def test_minute_without_a_bar_is_no_signal():
    day = _day({20: 101.0}, skip=(21,))
    assert orb_score(day, _m(21), OPEN, 0.5, P) is None


@pytest.mark.parametrize("atr", [None, 0.0, -1.0])
def test_missing_atr_is_no_signal(atr):
    assert orb_score(_day({20: 101.0}), _m(20), OPEN, atr, P) is None


def test_score_is_capped_at_ten():
    assert orb_score(_day({20: 150.0}), _m(20), OPEN, 0.5, P) == 10.0


def test_buffer_raises_the_level():
    day = _day({20: 101.0})
    assert orb_score(day, _m(20), OPEN, 0.5, P) is not None
    assert orb_score(day, _m(20), OPEN, 0.5, ORBSignal(breakout_buffer_pct=0.01)) is None


def test_rank_drops_nan_and_breaks_ties_by_symbol():
    vals = pd.Series({"ZZZ": 0.05, "AAA": 0.05, "MMM": np.nan, "BBB": 0.07, "CCC": 0.01})
    assert rank_pool(vals, 3) == [("BBB", 0.07), ("AAA", 0.05), ("ZZZ", 0.05)]
    assert all(type(v) is float for _, v in rank_pool(vals, 10))
    assert rank_pool(vals, 10)[-1] == ("CCC", 0.01)


def test_rows_after_t_never_change_the_score():
    day = _day({20: 101.0}, until=21)
    later = _day({20: 101.0, 21: 99.0, 22: 120.0}, until=60)
    for t in (_m(18), _m(20)):
        assert orb_score(day, t, OPEN, 0.5, P) == orb_score(later, t, OPEN, 0.5, P)


def test_defaults_are_the_d3_contract():
    assert asdict(ORBSignal()) == {"range_minutes": 15, "breakout_buffer_pct": 0.0005,
                                   "entry_window_minutes": 120, "min_volume_ratio": 1.0,
                                   "atr_period": 14}
    assert asdict(RankParams()) == {"rank_window": 5, "watch_max": 10}
    assert ORBConfig(ORBSignal(), RankParams()).signal == ORBSignal()
