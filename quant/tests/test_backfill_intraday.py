"""Phase 6 intraday backfill — range-request batching, ET period splitting across
month and DST boundaries, resume from the registry high-water, idempotency and
per-symbol failure isolation, all against a fake provider + fake registry
(no network, no PostgreSQL)."""

from __future__ import annotations

import pandas as pd
import pytest

from quant import config
from quant.data import backfill, calendar, store
from quant.data.registry import StreamSymbol, SystemAccount
from quant.tests.conftest import FakeRegistry

ET = "America/New_York"
NOW = pd.Timestamp("2026-09-05 12:00", tz="UTC")


class BackfillRegistry(FakeRegistry):
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

    def __init__(self, frames: dict | None = None, errors=(), transient=False):
        self.frames = frames or {}
        self.errors = set(errors)
        self.transient = transient
        self.calls: list[tuple[str, str, pd.Timestamp, pd.Timestamp]] = []

    def fetch_bars(self, symbol, timeframe, start, end=None, *, now=None, client=None):
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        self.calls.append((symbol, timeframe, lo, hi))
        if symbol in self.errors:
            if self.transient:
                self.errors.discard(symbol)
            raise RuntimeError("alpaca 429")
        df = self.frames.get(symbol)
        if df is None:
            return _frame([])
        return df[(df["ts"] >= lo) & (df["ts"] < hi)].reset_index(drop=True)

    def starts(self, symbol: str) -> list[pd.Timestamp]:
        return [c[2] for c in self.calls if c[0] == symbol]


def _frame(ts, close: float = 100.0, volume: float = 1000.0) -> pd.DataFrame:
    if len(ts) == 0:
        return pd.DataFrame(columns=list(config.BAR_COLUMNS))
    ts = [pd.Timestamp(t).tz_convert("UTC") for t in ts]
    return pd.DataFrame({
        "ts": pd.to_datetime(ts, utc=True),
        "open": close, "high": close + 1, "low": close - 1, "close": close,
        "volume": volume, "vwap": close, "trade_count": 5.0,
    })


def _minutes(day: str, count: int, start: str = "09:30") -> pd.DataFrame:
    first = pd.Timestamp(f"{day} {start}", tz=ET)
    return _frame([first + pd.Timedelta(minutes=i) for i in range(count)])


def _hours(day: str, count: int = 16, start: str = "04:00") -> pd.DataFrame:
    first = pd.Timestamp(f"{day} {start}", tz=ET)
    return _frame([first + pd.Timedelta(hours=i) for i in range(count)])


def _keys(reg: FakeRegistry, symbol: str, timeframe: str) -> list[str]:
    return sorted(k[2] for k in reg.rows if k[0] == symbol and k[1] == timeframe)


def _run(symbols, timeframe, reg, provider, *, years=1, now=NOW):
    return backfill.run_intraday_backfill(symbols, timeframe, years, provider=provider,
                                          registry=reg, now=now, progress=lambda *_: None)


class TestSymbolResolution:
    def test_positions_then_configured_then_watchlist(self):
        reg = BackfillRegistry(positions=["AAPL"], configured=["SPY", "AAPL"],
                               watchlist=["qqq", "SPY"])
        assert backfill.resolve_intraday_symbols(reg) == ["AAPL", "SPY", "QQQ"]

    def test_no_system_account_falls_back_to_configured(self):
        reg = BackfillRegistry(configured=["SPY"], account=False)
        assert backfill.resolve_intraday_symbols(reg) == ["SPY"]


class TestRangeRequests:
    def test_one_request_per_window_not_per_day(self, tmp_store):
        provider = FakeProvider()
        stats = _run(["SPY"], "1min", tmp_store, provider, years=5)

        sessions = len(calendar.sessions_in_range("2021-09-05", "2026-09-05"))
        assert stats.requests == 5                       # 5 year-long windows
        assert len(provider.calls) == 5
        assert stats.requests < sessions / 100           # O(pages), not O(days)

    def test_windows_tile_the_history_and_stop_at_the_sip_delay(self, tmp_store):
        provider = FakeProvider()
        _run(["SPY"], "1min", tmp_store, provider, years=2)

        starts = [c[2] for c in provider.calls]
        ends = [c[3] for c in provider.calls]
        assert starts[0] == NOW - pd.Timedelta(days=730)
        assert ends[:-1] == starts[1:]
        assert ends[-1] == NOW - pd.Timedelta(minutes=config.SIP_DELAY_MINUTES)

    def test_unsupported_timeframe_rejected(self, tmp_store):
        with pytest.raises(ValueError):
            _run(["SPY"], "daily", tmp_store, FakeProvider())


