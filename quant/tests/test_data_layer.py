"""R0-2 deterministic unit tests (no network): closed-bar filtering, Parquet
store merge/dedupe idempotency, and manifest roundtrip."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from quant import config
from quant.data import fetch, manifest, store


def _bars(ts_list, base=100.0):
    return pd.DataFrame({
        "ts": pd.to_datetime(ts_list, utc=True),
        "open": [base + i for i in range(len(ts_list))],
        "high": [base + i + 1 for i in range(len(ts_list))],
        "low": [base + i - 1 for i in range(len(ts_list))],
        "close": [base + i + 0.5 for i in range(len(ts_list))],
        "volume": [1000 * (i + 1) for i in range(len(ts_list))],
        "vwap": [base + i for i in range(len(ts_list))],
        "trade_count": [10 * (i + 1) for i in range(len(ts_list))],
    })


# --- closed-bar filtering -------------------------------------------------

def test_drop_unclosed_intraday():
    df = _bars(["2026-07-24T14:00:00Z", "2026-07-24T14:05:00Z", "2026-07-24T14:10:00Z"])
    # now = 14:12 -> [14:10,14:15) not closed; [14:05,14:10) closed at 14:10
    now = datetime(2026, 7, 24, 14, 12, tzinfo=timezone.utc)
    out = fetch.drop_unclosed_intraday(df, now, tf_minutes=5)
    assert list(out["ts"].dt.strftime("%H:%M")) == ["14:00", "14:05"]


def test_drop_unclosed_daily():
    # daily bars anchored at ET midnight -> 04:00Z during EDT
    df = _bars(["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"])
    # now = 07-24 15:00 ET (19:00Z) -> before 16:20 ET close -> today's bar unclosed
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    out = fetch.drop_unclosed_daily(df, now)
    assert list(out["ts"].dt.strftime("%Y-%m-%d")) == ["2026-07-23"]
    # after close (20:30Z = 16:30 ET) today's bar is kept
    now2 = datetime(2026, 7, 24, 20, 30, tzinfo=timezone.utc)
    out2 = fetch.drop_unclosed_daily(df, now2)
    assert list(out2["ts"].dt.strftime("%Y-%m-%d")) == ["2026-07-23", "2026-07-24"]


# --- store merge / dedupe / idempotency -----------------------------------

@pytest.fixture
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BARS_DIR", tmp_path / "bars")
    return tmp_path


def test_store_daily_merge_dedupe(tmp_store):
    first = _bars(["2026-07-20T04:00:00Z", "2026-07-21T04:00:00Z", "2026-07-22T04:00:00Z"])
    assert store.write_daily("AAPL", first) == 3
    assert store.daily_path("AAPL").exists()
    # overlapping re-write (21,22,23) -> only 23 is new; total 4, no dups, sorted
    second = _bars(["2026-07-21T04:00:00Z", "2026-07-22T04:00:00Z", "2026-07-23T04:00:00Z"], base=200.0)
    assert store.write_daily("AAPL", second) == 4
    out = store.read_daily("AAPL")
    assert len(out) == 4
    assert out["ts"].is_monotonic_increasing
    assert out["ts"].duplicated().sum() == 0
    # keep="last": overlapping ts take the second write's values
    row22 = out[out["ts"] == pd.Timestamp("2026-07-22T04:00:00Z")].iloc[0]
    assert row22["open"] == 201.0  # base 200 + index 1 in the second batch


def test_store_intraday_splits_by_month(tmp_store):
    df = _bars(["2026-06-30T14:00:00Z", "2026-07-01T14:00:00Z", "2026-07-01T14:05:00Z"])
    store.write_intraday("AAPL", df)
    assert store.intraday_path("AAPL", 2026, 6).exists()
    assert store.intraday_path("AAPL", 2026, 7).exists()
    assert len(store.read_intraday("AAPL", 2026, 7)) == 2


# --- manifest roundtrip ---------------------------------------------------

def test_manifest_roundtrip(tmp_path):
    db = tmp_path / "manifest.db"
    assert manifest.get_last_ts("AAPL", "1d", db_path=db) is None
    t1 = datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
    manifest.upsert("AAPL", "1d", first_ts=t1, last_ts=t2, row_count=3,
                    fetched_at=t2, session="regular", source="alpaca", db_path=db)
    assert manifest.get_last_ts("AAPL", "1d", db_path=db) == t2
    row = manifest.get_row("AAPL", "1d", db_path=db)
    assert row.row_count == 3 and row.source == "alpaca"
    # update (later high-water) overwrites in place, no second row
    t3 = datetime(2026, 7, 23, 4, 0, tzinfo=timezone.utc)
    manifest.upsert("AAPL", "1d", first_ts=t1, last_ts=t3, row_count=4,
                    fetched_at=t3, session="regular", source="alpaca", db_path=db)
    assert manifest.get_last_ts("AAPL", "1d", db_path=db) == t3
    assert manifest.get_row("AAPL", "1d", db_path=db).row_count == 4
