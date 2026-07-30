"""Alpaca bar fetching — RAW, SIP feed, closed bars only (design §4.4–4.5).

Guards implemented here:
  1. only CLOSED bars are returned — the last, still-forming bar is dropped
  2. incremental: sync_* starts from ``manifest.last_ts`` (overlapping the last
     stored bar by design; store.py dedupes on ts so re-fetch is idempotent)
  3. SIP feed with ``end = now - 15min`` (free-tier-legal full-market SIP)

Network access is confined to this module. The Alpaca client and the ``now``
clock are injectable so the closed-bar logic is unit-testable without the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from dotenv import dotenv_values

from quant import config
from quant.data import manifest, store

_SOURCE = "alpaca"
# A daily bar's timestamp from Alpaca is anchored at ET midnight (expressed in
# UTC), so the regular-session close is bar_ts + 16h; +20m buffer for late SIP
# consolidation. This holds year-round across EST/EDT without a tz conversion.
_DAILY_CLOSE_OFFSET = pd.Timedelta(hours=16, minutes=20)


@lru_cache(maxsize=1)
def _keys() -> tuple[str, str]:
    cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
    key = cfg.get("ALPACA_API_KEY")
    sec = cfg.get("ALPACA_API_SECRET")
    if not key or not sec:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET missing from .env")
    return key, sec


@lru_cache(maxsize=1)
def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    key, sec = _keys()
    return StockHistoricalDataClient(key, sec)


def _timeframe(unit: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    if unit == "1Day":
        return TimeFrame.Day
    if unit == "5Min":
        return TimeFrame(5, TimeFrameUnit.Minute)
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


def _fetch(symbol: str, unit: str, start: datetime, end: datetime, client=None) -> pd.DataFrame:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.enums import Adjustment, DataFeed

    client = client or _client()
    req = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=_timeframe(unit),
        start=start,
        end=end,
        feed=DataFeed.SIP,          # design §4.5 / research B1
        adjustment=Adjustment.RAW,  # design §4.1 — store RAW, adjust on read
    )
    resp = client.get_stock_bars(req)
    df = resp.df
    if df is None or len(df) == 0:
        return store.normalize(pd.DataFrame(columns=list(config.BAR_COLUMNS)))
    return store.normalize(df)


def fetch_daily_multi(symbols: list[str], start: datetime, end: datetime | None = None, *,
                      client=None, now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """Fetch daily bars for many symbols in ONE Alpaca request (backfill path).
    Returns {symbol: closed RAW daily frame}. Symbols with no data are omitted."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.enums import Adjustment, DataFeed

    now = now or datetime.now(timezone.utc)
    end = end or (now - timedelta(minutes=config.SIP_DELAY_MINUTES))
    client = client or _client()
    req = StockBarsRequest(
        symbol_or_symbols=[s.upper() for s in symbols],
        timeframe=_timeframe("1Day"),
        start=start, end=end,
        feed=DataFeed.SIP, adjustment=Adjustment.RAW,
    )
    resp = client.get_stock_bars(req)
    df = resp.df
    if df is None or len(df) == 0:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for sym, sub in df.groupby(level="symbol"):
        ndf = drop_unclosed_daily(store.normalize(sub), now)
        if not ndf.empty:
            out[str(sym)] = ndf
    return out


def fetch_daily(symbol: str, start: datetime, end: datetime | None = None, *,
                client=None, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    end = end or (now - timedelta(minutes=config.SIP_DELAY_MINUTES))
    df = _fetch(symbol, "1Day", start, end, client=client)
    return drop_unclosed_daily(df, now)


def fetch_intraday(symbol: str, start: datetime, end: datetime | None = None, *,
                   client=None, now: datetime | None = None, tf_minutes: int = 5) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    end = end or (now - timedelta(minutes=config.SIP_DELAY_MINUTES))
    df = _fetch(symbol, "5Min", start, end, client=client)
    return drop_unclosed_intraday(df, now, tf_minutes)


def _upsert_from_store(symbol: str, timeframe: str, stored: pd.DataFrame,
                       now: datetime, session: str) -> None:
    if stored.empty:
        return
    manifest.upsert(
        symbol, timeframe,
        first_ts=stored["ts"].min().to_pydatetime(),
        last_ts=stored["ts"].max().to_pydatetime(),
        row_count=len(stored),
        fetched_at=now,
        session=session,
        source=_SOURCE,
    )


def sync_daily(symbol: str, *, client=None, now: datetime | None = None,
               history_years: int = config.DAILY_HISTORY_YEARS) -> int:
    """Incrementally fetch + store daily bars. Returns NEW rows added."""
    now = now or datetime.now(timezone.utc)
    last = manifest.get_last_ts(symbol, "1d")
    # start at last stored bar (overlap by 1; store dedupes) or full history
    start = last if last is not None else (now - timedelta(days=365 * history_years + 7))
    end = now - timedelta(minutes=config.SIP_DELAY_MINUTES)
    before = len(store.read_daily(symbol))
    df = fetch_daily(symbol, start, end, client=client, now=now)
    if not df.empty:
        store.write_daily(symbol, df)
    stored = store.read_daily(symbol)
    _upsert_from_store(symbol, "1d", stored, now, session="regular")
    return len(stored) - before


def sync_intraday(symbol: str, *, client=None, now: datetime | None = None,
                  history_years: int = config.INTRADAY_HISTORY_YEARS) -> int:
    """Incrementally fetch + store 5m bars. Returns NEW rows added."""
    now = now or datetime.now(timezone.utc)
    last = manifest.get_last_ts(symbol, "5m")
    start = last if last is not None else (now - timedelta(days=365 * history_years + 7))
    end = now - timedelta(minutes=config.SIP_DELAY_MINUTES)
    df = fetch_intraday(symbol, start, end, client=client, now=now)
    # write_intraday returns merged TOTALS per touched month file — count NEW
    # rows against the prior high-water instead (review F8: row_count inflation)
    added = 0
    if not df.empty:
        store.write_intraday(symbol, df)
        added = int((df["ts"] > pd.Timestamp(last)).sum()) if last is not None else len(df)
    # recompute manifest high-water from what we just stored this run
    if not df.empty:
        # last_ts is the max ts we stored; first_ts tracked coarsely from this batch
        prev = manifest.get_row(symbol, "5m")
        first = df["ts"].min().to_pydatetime()
        if prev and prev.first_ts and prev.first_ts < df["ts"].min().to_pydatetime():
            first = prev.first_ts
        manifest.upsert(
            symbol, "5m",
            first_ts=first,
            last_ts=df["ts"].max().to_pydatetime(),
            row_count=(prev.row_count if prev else 0) + added,
            fetched_at=now, session="all", source=_SOURCE,
        )
    return added
