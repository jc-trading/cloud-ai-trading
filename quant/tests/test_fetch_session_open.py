"""Phase C step 2 — the 09:30 ET opening-minute price feed for open_once entries.

Fake Alpaca client throughout (no network, no store writes): the request params,
the exact-minute selection, early-close days, and the missing/unclosed bar cases
that make the entry cycle fail closed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from quant import config
from quant.data import fetch
from quant.data.calendar import rth_bounds

NORMAL_DAY = date(2026, 8, 18)
EARLY_CLOSE_DAY = date(2025, 11, 28)      # Friday after Thanksgiving, 13:00 ET
HOLIDAY = date(2026, 7, 3)                # observed Independence Day


def _bars(ts_list, base):
    return pd.DataFrame({
        "ts": pd.to_datetime(ts_list, utc=True),
        "open": [base + i for i in range(len(ts_list))],
        "high": [base + i + 1 for i in range(len(ts_list))],
        "low": [base + i - 1 for i in range(len(ts_list))],
        "close": [base + i + 0.5 for i in range(len(ts_list))],
        "volume": [1000] * len(ts_list),
        "vwap": [base + i for i in range(len(ts_list))],
        "trade_count": [10] * len(ts_list),
    })


class _Resp:
    def __init__(self, df):
        self.df = df


class _Client:
    """Returns the given per-symbol minute bars and records every request."""

    def __init__(self, sym_bars: dict[str, tuple[list[str], float]]):
        self._sym_bars = sym_bars
        self.calls = []

    def get_stock_bars(self, req):
        self.calls.append(req)
        frames = []
        for sym, (ts_list, base) in self._sym_bars.items():
            df = _bars(ts_list, base)
            df.index = pd.MultiIndex.from_arrays(
                [[sym] * len(df), df["ts"]], names=["symbol", "timestamp"])
            frames.append(df.drop(columns=["ts"]))
        if not frames:
            return _Resp(None)
        return _Resp(pd.concat(frames))


def _after_open(day: date, minutes: int) -> datetime:
    open_ts, _ = rth_bounds(day)
    return (open_ts + pd.Timedelta(minutes=minutes)).to_pydatetime()


def _client_for(day: date, symbols: dict[str, float], *, count: int = 3):
    open_ts, _ = rth_bounds(day)
    return _Client({s: ([(open_ts + pd.Timedelta(minutes=i)).isoformat()
                         for i in range(count)], base)
                    for s, base in symbols.items()})


def test_returns_the_open_of_the_0930_bar():
    client = _client_for(NORMAL_DAY, {"AAA": 100.0, "BBB": 50.0})
    out = fetch.session_open_prices(["AAA", "bbb"], NORMAL_DAY, client=client,
                                    now=_after_open(NORMAL_DAY, 20))
    assert out == {"AAA": 100.0, "BBB": 50.0}          # the 09:31 bar is 101/51


def test_request_uses_sip_raw_1min_and_the_session_open_window():
    client = _client_for(NORMAL_DAY, {"AAA": 100.0})
    fetch.session_open_prices(["AAA"], NORMAL_DAY, client=client,
                              now=_after_open(NORMAL_DAY, 20))
    req = client.calls[0]
    open_ts, _ = rth_bounds(NORMAL_DAY)
    assert req.feed.value == config.DATA_FEED
    assert req.adjustment.value == "raw"
    assert req.timeframe.value == "1Min"
    # the alpaca request model normalises to naive UTC (same as every other
    # fetch path in this repo), so compare on UTC instants
    assert pd.Timestamp(req.start).tz_localize("UTC") == open_ts
    assert pd.Timestamp(req.end).tz_localize("UTC") == open_ts + pd.Timedelta(minutes=1)


def test_early_close_day_still_opens_at_0930():
    client = _client_for(EARLY_CLOSE_DAY, {"AAA": 100.0})
    out = fetch.session_open_prices(["AAA"], EARLY_CLOSE_DAY, client=client,
                                    now=_after_open(EARLY_CLOSE_DAY, 20))
    assert out == {"AAA": 100.0}
    open_ts, _ = rth_bounds(EARLY_CLOSE_DAY)
    assert open_ts.tz_convert("America/New_York").strftime("%H:%M") == "09:30"


def test_symbol_without_an_open_bar_is_omitted():
    """The provider returned the name but its first bar is 09:31 — a symbol that
    did not print at the open must not get a guessed fill price."""
    open_ts, _ = rth_bounds(NORMAL_DAY)
    client = _Client({
        "AAA": ([(open_ts + pd.Timedelta(minutes=i)).isoformat() for i in range(2)], 100.0),
        "LATE": ([(open_ts + pd.Timedelta(minutes=i)).isoformat() for i in (1, 2)], 70.0),
    })
    out = fetch.session_open_prices(["AAA", "LATE"], NORMAL_DAY, client=client,
                                    now=_after_open(NORMAL_DAY, 20))
    assert out == {"AAA": 100.0}


def test_unclosed_open_bar_is_not_returned():
    """Called one minute into the session the 09:30 bar is still forming."""
    client = _client_for(NORMAL_DAY, {"AAA": 100.0})
    assert fetch.session_open_prices(["AAA"], NORMAL_DAY, client=client,
                                     now=_after_open(NORMAL_DAY, 0)) == {}


def test_empty_input_and_non_session_never_call_the_provider():
    client = _client_for(NORMAL_DAY, {"AAA": 100.0})
    assert fetch.session_open_prices([], NORMAL_DAY, client=client) == {}
    assert fetch.session_open_prices(["AAA"], HOLIDAY, client=client) == {}
    assert client.calls == []


def test_provider_returning_nothing_yields_an_empty_map():
    assert fetch.session_open_prices(["AAA"], NORMAL_DAY, client=_Client({}),
                                     now=_after_open(NORMAL_DAY, 20)) == {}


def test_nothing_is_written_to_the_store(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch.store, "write_frame",
                        lambda *a, **kw: calls.append(a))
    client = _client_for(NORMAL_DAY, {"AAA": 100.0})
    fetch.session_open_prices(["AAA"], NORMAL_DAY, client=client,
                              now=_after_open(NORMAL_DAY, 20))
    assert calls == []
