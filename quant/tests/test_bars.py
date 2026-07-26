"""R0-5 get_bars() tests: single entry point, read-time adjustment, session
filter, and 5m->1h resample consistency. Synthetic parquet, no network."""

from datetime import date

import pandas as pd
import pytest

from quant import config
from quant.data import bars, corporate_actions, store


@pytest.fixture
def synth(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BARS_DIR", tmp_path / "bars")
    monkeypatch.setattr(config, "MANIFEST_DB", tmp_path / "manifest.db")
    return tmp_path


def _et5m(day, hhmm_list, closes):
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


def test_only_imports_get_bars(synth):
    # daily: write RAW, a 2:1 split, then read adjusted vs raw via get_bars only
    df = pd.DataFrame({
        "ts": pd.DatetimeIndex([pd.Timestamp(f"2024-01-0{d} 00:00", tz="America/New_York")
                                for d in (2, 3, 4, 5)]).tz_convert("UTC"),
        "open": [200.0, 202.0, 100.0, 101.0], "high": [201, 203, 101, 102],
        "low": [199, 201, 99, 100], "close": [200.0, 202.0, 100.0, 101.0],
        "volume": [1_000, 1_000, 2_000, 2_000], "vwap": [200, 202, 100, 101],
        "trade_count": [10, 10, 10, 10],
    })
    store.write_daily("ZZZ", df)
    corporate_actions.store_actions([{
        "symbol": "ZZZ", "ex_date": date(2024, 1, 4), "action_type": "split",
        "ratio": 2.0, "cash_amount": None}], db_path=config.MANIFEST_DB)

    raw = bars.get_bars("ZZZ", "1d", adjust="none")
    adj = bars.get_bars("ZZZ", "1d", adjust="split_div")
    assert raw["close"].tolist() == [200.0, 202.0, 100.0, 101.0]
    # pre-split halved -> continuous series, no 2x jump
    assert adj["close"].tolist() == [100.0, 101.0, 100.0, 101.0]


def test_resample_5m_to_1h_consistency(synth):
    hhmm = [f"{9 if m < 30 else 10}:{(30 + m) % 60:02d}" if m < 30
            else f"10:{(m - 30):02d}" for m in range(0, 60, 5)]
    # build 09:30..10:25 (12 five-minute bars)
    hhmm = ["09:30", "09:35", "09:40", "09:45", "09:50", "09:55",
            "10:00", "10:05", "10:10", "10:15", "10:20", "10:25"]
    closes = [10, 12, 9, 15, 11, 8, 14, 13, 7, 16, 10, 12]
    store.write_intraday("QQQ", _et5m("2026-07-24", hhmm, closes))

    hour = bars.get_bars("QQQ", "1h", start="2026-07-24", end="2026-07-25",
                         adjust="none", session="regular")
    assert len(hour) == 1
    b = hour.iloc[0]
    assert b["open"] == 10                 # first
    assert b["close"] == 12                # last
    assert b["high"] == max(closes) + 2    # 16+2
    assert b["low"] == min(closes) - 2     # 7-2
    assert b["volume"] == sum(100 * (i + 1) for i in range(12))


def test_session_filter(synth):
    # include a pre-market (09:00) and after-hours (16:30) bar
    hhmm = ["09:00", "09:30", "10:00", "16:30"]
    store.write_intraday("PRE", _et5m("2026-07-24", hhmm, [10, 11, 12, 13]))
    reg = bars.get_bars("PRE", "5m", start="2026-07-24", end="2026-07-25",
                        adjust="none", session="regular")
    allh = bars.get_bars("PRE", "5m", start="2026-07-24", end="2026-07-25",
                         adjust="none", session="all")
    reg_et = reg["ts"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist()
    assert reg_et == ["09:30", "10:00"]          # pre/post dropped
    assert len(allh) == 4                        # all kept
