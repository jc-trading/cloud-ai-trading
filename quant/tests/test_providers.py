"""Phase 1 provider abstraction — provider keys, closed-bar filtering, the
multi-symbol REST path, the SIP/RAW request params (golden protection), and the
IEX WebSocket adapter, all with fake clients (no network)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from quant import config
from quant.data import providers
from quant.data.providers import alpaca_ws
from quant.data.providers.alpaca_rest import AlpacaRestProvider
from quant.data.providers.alpaca_ws import AlpacaWsSource, SymbolCapExceeded
from quant.data.providers.base import StreamAuthError

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


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


def _alpaca_df(sym_bars: dict[str, list[str]]) -> pd.DataFrame:
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


class _C:
    def __init__(self, sym_bars):
        self._sym_bars = sym_bars
        self.calls = []

    def get_stock_bars(self, req):
        self.calls.append(req)
        return _Resp(_alpaca_df(self._sym_bars))


# --- factory / provider keys ----------------------------------------------

def test_provider_keys_and_factory():
    assert AlpacaRestProvider.provider_key == "alpaca:sip"
    assert AlpacaWsSource.provider_key == "alpaca:iex"
    assert config.PROVIDER_HISTORICAL == "alpaca:sip"
    assert config.PROVIDER_REALTIME == "alpaca:iex"
    assert providers.get_historical().provider_key == config.PROVIDER_HISTORICAL
    assert providers.get_realtime().provider_key == config.PROVIDER_REALTIME
    with pytest.raises(ValueError):
        providers.get_historical("ibkr")


def test_supports_timeframe_vocabulary():
    p = AlpacaRestProvider()
    assert [p.supports(tf) for tf in ("daily", "1hour", "1min")] == [True, True, True]
    assert not p.supports("5Min")
    with pytest.raises(ValueError):
        p.fetch_bars("AAPL", "5Min", NOW - timedelta(days=1), NOW)


# --- closed-bar filtering --------------------------------------------------

def test_fetch_bars_drops_unclosed_daily():
    client = _C({"AAPL": ["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"]})
    p = AlpacaRestProvider()
    # 07-24 19:00Z = 15:00 ET, before the 16:20 ET cutoff -> today's bar unclosed
    now = datetime(2026, 7, 24, 19, 0, tzinfo=timezone.utc)
    out = p.fetch_bars("AAPL", "daily", now - timedelta(days=5), now, client=client, now=now)
    assert list(out["ts"].dt.strftime("%Y-%m-%d")) == ["2026-07-23"]
    assert list(out.columns) == list(config.BAR_COLUMNS)
    assert str(out["ts"].dt.tz) == "UTC"


def test_fetch_bars_drops_unclosed_minute_bars():
    client = _C({"AAPL": ["2026-07-24T14:00:00Z", "2026-07-24T14:01:00Z"]})
    p = AlpacaRestProvider()
    now = datetime(2026, 7, 24, 14, 1, 30, tzinfo=timezone.utc)
    out = p.fetch_bars("AAPL", "1min", now - timedelta(hours=1), now, client=client, now=now)
    assert list(out["ts"].dt.strftime("%H:%M")) == ["14:00"]


# --- request params (golden protection: feed=sip + adjustment=RAW) ---------

@pytest.mark.parametrize("timeframe,unit", [("daily", "1Day"), ("1hour", "1Hour"),
                                            ("1min", "1Min")])
def test_request_keeps_sip_and_raw(timeframe, unit):
    from alpaca.data.enums import Adjustment, DataFeed

    client = _C({"AAPL": ["2026-07-23T04:00:00Z"]})
    p = AlpacaRestProvider()
    p.fetch_bars("AAPL", timeframe, NOW - timedelta(days=5), NOW, client=client, now=NOW)
    p.fetch_bars_multi(["AAPL", "MSFT"], timeframe, NOW - timedelta(days=5), NOW,
                       client=client, now=NOW)
    assert len(client.calls) == 2
    for req in client.calls:
        assert req.feed == DataFeed.SIP
        assert req.adjustment == Adjustment.RAW
        assert str(req.timeframe) == unit


def test_fetch_bars_multi_one_request_per_call():
    ts = ["2026-07-23T04:00:00Z", "2026-07-24T04:00:00Z"]
    client = _C({"AAA": ts, "BBB": ts})
    out = AlpacaRestProvider().fetch_bars_multi(["AAA", "BBB"], "daily",
                                                NOW - timedelta(days=5), NOW,
                                                client=client, now=NOW)
    assert len(client.calls) == 1
    assert sorted(client.calls[0].symbol_or_symbols) == ["AAA", "BBB"]
    assert sorted(out) == ["AAA", "BBB"]
    assert len(out["AAA"]) == 2


def test_fetch_bars_multi_omits_empty_symbols():
    client = _C({"AAA": ["2026-07-23T04:00:00Z"]})
    out = AlpacaRestProvider().fetch_bars_multi(["AAA", "BBB"], "daily",
                                                NOW - timedelta(days=5), NOW,
                                                client=client, now=NOW)
    assert list(out) == ["AAA"]


def test_fetch_bars_multi_propagates_failure_for_caller_retry():
    class _Bad:
        def get_stock_bars(self, req):
            raise RuntimeError("batch failed")

    with pytest.raises(RuntimeError):
        AlpacaRestProvider().fetch_bars_multi(["AAA"], "daily", NOW - timedelta(days=5),
                                              NOW, client=_Bad(), now=NOW)


# --- WebSocket adapter -----------------------------------------------------

def _msg(**kw):
    base = dict(symbol="aapl", timestamp=pd.Timestamp("2026-07-24T14:00:00Z"),
                open=1.0, high=2.0, low=0.5, close=1.5, volume=1000.0,
                vwap=1.25, trade_count=42.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_ws_converts_bar_message():
    bar = alpaca_ws.to_bar(_msg())
    assert bar.symbol == "AAPL"
    assert bar.ts == pd.Timestamp("2026-07-24T14:00:00Z")
    assert (bar.open, bar.high, bar.low, bar.close) == (1.0, 2.0, 0.5, 1.5)
    assert (bar.volume, bar.vwap, bar.trade_count) == (1000.0, 1.25, 42.0)
    assert alpaca_ws.to_bar(_msg(vwap=None, trade_count=None)).vwap is None


def test_ws_handler_emits_bar_to_callback():
    class _Stream:
        def __init__(self):
            self.subscribed = None
            self.ran = False

        def subscribe_bars(self, handler, *symbols):
            self.subscribed = (handler, symbols)

        def run(self):
            self.ran = True

    stream = _Stream()
    src = AlpacaWsSource(stream=stream)
    seen = []
    src.subscribe(["aapl", "msft"])
    src.run(seen.append)
    assert stream.ran and stream.subscribed[1] == ("AAPL", "MSFT")
    asyncio.run(stream.subscribed[0](_msg()))
    assert [b.symbol for b in seen] == ["AAPL"]


def test_ws_symbol_cap_raises_never_silently_drops():
    src = AlpacaWsSource(stream=object())
    ok = [f"S{i}" for i in range(alpaca_ws.MAX_STREAM_SYMBOLS)]
    src.subscribe(ok)
    assert len(src._symbols) == alpaca_ws.MAX_STREAM_SYMBOLS
    with pytest.raises(SymbolCapExceeded):
        src.subscribe(ok + ["EXTRA"])
    assert len(src._symbols) == alpaca_ws.MAX_STREAM_SYMBOLS


# --- WS auth escape hatch ---------------------------------------------------

class _StockDataStreamLike:
    """The slice of alpaca-py's StockDataStream that _run_forever exercises: it
    retries whatever _start_ws raises until _should_run is cleared."""

    def __init__(self, failure):
        self._failure = failure
        self._should_run = True
        self.attempts = 0

    def subscribe_bars(self, handler, *symbols):
        self.handler = handler

    async def _start_ws(self):
        self.attempts += 1
        raise self._failure

    def run(self):
        async def _run_forever():
            while self._should_run and self.attempts < 50:
                try:
                    await self._start_ws()
                except Exception:
                    continue

        asyncio.run(_run_forever())


def test_ws_run_raises_on_auth_rejection_instead_of_retrying_forever():
    stream = _StockDataStreamLike(ValueError("auth failed"))
    src = AlpacaWsSource(stream=stream)
    src.subscribe(["AAPL"])
    with pytest.raises(StreamAuthError):
        src.run(lambda bar: None)
    assert stream.attempts == 1
    assert stream._should_run is False
    assert stream._start_ws.__name__ == "_start_ws"   # the wrapper is removed again


def test_ws_run_keeps_retrying_a_transient_failure():
    stream = _StockDataStreamLike(ValueError("connected message not received"))
    src = AlpacaWsSource(stream=stream)
    src.subscribe(["AAPL"])
    src.run(lambda bar: None)                          # returns, no auth error
    assert stream.attempts == 50 and stream._should_run is True
