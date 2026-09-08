"""Chart candles read from the Parquet store (方案 Phase 7).

Covers the store/REST split and the bug the phase exists to kill: the old code
called Alpaca without a `start`, so every timeframe returned today's bars only.

The 1m regression writes real 1min files through ``store.write_bars`` into a
temp BARS_ROOT (registry stubbed — provenance is Phase 2's business, not this
one's), then asserts the service returns bars spanning several days.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.modules.market import service as svc
from app.modules.market.service import MarketService
from quant import config as qconfig
from quant.data import store


# ---- fakes ----------------------------------------------------------------

class _FakeRegistry:
    """Enough of quant.data.registry for write_bars to run without PostgreSQL."""

    def __init__(self):
        self.rows = {}

    def get_row(self, symbol, timeframe, period_key):
        return self.rows.get((symbol, timeframe, period_key))

    def upsert(self, symbol, timeframe, period_key, **kw):
        self.rows[(symbol, timeframe, period_key)] = type(
            "Row", (), {"provider": kw["provider"], **kw})()


def _frame(ts_index) -> pd.DataFrame:
    n = len(ts_index)
    return pd.DataFrame({
        "ts": ts_index,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
        "vwap": [100.2 + i for i in range(n)],
        "trade_count": [10 + i for i in range(n)],
    })


@pytest.fixture()
def store_root(tmp_path, monkeypatch):
    monkeypatch.setattr(qconfig, "BARS_ROOT", tmp_path)
    monkeypatch.setattr(store, "_registry", _FakeRegistry())
    return tmp_path


@pytest.fixture()
def no_rest(monkeypatch):
    """Fail loudly if a test that should be served by the store hits the network."""
    async def _boom(*a, **kw):
        raise AssertionError("REST fallback must not run when the store has data")

    monkeypatch.setattr(svc, "_alpaca_rest_candles", _boom)


# ---- window sizing --------------------------------------------------------

class TestCandleWindow:
    def test_daily_window_covers_weekends(self):
        end = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
        start, got_end = svc._candle_window("1d", 200, end)
        assert got_end == end
        # 200 sessions need >= 280 calendar days; never fewer than the sessions
        assert (end - start).days >= 280

    def test_intraday_window_is_multi_day(self):
        end = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
        start, _ = svc._candle_window("1m", 780, end)
        assert (end - start).days > 1

    def test_window_grows_with_limit(self):
        end = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
        small, _ = svc._candle_window("15m", 50, end)
        large, _ = svc._candle_window("15m", 500, end)
        assert large < small


# ---- store hit / miss -----------------------------------------------------

class TestStoreReads:
    def test_store_hit_serves_store_rows(self, store_root, no_rest, monkeypatch):
        day = pd.Timestamp("2026-03-02 14:30", tz="UTC")
        ts = pd.date_range(day, periods=30, freq="1min", tz="UTC")
        store.write_bars("ZZTEST", "1min", "2026-03-02", _frame(ts), provider="alpaca:sip")

        monkeypatch.setattr(svc, "_candle_window", lambda i, l, e=None: (
            datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 3, tzinfo=timezone.utc)))
        rows = asyncio.run(MarketService.get_stock_candles("ZZTEST", "1m", 10))

        assert len(rows) == 10
        assert rows[-1]["close"] == 129.5              # tail of the written frame
        assert rows[0]["timestamp"] < rows[-1]["timestamp"]

    def test_1m_spans_more_than_one_day(self, store_root, no_rest, monkeypatch):
        """The old no-`start` REST call could only ever return today."""
        for day in ("2026-03-02", "2026-03-03", "2026-03-04"):
            ts = pd.date_range(pd.Timestamp(f"{day} 14:30", tz="UTC"),
                               periods=390, freq="1min", tz="UTC")
            store.write_bars("ZZTEST", "1min", day, _frame(ts), provider="alpaca:sip")

        monkeypatch.setattr(svc, "_candle_window", lambda i, l, e=None: (
            datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 5, tzinfo=timezone.utc)))
        rows = asyncio.run(MarketService.get_stock_candles("ZZTEST", "1m", 2000))

        assert len(rows) == 1170
        days = {datetime.fromtimestamp(r["timestamp"] / 1000, timezone.utc).date()
                for r in rows}
        assert len(days) == 3

    def test_limit_takes_the_tail(self, store_root, no_rest, monkeypatch):
        ts = pd.date_range(pd.Timestamp("2026-03-02 14:30", tz="UTC"),
                           periods=100, freq="1min", tz="UTC")
        store.write_bars("ZZTEST", "1min", "2026-03-02", _frame(ts), provider="alpaca:sip")

        monkeypatch.setattr(svc, "_candle_window", lambda i, l, e=None: (
            datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 3, tzinfo=timezone.utc)))
        rows = asyncio.run(MarketService.get_stock_candles("ZZTEST", "1m", 5))

        assert len(rows) == 5
        assert rows[-1]["close"] == 199.5              # last bar of the 100 written

    def test_intraday_is_read_raw(self, store_root, monkeypatch):
        """get_bars refuses an adjustment on intraday — the service must ask for none."""
        seen = {}

        def _spy(symbol, timeframe, start=None, end=None, adjust="split_div", session="regular"):
            seen[timeframe] = adjust
            return pd.DataFrame(columns=list(qconfig.BAR_COLUMNS))

        monkeypatch.setattr("quant.data.bars.get_bars", _spy)
        for interval, expected in (("1m", "none"), ("5m", "none"), ("15m", "none"),
                                   ("1h", "none"), ("1d", "split_div")):
            svc._read_store_candles("ZZTEST", interval,
                                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                                    datetime(2026, 3, 3, tzinfo=timezone.utc), 10)
            assert seen[svc.STORE_INTERVAL_MAP[interval]] == expected


# ---- REST fallback --------------------------------------------------------

class TestRestFallback:
    def test_empty_store_falls_back_with_start(self, store_root, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                pass

            @staticmethod
            def json():
                return {"bars": {"ZZTEST": [
                    {"t": "2026-03-02T14:30:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10},
                    {"t": "2026-03-03T14:30:00Z", "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 20},
                ]}}

        class _Client:
            @staticmethod
            async def get(url, params=None):
                captured["url"] = url
                captured["params"] = params
                return _Resp()

        monkeypatch.setattr(svc, "_get_alpaca_client", lambda: _Client())
        rows = asyncio.run(MarketService.get_stock_candles("ZZTEST", "1h", 100))

        assert captured["params"]["start"].endswith("Z")
        assert captured["params"]["end"].endswith("Z")
        assert captured["params"]["feed"] == "iex"
        assert captured["params"]["timeframe"] == "1Hour"
        assert [r["close"] for r in rows] == [1.5, 2.0]

    def test_fallback_writes_nothing_to_the_store(self, store_root, monkeypatch):
        monkeypatch.setattr(svc, "_get_alpaca_client", lambda: None)
        assert asyncio.run(MarketService.get_stock_candles("ZZTEST", "1d", 10)) == []
        assert not (store_root / "ZZTEST").exists()

    def test_flag_off_skips_the_store(self, store_root, monkeypatch):
        ts = pd.date_range(pd.Timestamp("2026-03-02 14:30", tz="UTC"),
                           periods=30, freq="1min", tz="UTC")
        store.write_bars("ZZTEST", "1min", "2026-03-02", _frame(ts), provider="alpaca:sip")

        def _no_store(*a, **kw):
            raise AssertionError("store must not be read when the flag is off")

        monkeypatch.setattr(svc, "_read_store_candles", _no_store)
        monkeypatch.setattr(svc, "_get_alpaca_client", lambda: None)

        settings = svc.get_settings()
        monkeypatch.setattr(settings, "MARKET_CANDLES_FROM_STORE", False)
        assert asyncio.run(MarketService.get_stock_candles("ZZTEST", "1m", 10)) == []
