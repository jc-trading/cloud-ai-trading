"""Phase 8 EOD SIP correction — provider flip, fail-closed abort, empty pages,
completeness, the IEX-vs-SIP comparison and the 1hour sync, all against a fake
provider + fake registry (no network, no PostgreSQL)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from quant import config
from quant.data import eod_correction, store
from quant.data.registry import StreamSymbol, SystemAccount
from quant.tests.conftest import FakeRegistry

ET = "America/New_York"
DAY = "2026-09-04"          # Friday, a full XNYS session
HOLIDAY = "2026-09-07"      # Labor Day
EARLY_CLOSE = "2025-11-28"  # day after Thanksgiving, 13:00 ET close
NOW = pd.Timestamp("2026-09-05 01:30", tz="UTC")


class EodRegistry(FakeRegistry):
    def __init__(self, *, positions=(), configured=(), watchlist=(), account=True):
        super().__init__()
        self.positions = list(positions)
        self.configured = list(configured)
        self.watchlist = list(watchlist)
        self.account = SystemAccount(account_id="acct", user_id="user") if account else None

    def system_account(self):
        return self.account

    def open_position_symbols(self, account_id):
        return list(self.positions)

    def watchlist_symbols(self, user_id):
        return list(self.watchlist)

    def stream_symbols(self):
        return [StreamSymbol(symbol=s, priority=i, note=None)
                for i, s in enumerate(self.configured)]


class FakeProvider:
    provider_key = "alpaca:sip"

    def __init__(self, frames: dict, errors=(), hours: dict | None = None,
                 hour_errors=()):
        self.frames = frames
        self.errors = set(errors)
        self.hours = hours or {}
        self.hour_errors = set(hour_errors)
        self.calls: list[tuple[str, str]] = []

    def fetch_bars(self, symbol, timeframe, start, end=None, *, now=None, client=None):
        self.calls.append((symbol, timeframe))
        if symbol in self.errors:
            raise RuntimeError("alpaca 500")
        if timeframe == "1hour" and symbol in self.hour_errors:
            raise RuntimeError("alpaca 500 on 1hour")
        source = self.hours if timeframe == "1hour" else self.frames
        return source.get(symbol, _frame([]))


def _minutes(day: str, count: int, start: str = "04:00", close: float = 100.0,
             volume: float = 1000.0) -> pd.DataFrame:
    first = pd.Timestamp(f"{day} {start}", tz=ET)
    ts = [first + pd.Timedelta(minutes=i) for i in range(count)]
    return _frame(ts, close=close, volume=volume)


def _session(day: str, *, rth: int = 390, pre: int = 0) -> pd.DataFrame:
    """A day with `rth` of the regular minutes plus `pre` pre-market ones
    (from 04:00 ET; keep pre <= 330 so the two blocks stay disjoint)."""
    ts = [pd.Timestamp(f"{day} 09:30", tz=ET) + pd.Timedelta(minutes=i)
          for i in range(rth)]
    ts += [pd.Timestamp(f"{day} 04:00", tz=ET) + pd.Timedelta(minutes=i)
           for i in range(pre)]
    return _frame(ts)


def _hours(day: str, count: int = 16) -> pd.DataFrame:
    return _frame([pd.Timestamp(f"{day} 04:00", tz=ET) + pd.Timedelta(hours=i)
                   for i in range(count)])


def _frame(ts, close: float = 100.0, volume: float = 1000.0) -> pd.DataFrame:
    ts = [pd.Timestamp(t).tz_convert("UTC") for t in ts]
    return pd.DataFrame({
        "ts": pd.to_datetime(ts, utc=True),
        "open": close, "high": close + 1, "low": close - 1, "close": close,
        "volume": volume, "vwap": close, "trade_count": 5.0,
    })


@pytest.fixture
def bars_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BARS_ROOT", tmp_path / "stock-market-data")
    return config.BARS_ROOT


def _seed_iex(reg, symbol: str, df: pd.DataFrame, day: str = DAY) -> None:
    store.write_bars(symbol, "1min", day, df, provider="alpaca:iex", registry=reg)


class TestSessionWindow:
    def test_full_day_is_960_minutes(self):
        assert eod_correction.expected_minutes(date(2026, 9, 4)) == 960

    def test_early_close_stops_at_17_et(self):
        day = pd.Timestamp(EARLY_CLOSE).date()
        assert eod_correction.expected_minutes(day) == 780
        _, close_ts = eod_correction.session_window(day)
        assert close_ts.tz_convert(ET).hour == 17

    def test_last_completed_session_skips_the_holiday(self):
        # Tuesday 01:30 UTC after Labor Day: the newest closed session is Friday
        got = eod_correction.last_completed_session(pd.Timestamp("2026-09-08 01:30", tz="UTC"))
        assert got == date(2026, 9, 4)


class TestSkips:
    def test_non_trading_day_is_skipped(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        report = eod_correction.run(HOLIDAY, now=NOW, registry=reg,
                                    provider=FakeProvider({}))
        assert report.skipped and "not a session" in report.skipped
        assert report.results == []

    def test_session_still_open_is_skipped(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        report = eod_correction.run(DAY, now=pd.Timestamp(f"{DAY} 18:00", tz=ET),
                                    registry=reg, provider=FakeProvider({}))
        assert report.skipped and "not closed" in report.skipped


class TestCorrection:
    def test_provider_flips_iex_to_sip(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        _seed_iex(reg, "AAPL", _minutes(DAY, 400, close=99.0))
        provider = FakeProvider({"AAPL": _minutes(DAY, 960, close=100.0)})

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        row = reg.get_row("AAPL", "1min", DAY)
        assert row.provider == "alpaca:sip"
        assert row.row_count == 960
        assert row.status == "ok"
        assert store.parquet_provider(store.bar_path("AAPL", "1min", DAY)) == "alpaca:sip"
        assert report.corrected == ["AAPL"]
        assert row.meta["completeness"] == 1.0
        assert row.meta["expected_minutes"] == 960

    def test_symbol_set_unions_stream_resolution_and_stored_days(self, bars_root):
        reg = EodRegistry(positions=["NVDA"], configured=["SPY"], watchlist=["QQQ"])
        _seed_iex(reg, "TSLA", _minutes(DAY, 10))
        provider = FakeProvider({s: _minutes(DAY, 960) for s in
                                 ("NVDA", "SPY", "QQQ", "TSLA")})

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert report.symbols == ["NVDA", "SPY", "QQQ", "TSLA"]
        assert sorted(report.corrected) == ["NVDA", "QQQ", "SPY", "TSLA"]

    def test_empty_sip_page_keeps_the_iex_file_and_marks_stale(self, bars_root):
        reg = EodRegistry(configured=["AAPL", "MSFT", "SPY", "QQQ", "NVDA"])
        _seed_iex(reg, "AAPL", _minutes(DAY, 300, close=99.0))
        provider = FakeProvider({s: _minutes(DAY, 960) for s in
                                 ("MSFT", "SPY", "QQQ", "NVDA")})

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        row = reg.get_row("AAPL", "1min", DAY)
        assert row.provider == "alpaca:iex"
        assert row.row_count == 300
        assert row.status == "stale"
        assert report.stale == ["AAPL"]
        assert len(report.corrected) == 4
        stored = store.read_bars("AAPL", "1min")
        assert len(stored) == 300 and float(stored["close"].iloc[0]) == 99.0

    def test_fail_closed_above_20_percent_overwrites_nothing(self, bars_root):
        symbols = ["AAPL", "MSFT", "SPY", "QQQ", "NVDA"]
        reg = EodRegistry(configured=symbols)
        for s in symbols:
            _seed_iex(reg, s, _minutes(DAY, 300, close=99.0))
        provider = FakeProvider(
            {s: _minutes(DAY, 960) for s in ("SPY", "QQQ", "NVDA")},
            errors=["MSFT"])   # AAPL returns empty, MSFT raises -> 2/5 = 40%

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert report.fail_closed and "2/5" in report.fail_closed
        assert sorted(report.stale) == sorted(symbols)
        for s in symbols:
            row = reg.get_row(s, "1min", DAY)
            assert row.provider == "alpaca:iex"
            assert row.row_count == 300
            assert row.status == "stale"
        assert ("SPY", "1hour") not in provider.calls

    def test_sparse_extended_hours_is_not_partial(self, bars_root):
        # the real 2026-09-04 shape: every RTH minute present, thin pre/post
        reg = EodRegistry(configured=["SPY"])
        provider = FakeProvider({"SPY": _session(DAY, rth=390, pre=60)})

        eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        row = reg.get_row("SPY", "1min", DAY)
        assert row.status == "ok"
        assert row.meta["rth_completeness"] == 1.0
        assert row.meta["completeness"] == pytest.approx(450 / 960, abs=1e-4)
        assert row.meta["rth_expected_minutes"] == 390

    def test_missing_rth_minutes_is_partial(self, bars_root):
        reg = EodRegistry(configured=["SPY"])
        provider = FakeProvider({"SPY": _session(DAY, rth=380, pre=330)})

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        row = reg.get_row("SPY", "1min", DAY)
        assert report.partial == ["SPY"]
        assert row.status == "partial"
        assert row.meta["rth_completeness"] == pytest.approx(380 / 390, abs=1e-4)
        assert row.meta["completeness"] == pytest.approx(710 / 960, abs=1e-4)

    def test_partial_only_for_liquid_symbols(self, bars_root):
        reg = EodRegistry(positions=["NVDA"], configured=["SPY"], watchlist=["ZZZ"])
        provider = FakeProvider({
            "NVDA": _session(DAY, rth=200),   # half the regular session missing
            "SPY": _session(DAY, rth=390),
            "ZZZ": _session(DAY, rth=200),    # watchlist-only: thin by choice
        })

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert report.partial == ["NVDA"]
        assert reg.get_row("ZZZ", "1min", DAY).status == "ok"
        assert reg.get_row("SPY", "1min", DAY).status == "ok"

    def test_early_close_shrinks_the_rth_denominator(self, bars_root):
        day = EARLY_CLOSE
        reg = EodRegistry(configured=["SPY"])
        provider = FakeProvider({"SPY": _session(day, rth=210)})

        eod_correction.run(day, now=pd.Timestamp(f"{day} 23:00", tz=ET),
                           registry=reg, provider=provider)

        row = reg.get_row("SPY", "1min", day)
        assert row.meta["rth_expected_minutes"] == 210
        assert row.meta["rth_completeness"] == 1.0
        assert row.status == "ok"

    def test_one_hour_bars_are_merged_not_replaced(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        earlier = _frame([pd.Timestamp("2026-09-03 10:00", tz=ET),
                          pd.Timestamp("2026-09-03 11:00", tz=ET)])
        store.write_bars("AAPL", "1hour", "2026-09", earlier, provider="alpaca:sip",
                         registry=reg)
        hours = _frame([pd.Timestamp(f"{DAY} {h}:00", tz=ET) for h in (10, 11, 12)])
        provider = FakeProvider({"AAPL": _minutes(DAY, 960)}, hours={"AAPL": hours})

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert reg.get_row("AAPL", "1hour", "2026-09").row_count == 5
        assert report.results[0].hour_rows == 5
        assert ("AAPL", "1hour") in provider.calls


class TestRerun:
    def test_second_run_is_a_no_op_and_keeps_the_comparison(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        _seed_iex(reg, "AAPL", _minutes(DAY, 400, close=99.0))
        provider = FakeProvider({"AAPL": _minutes(DAY, 960)},
                                hours={"AAPL": _hours(DAY)})

        first = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)
        stats = reg.get_row("AAPL", "1min", DAY).meta["iex_vs_sip"]
        assert stats["minutes_iex"] == 400
        calls_after_first = len(provider.calls)

        second = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert second.skipped == "already corrected"
        assert second.already_corrected == ["AAPL"]
        assert len(provider.calls) == calls_after_first          # zero REST calls
        assert reg.get_row("AAPL", "1min", DAY).meta["iex_vs_sip"] == stats
        assert first.corrected == ["AAPL"]

    def test_a_partial_row_is_retried(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        eod_correction.run(DAY, now=NOW, registry=reg,
                           provider=FakeProvider({"AAPL": _session(DAY, rth=300)}))
        assert reg.get_row("AAPL", "1min", DAY).status == "partial"

        provider = FakeProvider({"AAPL": _session(DAY, rth=390)})
        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert report.corrected == ["AAPL"]
        assert ("AAPL", "1min") in provider.calls

    def test_an_uncorrected_symbol_still_runs_beside_a_corrected_one(self, bars_root):
        reg = EodRegistry(configured=["AAPL", "MSFT"])
        provider = FakeProvider({s: _minutes(DAY, 960) for s in ("AAPL", "MSFT")},
                                hours={s: _hours(DAY) for s in ("AAPL", "MSFT")})
        eod_correction.run(DAY, now=NOW, registry=reg, provider=provider,
                           symbols=["AAPL"])

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert report.already_corrected == ["AAPL"]
        assert report.corrected == ["MSFT"]


class TestHourSync:
    def test_missing_hour_file_heals_without_refetching_1min(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        first = FakeProvider({"AAPL": _minutes(DAY, 960)})   # no 1hour data
        eod_correction.run(DAY, now=NOW, registry=reg, provider=first)
        assert reg.get_row("AAPL", "1hour", "2026-09") is None

        second = FakeProvider({"AAPL": _minutes(DAY, 960)}, hours={"AAPL": _hours(DAY)})
        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=second)

        assert report.hour_healed == ["AAPL"]
        assert report.already_corrected == []
        assert second.calls == [("AAPL", "1hour")]           # 1min never refetched
        assert reg.get_row("AAPL", "1hour", "2026-09").row_count == 16
        assert reg.get_row("AAPL", "1min", DAY).status == "ok"

        third = FakeProvider({"AAPL": _minutes(DAY, 960)}, hours={"AAPL": _hours(DAY)})
        done = eod_correction.run(DAY, now=NOW, registry=reg, provider=third)
        assert done.skipped == "already corrected"
        assert third.calls == []

    def test_failed_hour_write_marks_the_1min_row_partial(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        provider = FakeProvider({"AAPL": _minutes(DAY, 960)}, hour_errors=["AAPL"])

        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        row = reg.get_row("AAPL", "1min", DAY)
        assert row.status == "partial"
        assert "alpaca 500 on 1hour" in row.meta["hour_sync_error"]
        assert report.partial == ["AAPL"]

    def test_a_hour_only_retry_that_fails_again_stays_partial(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        eod_correction.run(DAY, now=NOW, registry=reg,
                           provider=FakeProvider({"AAPL": _minutes(DAY, 960)}))

        provider = FakeProvider({"AAPL": _minutes(DAY, 960)}, hour_errors=["AAPL"])
        report = eod_correction.run(DAY, now=NOW, registry=reg, provider=provider)

        assert provider.calls == [("AAPL", "1hour")]
        assert report.partial == ["AAPL"]
        assert report.hour_healed == []
        assert "hour_sync_error" in reg.get_row("AAPL", "1min", DAY).meta


class TestFeedComparison:
    def test_numbers_on_a_synthetic_day(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        iex = _minutes(DAY, 100, close=100.0, volume=30.0)
        sip = _minutes(DAY, 120, close=100.1, volume=1000.0)
        _seed_iex(reg, "AAPL", iex)

        eod_correction.run(DAY, now=NOW, registry=reg,
                           provider=FakeProvider({"AAPL": sip}))

        stats = reg.get_row("AAPL", "1min", DAY).meta["iex_vs_sip"]
        assert stats["minutes_iex"] == 100
        assert stats["minutes_sip"] == 120
        assert stats["minutes_common"] == 100
        assert stats["iex_missing_minutes"] == 20
        assert stats["close_bps_median"] == pytest.approx(9.99, abs=0.05)
        assert stats["close_bps_max"] == pytest.approx(9.99, abs=0.05)
        assert stats["volume_ratio"] == pytest.approx(100 * 30 / (120 * 1000), abs=1e-6)

    def test_no_stored_file_records_no_comparison(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        eod_correction.run(DAY, now=NOW, registry=reg,
                           provider=FakeProvider({"AAPL": _minutes(DAY, 960)}))
        assert "iex_vs_sip" not in reg.get_row("AAPL", "1min", DAY).meta

    def test_bars_outside_the_session_window_are_dropped(self, bars_root):
        reg = EodRegistry(configured=["AAPL"])
        overflow = _frame([pd.Timestamp(f"{DAY} 03:59", tz=ET),
                           pd.Timestamp(f"{DAY} 04:00", tz=ET),
                           pd.Timestamp(f"{DAY} 20:00", tz=ET)])
        eod_correction.run(DAY, now=NOW, registry=reg,
                           provider=FakeProvider({"AAPL": overflow}))
        stored = store.read_bars("AAPL", "1min")
        assert len(stored) == 1
        assert pd.Timestamp(stored["ts"].iloc[0]).tz_convert(ET).hour == 4


def test_compare_feeds_handles_a_disjoint_day():
    iex = _frame([pd.Timestamp(f"{DAY} 09:30", tz=ET)], volume=5.0)
    sip = _frame([pd.Timestamp(f"{DAY} 15:00", tz=ET)], volume=50.0)
    stats = eod_correction.compare_feeds(iex, sip)
    assert stats["minutes_common"] == 0
    assert stats["close_bps_median"] is None
    assert stats["iex_missing_minutes"] == 1
    assert stats["volume_ratio"] == pytest.approx(0.1)


def test_registry_status_update_is_isolated_to_the_row():
    reg = EodRegistry()
    reg.upsert("AAPL", "1min", DAY, path="p", provider="alpaca:iex", row_count=1,
               first_ts=datetime(2026, 9, 4), last_ts=datetime(2026, 9, 4),
               checksum="x", fetched_at=datetime(2026, 9, 4))
    assert reg.update_status("AAPL", "1min", DAY, status="partial", meta={"a": 1}) == 1
    assert reg.update_status("MSFT", "1min", DAY, status="stale") == 0
    row = reg.get_row("AAPL", "1min", DAY)
    assert (row.status, row.meta, row.provider) == ("partial", {"a": 1}, "alpaca:iex")