class TestPeriodSplit:
    def test_month_boundary_splits_1hour_files(self, tmp_store):
        frame = pd.concat([_hours("2026-08-31"), _hours("2026-09-01")],
                          ignore_index=True)
        provider = FakeProvider({"SPY": frame})
        stats = _run(["SPY"], "1hour", tmp_store, provider)

        assert _keys(tmp_store, "SPY", "1hour") == ["2026-08", "2026-09"]
        assert stats.rows == 32
        assert tmp_store.rows[("SPY", "1hour", "2026-08")].row_count == 16

    def test_dst_boundary_keys_on_et_date_not_utc(self, tmp_store):
        # 19:00 ET is 23:00Z under EDT but 00:00Z of the NEXT day under EST — a
        # UTC bucket would file the EST after-hours bar under the wrong day
        edt = _minutes("2025-10-31", 30, start="19:00")
        est = _minutes("2025-11-03", 30, start="19:00")
        provider = FakeProvider({"SPY": pd.concat([edt, est], ignore_index=True)})
        _run(["SPY"], "1min", tmp_store, provider, now=pd.Timestamp("2025-12-01", tz="UTC"))

        assert _keys(tmp_store, "SPY", "1min") == ["2025-10-31", "2025-11-03"]
        assert tmp_store.rows[("SPY", "1min", "2025-11-03")].row_count == 30
        assert len(store.read_bars("SPY", "1min", "2025-10-31", "2025-11-04")) == 60

    def test_bars_outside_the_session_window_are_dropped(self, tmp_store):
        frame = pd.concat([_minutes("2026-09-01", 10, start="03:50"),
                           _minutes("2026-09-01", 10, start="09:30"),
                           _minutes("2026-09-01", 10, start="19:55")],
                          ignore_index=True)
        provider = FakeProvider({"SPY": frame})
        stats = _run(["SPY"], "1min", tmp_store, provider)

        # 03:50-03:59 is before the 04:00 open, 20:00-20:04 after the close;
        # only the 10 RTH bars and 19:55-19:59 survive
        assert stats.rows == 15
        assert tmp_store.rows[("SPY", "1min", "2026-09-01")].row_count == 15


class TestResumeAndIdempotency:
    def _seed(self, reg, day: str, count: int = 390) -> None:
        store.write_bars("SPY", "1min", day, _minutes(day, count),
                         provider="alpaca:sip", registry=reg)

    def test_resume_starts_one_period_before_the_high_water(self, tmp_store):
        self._seed(tmp_store, "2025-09-04")            # below full_start: no head gap
        self._seed(tmp_store, "2026-09-01")
        last = tmp_store.max_last_ts("SPY", "1min")

        provider = FakeProvider()
        stats = _run(["SPY"], "1min", tmp_store, provider)

        assert stats.stale == 1 and stats.fresh == 0
        assert provider.starts("SPY") == [pd.Timestamp(last) - pd.Timedelta(minutes=1)]

    def test_head_gap_below_the_stored_history_is_refetched(self, tmp_store):
        # the WS writer seeds today's file long before any history exists — the
        # high-water alone would declare the five years underneath already done
        self._seed(tmp_store, "2026-09-01")
        last = tmp_store.max_last_ts("SPY", "1min")

        provider = FakeProvider()
        _run(["SPY"], "1min", tmp_store, provider)

        head, tail = provider.calls[0], provider.calls[-1]
        assert head[2] == NOW - pd.Timedelta(days=365)
        assert head[3] == pd.Timestamp("2026-09-01", tz=ET).tz_convert("UTC")
        assert tail[2] == pd.Timestamp(last) - pd.Timedelta(minutes=1)

    def test_current_symbol_is_skipped_entirely(self, tmp_store):
        self._seed(tmp_store, "2025-09-04")
        store.write_bars("SPY", "1min", "2026-09-05",
                         _frame([NOW - pd.Timedelta(minutes=config.SIP_DELAY_MINUTES)]),
                         provider="alpaca:sip", registry=tmp_store)

        provider = FakeProvider()
        stats = _run(["SPY"], "1min", tmp_store, provider)

        assert stats.current == 1
        assert provider.calls == []

    def test_second_run_writes_zero_new_rows(self, tmp_store):
        provider = FakeProvider({"SPY": _minutes("2026-09-01", 390)})
        first = _run(["SPY"], "1min", tmp_store, provider)
        row = tmp_store.rows[("SPY", "1min", "2026-09-01")]

        second = _run(["SPY"], "1min", tmp_store, provider)

        assert first.rows == 390
        assert second.rows == 0
        after = tmp_store.rows[("SPY", "1min", "2026-09-01")]
        assert after.row_count == row.row_count == 390
        assert after.checksum == row.checksum


class TestFailureIsolation:
    def test_one_bad_symbol_does_not_abort_the_batch(self, tmp_store):
        provider = FakeProvider({"SPY": _minutes("2026-09-01", 390),
                                 "QQQ": _minutes("2026-09-01", 390)},
                                errors=["BAD"])
        stats = _run(["SPY", "BAD", "QQQ"], "1min", tmp_store, provider)

        assert stats.failed == ["BAD"]
        assert stats.stored == 2
        assert _keys(tmp_store, "SPY", "1min") == ["2026-09-01"]
        assert _keys(tmp_store, "QQQ", "1min") == ["2026-09-01"]
        assert [c[0] for c in provider.calls].count("BAD") == 2  # + recovery retry

    def test_recovery_pass_rescues_a_transient_failure(self, tmp_store):
        provider = FakeProvider({"SPY": _minutes("2026-09-01", 390)}, errors=["SPY"],
                                transient=True)
        stats = _run(["SPY"], "1min", tmp_store, provider)

        assert stats.failed == []
        assert stats.recovered == 1
        assert stats.rows == 390
