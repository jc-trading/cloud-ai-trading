"""period_key must be derived from the ET calendar date, never UTC (方案 Phase 2 #1).

Two ways a UTC bucket breaks the layout:
  * during EST the 16:00-20:00 ET after-hours session runs past UTC midnight, so
    one trading day would be split across two 1min files;
  * daily bars are anchored at ET midnight (04:00Z EDT / 05:00Z EST), so a
    year boundary must land on the ET year, not on the UTC one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.data import store


def _utc(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts, tz="UTC")


@pytest.mark.parametrize("utc_ts, expected", [
    # EST after-hours: 19:30 ET on 2026-01-05 is 00:30Z on 2026-01-06
    ("2026-01-06T00:30:00Z", "2026-01-05"),
    ("2026-01-06T01:00:00Z", "2026-01-05"),   # 20:00 ET, the session window edge
    # EDT after-hours: 19:30 ET on 2026-07-24 is 23:30Z the same UTC day
    ("2026-07-24T23:30:00Z", "2026-07-24"),
    # regular session, both tz regimes
    ("2026-01-05T14:30:00Z", "2026-01-05"),
    ("2026-07-24T13:30:00Z", "2026-07-24"),
])
def test_1min_period_key_follows_et_date(utc_ts, expected):
    assert store.period_key(_utc(utc_ts), "1min") == expected


@pytest.mark.parametrize("utc_ts, expected", [
    ("2025-12-31T05:00:00Z", "2025"),   # EST daily bar for ET 2025-12-31
    ("2026-01-02T05:00:00Z", "2026"),   # EST daily bar for ET 2026-01-02
    ("2025-07-01T04:00:00Z", "2025"),   # EDT daily bar
])
def test_daily_period_key_is_the_et_year(utc_ts, expected):
    assert store.period_key(_utc(utc_ts), "daily") == expected


def test_1hour_period_key_is_the_et_month():
    assert store.period_key(_utc("2026-02-01T00:30:00Z"), "1hour") == "2026-01"
    assert store.period_key(_utc("2026-02-02T14:30:00Z"), "1hour") == "2026-02"


def test_split_periods_keeps_an_est_evening_session_in_one_file():
    ts = pd.DatetimeIndex([
        pd.Timestamp("2026-01-05 09:30", tz="America/New_York"),
        pd.Timestamp("2026-01-05 16:30", tz="America/New_York"),
        pd.Timestamp("2026-01-05 19:59", tz="America/New_York"),
    ]).tz_convert("UTC")
    df = pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                       "close": 1.0, "volume": 1.0, "vwap": 1.0, "trade_count": 1.0})
    chunks = store.split_periods(df, "1min")
    assert [key for key, _ in chunks] == ["2026-01-05"]
    assert len(chunks[0][1]) == 3


def test_split_periods_splits_daily_on_the_year_boundary():
    ts = pd.DatetimeIndex(["2025-12-31T05:00:00Z", "2026-01-02T05:00:00Z"])
    df = pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                       "close": 1.0, "volume": 1.0, "vwap": 1.0, "trade_count": 1.0})
    assert [key for key, _ in store.split_periods(df, "daily")] == ["2025", "2026"]


def test_unknown_timeframe_is_refused():
    with pytest.raises(ValueError):
        store.period_key(_utc("2026-01-05T14:30:00Z"), "5m")
