"""Point-in-time S&P 500 constituents (design §4.6, D3).

Source: fja05680/sp500 — a public CSV of `date,tickers` snapshots recording the
index membership on each change date (1996-present). Using the as-of membership
(not today's) is what prevents survivorship bias in the backtest.

The CSV is cached under cat-data/universe/ with its source URL + snapshot date
recorded alongside (D3 requirement). If the download ever fails and no cache
exists, callers must treat it as an OPEN ISSUE and NOT silently fall back to
today's list.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone
from functools import lru_cache

import pandas as pd

from quant import config

_SOURCE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
_CSV_PATH = config.UNIVERSE_DIR / "sp500_changes.csv"
_META_PATH = config.UNIVERSE_DIR / "sp500_changes.source.json"


def download_constituents(*, force: bool = False) -> None:
    """Fetch + cache the constituents changelog CSV (idempotent)."""
    if _CSV_PATH.exists() and not force:
        return
    config.UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_SOURCE_URL, timeout=30) as resp:
        raw = resp.read()
    if raw[:3] == b"404" or len(raw) < 1000:
        raise RuntimeError(f"S&P500 constituents download looks wrong ({len(raw)} bytes)")
    _CSV_PATH.write_bytes(raw)
    _META_PATH.write_text(json.dumps({
        "source_url": _SOURCE_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "bytes": len(raw),
    }, indent=2))


@lru_cache(maxsize=1)
def _changes() -> pd.DataFrame:
    """Load the changelog as (date -> frozenset of tickers), sorted by date."""
    if not _CSV_PATH.exists():
        download_constituents()
    df = pd.read_csv(_CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["members"] = df["tickers"].apply(lambda s: frozenset(str(s).split(",")))
    return df[["date", "members"]]


@lru_cache(maxsize=8192)
def constituents_set_on(d: date) -> frozenset[str]:
    """S&P500 members as of date d, cached per date — fast enough to call once
    per simulated day (the B3 point-in-time gate)."""
    ch = _changes()
    prior = ch[ch["date"] <= d]
    if prior.empty:
        return frozenset()
    return prior.iloc[-1]["members"]


def constituents_on(d: date | str) -> list[str]:
    """S&P500 members as of date d (latest snapshot with snapshot_date <= d)."""
    return sorted(constituents_set_on(pd.Timestamp(d).date()))


def all_symbols_in_range(start: date | str, end: date | str) -> list[str]:
    """Union of every ticker that was a member at any point in [start, end].

    This is the survivorship-bias-free backfill scope: include names that were
    later removed/delisted, because the strategy would have traded them then.
    """
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date()
    ch = _changes()
    # snapshots active during the window: those with date <= end, plus the one
    # active at `start` (last snapshot <= start).
    union: set[str] = set()
    active_at_start = ch[ch["date"] <= start]
    if not active_at_start.empty:
        union |= set(active_at_start.iloc[-1]["members"])
    for _, row in ch[(ch["date"] > start) & (ch["date"] <= end)].iterrows():
        union |= set(row["members"])
    return sorted(union)


def normalize_for_alpaca(symbol: str) -> str:
    """Map dataset ticker punctuation to Alpaca's convention (class shares use a
    dot in the dataset, e.g. BRK.B; Alpaca also uses a dot). Left as a single
    hook so backfill can adjust if a symbol 404s."""
    return symbol.strip().upper()
