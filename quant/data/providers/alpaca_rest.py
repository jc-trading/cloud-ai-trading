"""Alpaca REST bar provider — RAW, SIP feed, closed bars only (design §4.4–4.5).

Moved verbatim out of ``quant/data/fetch.py`` (Phase 1); ``fetch.py`` keeps the
sync_* orchestration and delegates here. Guards implemented in this module:

  1. only CLOSED bars are returned — the last, still-forming bar is dropped
  2. SIP feed with ``end = now - 15min`` (free-tier-legal full-market SIP)
  3. RAW adjustment — a split never rewrites a stored file

Network access is confined to this module. The Alpaca client and the ``now``
clock are injectable so the closed-bar logic is unit-testable without the network.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from dotenv import dotenv_values

from quant import config
from quant.data import store
from quant.data.providers.base import TIMEFRAMES

# A daily bar's timestamp from Alpaca is anchored at ET midnight (expressed in
# UTC), so the regular-session close is bar_ts + 16h; +20m buffer for late SIP
# consolidation. This holds year-round across EST/EDT without a tz conversion.
_DAILY_CLOSE_OFFSET = pd.Timedelta(hours=16, minutes=20)

# CAT timeframe vocabulary -> Alpaca unit
_ALPACA_UNIT: dict[str, str] = {"daily": "1Day", "1hour": "1Hour", "1min": "1Min"}
_INTRADAY_MINUTES: dict[str, int] = {"1hour": 60, "1min": 1}


@lru_cache(maxsize=1)
def _keys() -> tuple[str, str]:
    # host runs read repo .env; inside the backend container the keys arrive as
    # ENV vars instead (compose passthrough) and no .env is mounted
    cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
    key = cfg.get("ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY")
    sec = cfg.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET missing from .env")
    return key, sec


@lru_cache(maxsize=1)
def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    key, sec = _keys()
    return StockHistoricalDataClient(key, sec)


def _timeframe(unit: str):
    from alpaca.data.timeframe import TimeFrame

    if unit == "1Day":
        return TimeFrame.Day
    if unit == "1Hour":
        return TimeFrame.Hour
    if unit == "1Min":
        return TimeFrame.Minute
    raise ValueError(f"unsupported timeframe {unit!r}")


def _now(now: datetime | None) -> pd.Timestamp:
    return pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC") \
        if (now and now.tzinfo) else pd.Timestamp(now or datetime.now(timezone.utc), tz="UTC")


def drop_unclosed_daily(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Keep only daily bars whose regular session has closed."""
    if df.empty:
        return df
    now_ts = _now(now)
    closed = (df["ts"] + _DAILY_CLOSE_OFFSET) <= now_ts
    return df[closed].reset_index(drop=True)


def drop_unclosed_intraday(df: pd.DataFrame, now: datetime, tf_minutes: int) -> pd.DataFrame:
    """Keep only intraday bars whose [ts, ts+tf) window has fully elapsed."""
    if df.empty:
        return df
    now_ts = _now(now)
    closed = (df["ts"] + pd.Timedelta(minutes=tf_minutes)) <= now_ts
    return df[closed].reset_index(drop=True)


def _feed(name: str):
    from alpaca.data.enums import DataFeed

    return DataFeed.SIP if name == "sip" else DataFeed.IEX


def _fetch(symbol: str, unit: str, start: datetime, end: datetime, client=None,
           feed: str = config.DATA_FEED) -> pd.DataFrame:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.enums import Adjustment

    client = client or _client()
    req = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=_timeframe(unit),
        start=start,
        end=end,
        feed=_feed(feed),           # design §4.5 / research B1
        adjustment=Adjustment.RAW,  # design §4.1 — store RAW, adjust on read
    )
    resp = client.get_stock_bars(req)
    df = resp.df
    if df is None or len(df) == 0:
        return store.normalize(pd.DataFrame(columns=list(config.BAR_COLUMNS)))
    return store.normalize(df)


def _fetch_multi(symbols: list[str], unit: str, start: datetime, end: datetime,
                 client=None, feed: str = config.DATA_FEED) -> pd.DataFrame | None:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.enums import Adjustment

    client = client or _client()
    req = StockBarsRequest(
        symbol_or_symbols=[s.upper() for s in symbols],
        timeframe=_timeframe(unit),
        start=start, end=end,
        feed=_feed(feed), adjustment=Adjustment.RAW,
    )
    resp = client.get_stock_bars(req)
    return resp.df


def _drop_unclosed(df: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    if timeframe == "daily":
        return drop_unclosed_daily(df, now)
    return drop_unclosed_intraday(df, now, _INTRADAY_MINUTES[timeframe])


class AlpacaRestProvider:
    """Historical BarProvider over Alpaca's REST bars endpoint (SIP feed)."""

    provider_key = "alpaca:sip"
    feed = config.DATA_FEED
    delay_minutes = config.SIP_DELAY_MINUTES

    def supports(self, timeframe: str) -> bool:
        return timeframe in TIMEFRAMES

    def fetch_bars(self, symbol: str, timeframe: str, start: datetime,
                   end: datetime | None = None, *, client=None,
                   now: datetime | None = None) -> pd.DataFrame:
        if not self.supports(timeframe):
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        now = now or datetime.now(timezone.utc)
        end = end or (now - timedelta(minutes=self.delay_minutes))
        df = _fetch(symbol, _ALPACA_UNIT[timeframe], start, end, client=client,
                    feed=self.feed)
        return _drop_unclosed(df, timeframe, now)

    def fetch_bars_multi(self, symbols: list[str], timeframe: str, start: datetime,
                         end: datetime | None = None, *, client=None,
                         now: datetime | None = None) -> dict[str, pd.DataFrame]:
        """Fetch bars for many symbols in ONE Alpaca request (backfill path).
        Returns {symbol: closed RAW frame}. Symbols with no data are omitted;
        a failed request propagates so the caller can retry per symbol."""
        if not self.supports(timeframe):
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        now = now or datetime.now(timezone.utc)
        end = end or (now - timedelta(minutes=self.delay_minutes))
        df = _fetch_multi(list(symbols), _ALPACA_UNIT[timeframe], start, end,
                          client=client, feed=self.feed)
        if df is None or len(df) == 0:
            return {}
        out: dict[str, pd.DataFrame] = {}
        for sym, sub in df.groupby(level="symbol"):
            ndf = _drop_unclosed(store.normalize(sub), timeframe, now)
            if not ndf.empty:
                out[str(sym)] = ndf
        return out


class AlpacaIexRestProvider(AlpacaRestProvider):
    """IEX REST bars — the gap filler behind the IEX WebSocket stream.

    Same provenance key as the stream so a recovered minute merges into the day
    file instead of tripping the single-source rule. IEX carries no 15-minute
    delay rule, so ``end`` is not held back; the multi-symbol backfill path is
    inherited but unused (history stays on SIP).
    """

    provider_key = "alpaca:iex"
    feed = "iex"
    delay_minutes = 0
