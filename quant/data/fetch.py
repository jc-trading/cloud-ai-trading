"""Bar sync orchestration — incremental fetch + store + registry high-water.

The Alpaca specifics (SIP feed, RAW adjustment, ``end = now - 15min``, the
closed-bar guards) live in ``quant/data/providers/alpaca_rest.py``; this module
only orchestrates and keeps its public signatures stable for backfill.py,
quant_tasks.py and the tests. The client and the ``now`` clock stay injectable.

Guards still enforced end to end:
  1. only CLOSED bars are stored — the provider drops the still-forming bar
  2. incremental: sync_* starts from ``registry.max_last_ts`` (overlapping the
     last stored bar by design; store.py dedupes on ts so re-fetch is idempotent)
  3. SIP feed with ``end = now - 15min`` (free-tier-legal full-market SIP)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from quant import config
from quant.data import providers, registry, store
from quant.data.providers.alpaca_rest import (  # re-exported: public API of this module
    drop_unclosed_daily,
    drop_unclosed_intraday,
)

logger = logging.getLogger(__name__)

_TF = "daily"


def fetch_daily_multi(symbols: list[str], start: datetime, end: datetime | None = None, *,
                      client=None, now: datetime | None = None) -> dict[str, pd.DataFrame]:
    return providers.get_historical().fetch_bars_multi(
        symbols, "daily", start, end, client=client, now=now)


def fetch_daily(symbol: str, start: datetime, end: datetime | None = None, *,
                client=None, now: datetime | None = None) -> pd.DataFrame:
    return providers.get_historical().fetch_bars(
        symbol, "daily", start, end, client=client, now=now)


def _store_daily_incremental(symbol: str, df: pd.DataFrame,
                             last: datetime | None, now: datetime) -> int:
    """Store fetched daily bars into their per-year files. Counts NEW rows
    against the prior registry high-water (review #1: the old
    ``before = len(stored)`` pattern read the parquet twice per sync)."""
    if df.empty:
        return 0
    store.write_frame(symbol, _TF, df, provider=config.PROVIDER_HISTORICAL)
    return int((df["ts"] > pd.Timestamp(last)).sum()) if last is not None else len(df)


def sync_daily(symbol: str, *, client=None, now: datetime | None = None,
               history_years: int = config.DAILY_HISTORY_YEARS) -> int:
    """Incrementally fetch + store daily bars. Returns NEW rows added."""
    now = now or datetime.now(timezone.utc)
    last = registry.max_last_ts(symbol.upper(), _TF)
    # start at last stored bar (overlap by 1; store dedupes) or full history
    start = last if last is not None else (now - timedelta(days=365 * history_years + 7))
    end = now - timedelta(minutes=config.SIP_DELAY_MINUTES)
    df = fetch_daily(symbol, start, end, client=client, now=now)
    return _store_daily_incremental(symbol, df, last, now)


def sync_daily_many(symbols: list[str], chunk_size: int = 200, *, client=None,
                    now: datetime | None = None,
                    history_years: int = config.DAILY_HISTORY_YEARS) -> tuple[int, list[str]]:
    """Batched incremental daily sync — ONE Alpaca request per chunk (review #1:
    the nightly cycle was ~500 serial round-trips through sync_daily).

    Each chunk shares one incremental start = the MIN of its symbols' registry
    last_ts (full-history fallback when any symbol is new); over-fetching a few
    days is fine because the store dedupes on ts. A failed chunk falls
    back to per-symbol sync_daily so one bad symbol never loses the whole
    chunk. Returns (synced_count, failed_symbols)."""
    now = now or datetime.now(timezone.utc)
    end = now - timedelta(minutes=config.SIP_DELAY_MINUTES)
    synced = 0
    failed: list[str] = []
    for i in range(0, len(symbols), chunk_size):
        chunk = list(symbols[i:i + chunk_size])
        chunk_ok = 0
        try:
            lasts = [registry.max_last_ts(s.upper(), _TF) for s in chunk]
            if any(ts is None for ts in lasts):
                start = now - timedelta(days=365 * history_years + 7)
            else:
                start = min(lasts)
            frames = fetch_daily_multi(chunk, start, end, client=client, now=now)
            for sym, last in zip(chunk, lasts):
                df = frames.get(sym.upper())
                if df is None:
                    df = pd.DataFrame(columns=list(config.BAR_COLUMNS))
                _store_daily_incremental(sym, df, last, now)
                chunk_ok += 1
        except Exception:
            logger.warning("sync_daily_many: chunk fetch failed (%d symbols) — "
                           "falling back to per-symbol sync", len(chunk),
                           exc_info=True)
            chunk_ok = 0
            for sym in chunk:
                try:
                    sync_daily(sym, client=client, now=now,
                               history_years=history_years)
                    chunk_ok += 1
                except Exception:
                    logger.warning("sync_daily_many: %s failed", sym, exc_info=True)
                    failed.append(sym)
        synced += chunk_ok
    return synced, failed
