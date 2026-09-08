"""R0-5 get_bars() tests: single entry point, read-time adjustment, session
filter, native 1hour reads and the 1min->higher-timeframe resample. Synthetic
parquet, no network, in-memory file registry."""

from datetime import date

import pandas as pd
import pytest

from quant import config
from quant.data import bars, corporate_actions, store

_SIP = config.PROVIDER_HISTORICAL


def _et_bars(day, hhmm_list, closes):
    ts = pd.DatetimeIndex(
        [pd.Timestamp(f"{day} {hm}", tz="America/New_York") for hm in hhmm_list]
    ).tz_convert("UTC")
    n = len(closes)
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c + 2 for c in closes],
        "low": [c - 2 for c in closes], "close": closes,
        "volume": [100 * (i + 1) for i in range(n)], "vwap": closes,
        "trade_count": [5] * n,
    })


def _minutes(day, start_hhmm, closes):
    start = pd.Timestamp(f"{day} {start_hhmm}", tz="America/New_York")
    hhmm = [(start + pd.Timedelta(minutes=i)).strftime("%H:%M") for i in range(len(closes))]
    return _et_bars(day, hhmm, closes)


def test_only_imports_get_bars(tmp_store):
    # daily: write RAW, a 2:1 split, then read adjusted vs raw via get_bars only
    df = pd.DataFrame({
        "ts": pd.DatetimeIndex([pd.Timestamp(f"2024-01-0{d} 00:00", tz="America/New_York")
                                for d in (2, 3, 4, 5)]).tz_convert("UTC"),
        "open": [200.0, 202.0, 100.0, 101.0], "high": [201, 203, 101, 102],
        "low": [199, 201, 99, 100], "close": [200.0, 202.0, 100.0, 101.0],
        "volume": [1_000, 1_000, 2_000, 2_000], "vwap": [200, 202, 100, 101],
        "trade_count": [10, 10, 10, 10],
    })
    store.write_frame("ZZZ", "daily", df, provider=_SIP)
    corporate_actions.store_actions([{
        "symbol": "ZZZ", "ex_date": date(2024, 1, 4), "action_type": "split",
        "ratio": 2.0, "cash_amount": None}], db_path=config.ACTIONS_DB)

    raw = bars.get_bars("ZZZ", "1d", adjust="none")
    adj = bars.get_bars("ZZZ", "1d", adjust="split_div")
    assert raw["close"].tolist() == [200.0, 202.0, 100.0, 101.0]
    # pre-split halved -> continuous series, no 2x jump
    assert adj["close"].tolist() == [100.0, 101.0, 100.0, 101.0]


def test_native_1hour_is_read_not_resampled(tmp_store):
    hour = _et_bars("2026-07-24", ["09:30", "10:30", "11:30"], [10, 11, 12])
    store.write_frame("HHH", "1hour", hour, provider=_SIP)
    got = bars.get_bars("HHH", "1h", start="2026-07-24", end="2026-07-24",
                        adjust="none", session="regular")
    assert got["close"].tolist() == [10, 11, 12]
    # nothing was written to 1min: 1h no longer comes from a resample
    assert not store.bar_dir("HHH", "1min").exists()


def test_resample_1min_to_30m_consistency(tmp_store):
    closes = [10, 12, 9, 15, 11, 8, 14, 13, 7, 16, 10, 12] * 5   # 60 one-minute bars
    store.write_frame("QQQ", "1min", _minutes("2026-07-24", "09:30", closes),
                      provider=_SIP)

    half = bars.get_bars("QQQ", "30m", start="2026-07-24", end="2026-07-24",
                         adjust="none", session="regular")
    assert len(half) == 2
    first = half.iloc[0]
    assert first["open"] == closes[0]
    assert first["close"] == closes[29]
    assert first["high"] == max(closes[:30]) + 2
    assert first["low"] == min(closes[:30]) - 2
    assert first["volume"] == sum(100 * (i + 1) for i in range(30))


def test_end_date_is_inclusive(tmp_store):
    # daily bars anchor at ET midnight (04/05:00Z); a date-like `end` must
    # include that day's bar, not silently drop it (review F9)
    df = pd.DataFrame({
        "ts": pd.DatetimeIndex([pd.Timestamp(f"2024-03-0{d} 00:00", tz="America/New_York")
                                for d in (4, 5, 6)]).tz_convert("UTC"),
        "open": [10.0, 11.0, 12.0], "high": [10, 11, 12], "low": [10, 11, 12],
        "close": [10.0, 11.0, 12.0], "volume": [1, 1, 1], "vwap": [10, 11, 12],
        "trade_count": [1, 1, 1],
    })
    store.write_frame("INC", "daily", df, provider=_SIP)
    got = bars.get_bars("INC", "1d", start="2024-03-04", end="2024-03-05", adjust="none")
    assert len(got) == 2                      # the 03-05 bar is included
    assert got["close"].tolist() == [10.0, 11.0]


def test_unadjusted_series_warns(tmp_store):
    # split-sized jump + zero cached actions + adjust requested -> loud warning
    df = pd.DataFrame({
        "ts": pd.DatetimeIndex([pd.Timestamp(f"2024-01-0{d} 00:00", tz="America/New_York")
                                for d in (2, 3)]).tz_convert("UTC"),
        "open": [400.0, 100.0], "high": [401, 101], "low": [399, 99],
        "close": [400.0, 100.0], "volume": [1, 1], "vwap": [400, 100],
        "trade_count": [1, 1],
    })
    store.write_frame("NOSYNC", "daily", df, provider=_SIP)
    with pytest.warns(UserWarning, match="actions likely not synced"):
        bars.get_bars("NOSYNC", "1d", adjust="split_div")
    # with the action cached, no warning
    corporate_actions.store_actions([{
        "symbol": "NOSYNC", "ex_date": date(2024, 1, 3), "action_type": "split",
        "ratio": 4.0, "cash_amount": None}], db_path=config.ACTIONS_DB)
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("error")
        bars.get_bars("NOSYNC", "1d", adjust="split_div")


def test_session_filter(tmp_store):
    # include a pre-market (09:00) and after-hours (16:30) bar
    hhmm = ["09:00", "09:30", "10:00", "16:30"]
    store.write_frame("PRE", "1min", _et_bars("2026-07-24", hhmm, [10, 11, 12, 13]),
                      provider=_SIP)
    reg = bars.get_bars("PRE", "1min", start="2026-07-24", end="2026-07-24",
                        adjust="none", session="regular")
    allh = bars.get_bars("PRE", "1min", start="2026-07-24", end="2026-07-24",
                         adjust="none", session="all")
    reg_et = reg["ts"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist()
    assert reg_et == ["09:30", "10:00"]          # pre/post dropped
    assert len(allh) == 4                        # all kept
