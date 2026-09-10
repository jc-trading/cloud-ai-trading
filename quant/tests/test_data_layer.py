"""R0-2 deterministic unit tests (no network): closed-bar filtering, Parquet
store merge/dedupe idempotency, and the batched daily sync (R1 review fix #1)
with fake Alpaca clients. The file registry is the in-memory fake (conftest)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from quant import config
from quant.data import fetch, store

_SIP = config.PROVIDER_HISTORICAL
_IEX = config.PROVIDER_REALTIME


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

def test_store_daily_merge_dedupe(tmp_store):
    first = _bars(["2026-07-20T04:00:00Z", "2026-07-21T04:00:00Z", "2026-07-22T04:00:00Z"])
    assert store.write_frame("AAPL", "daily", first, provider=_SIP) == 3
    assert store.bar_path("AAPL", "daily", "2026").exists()
    # overlapping re-write (21,22,23) -> only 23 is new; total 4, no dups, sorted
    second = _bars(["2026-07-21T04:00:00Z", "2026-07-22T04:00:00Z", "2026-07-23T04:00:00Z"], base=200.0)
    assert store.write_frame("AAPL", "daily", second, provider=_SIP) == 4
    out = store.read_bars("AAPL", "daily")
    assert len(out) == 4
    assert out["ts"].is_monotonic_increasing
    assert out["ts"].duplicated().sum() == 0
    # keep="last": overlapping ts take the second write's values
    row22 = out[out["ts"] == pd.Timestamp("2026-07-22T04:00:00Z")].iloc[0]
    assert row22["open"] == 201.0  # base 200 + index 1 in the second batch


def test_store_intraday_splits_by_period(tmp_store):
    df = _bars(["2026-06-30T14:00:00Z", "2026-07-01T14:00:00Z", "2026-07-01T14:05:00Z"])
    store.write_frame("AAPL", "1min", df, provider=_IEX)
    assert store.bar_path("AAPL", "1min", "2026-06-30").exists()
    assert store.bar_path("AAPL", "1min", "2026-07-01").exists()
    assert len(store.read_bars("AAPL", "1min", "2026-07-01", "2026-07-01")) == 2

    store.write_frame("AAPL", "1hour", df, provider=_SIP)
    assert store.bar_path("AAPL", "1hour", "2026-06").exists()
    assert store.bar_path("AAPL", "1hour", "2026-07").exists()


# --- batched daily sync (review #1) ----------------------------------------

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)   # Monday, both bars closed


def _alpaca_df(sym_bars: dict[str, list[str]]) -> pd.DataFrame:
    """Build a frame shaped like alpaca-py's resp.df (MultiIndex symbol/timestamp)."""
    frames = []
    for sym, ts_list in sym_bars.items():
        df = _bars(ts_list)
        df.index = pd.MultiIndex.from_arrays(
            [[sym] * len(df), df["ts"]], names=["symbol", "timestamp"])
        frames.append(df.drop(columns=["ts"]))
    return pd.concat(frames)


class _Resp:
    def __init__(self, df):
        self.df = df


def test_sync_daily_counts_new_rows_against_registry(tmp_store):
    """#1: no more double parquet read — new-row count comes from the prior
    registry last_ts, overlap rows are not counted."""
    class _C:
        def __init__(self, ts_list):
            self._ts = ts_list

        def get_stock_bars(self, req):
            return _Resp(_alpaca_df({"AAPL": self._ts}))

    first = _C(["2026-07-20T04:00:00Z", "2026-07-21T04:00:00Z", "2026-07-22T04:00:00Z"])
    assert fetch.sync_daily("AAPL", client=first, now=NOW) == 3
    # second sync overlaps on 07-22 -> only 23/24 count as NEW
    second = _C(["2026-07-22T04:00:00Z", "2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"])
    assert fetch.sync_daily("AAPL", client=second, now=NOW) == 2
    assert len(store.read_bars("AAPL", "daily")) == 5
    assert tmp_store.max_last_ts("AAPL", "daily") == datetime(2026, 7, 24, 4,
                                                              tzinfo=timezone.utc)


def test_sync_daily_many_one_request_per_chunk(tmp_store):
    ts = ["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"]

    class _C:
        def __init__(self):
            self.calls = []

        def get_stock_bars(self, req):
            self.calls.append(req)
            return _Resp(_alpaca_df({"AAA": ts, "BBB": ts}))

    client = _C()
    synced, failed = fetch.sync_daily_many(["AAA", "BBB"], client=client, now=NOW)
    assert (synced, failed) == (2, [])
    assert len(client.calls) == 1                       # ONE request, not two
    assert sorted(client.calls[0].symbol_or_symbols) == ["AAA", "BBB"]
    assert len(store.read_bars("AAA", "daily")) == 2
    assert len(store.read_bars("BBB", "daily")) == 2
    assert tmp_store.max_last_ts("AAA", "daily") == datetime(2026, 7, 24, 4,
                                                             tzinfo=timezone.utc)
    # new symbol (no registry row) -> the chunk fell back to full-history start
    # (alpaca-py normalizes request datetimes to tz-naive UTC)
    expected_start = NOW - timedelta(days=365 * config.DAILY_HISTORY_YEARS + 7)
    assert pd.Timestamp(client.calls[0].start) == \
        pd.Timestamp(expected_start).tz_localize(None)

    # second run: incremental — shared start = MIN of the chunk's last_ts
    tmp_store.upsert("BBB", "daily", "2026", path="BBB/daily/2026.parquet",
                     provider=_SIP, row_count=1,
                     first_ts=datetime(2026, 7, 20, 4, tzinfo=timezone.utc),
                     last_ts=datetime(2026, 7, 20, 4, tzinfo=timezone.utc),
                     checksum="x", fetched_at=NOW)
    client.calls.clear()
    synced, failed = fetch.sync_daily_many(["AAA", "BBB"], client=client, now=NOW)
    assert (synced, failed) == (2, [])
    assert pd.Timestamp(client.calls[0].start) == pd.Timestamp("2026-07-20 04:00")
    # over-fetch overlap is deduped by the store
    assert len(store.read_bars("AAA", "daily")) == 2


def test_sync_daily_many_failed_chunk_falls_back_per_symbol(tmp_store):
    ts = ["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"]

    class _C:
        def get_stock_bars(self, req):
            syms = req.symbol_or_symbols
            if isinstance(syms, list):                  # batch endpoint "down"
                raise RuntimeError("batch failed")
            if syms == "BAD":
                raise RuntimeError("bad symbol")
            return _Resp(_alpaca_df({syms: ts}))

    synced, failed = fetch.sync_daily_many(["BAD", "GOOD"], client=_C(), now=NOW)
    assert synced == 1
    assert failed == ["BAD"]                            # only the bad one lost
    assert len(store.read_bars("GOOD", "daily")) == 2   # chunk-mate survived
    assert not store.bar_dir("BAD", "daily").exists()


def test_sync_daily_many_missing_frame_counts_as_failed(tmp_store):
    """Phase A #2: Alpaca returning no frame for a symbol is a MISS. Counting it
    ok hid the symbol from signal_cycle's 20% fail-closed ratio."""
    ts = ["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"]

    class _C:
        def get_stock_bars(self, req):
            return _Resp(_alpaca_df({"GOOD": ts}))      # MISSING absent entirely

    synced, failed = fetch.sync_daily_many(["GOOD", "MISSING"], client=_C(), now=NOW)
    assert synced == 1
    assert failed == ["MISSING"]
    assert not store.bar_dir("MISSING", "daily").exists()
