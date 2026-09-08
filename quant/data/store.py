"""Parquet store for RAW (unadjusted) bars — 方案 Phase 2.

Layout, rooted at ``config.BARS_ROOT``:

  {SYMBOL}/daily/{YYYY}.parquet
  {SYMBOL}/1hour/{YYYY-MM}.parquet
  {SYMBOL}/1min/{YYYY-MM-DD}.parquet

The period key always comes from the ET calendar date of the bar, never UTC:
during EST the 16:00-20:00 ET after-hours session crosses UTC midnight and a
UTC bucket would split one trading day across two files.

Provenance 铁律: one file has exactly ONE provider. ``write_bars`` is the single
write entry point — it refuses to mix sources (``ProviderMismatch``), stamps the
provider into the Parquet key-value metadata, registers the file in
``market_data_files`` and holds a per-file lock across the read-merge-write so
concurrent writers (WS, backfill, EOD correction) cannot drop each other's rows.

Bars are stored RAW so a file is frozen once written — a split never rewrites
history (read-time adjustment lives in corporate_actions.py). Same-provider
writes merge-and-dedupe on ts, so re-fetching an overlapping window is idempotent.

Reads never touch PostgreSQL: ``read_bars`` derives candidate paths from the
calendar and opens only the files that exist, so a PG-free research run works.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config

# canonical column order stored on disk
_COLS = list(config.BAR_COLUMNS)  # ts, open, high, low, close, volume, vwap, trade_count
_ET = ZoneInfo("America/New_York")
_PERIOD_FMT = {"daily": "%Y", "1hour": "%Y-%m", "1min": "%Y-%m-%d"}
_PROVIDER_META_KEY = b"provider"

# registry module seam — resolved lazily so importing the store (the read path)
# never pulls psycopg in; tests inject a fake by assigning store._registry
_registry = None


class ProviderMismatch(RuntimeError):
    """A write would mix two data sources into one file (fail-closed)."""


def _registry_module(explicit=None):
    global _registry
    if explicit is not None:
        return explicit
    if _registry is None:
        from quant.data import registry

        _registry = registry
    return _registry


def _as_ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _check_timeframe(timeframe: str) -> str:
    if timeframe not in config.TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {timeframe!r}, "
                         f"expected one of {config.TIMEFRAMES}")
    return timeframe


def bar_dir(symbol: str, timeframe: str) -> Path:
    return config.BARS_ROOT / symbol.upper() / _check_timeframe(timeframe)


def bar_path(symbol: str, timeframe: str, period_key: str) -> Path:
    return bar_dir(symbol, timeframe) / f"{period_key}.parquet"


def relative_path(symbol: str, timeframe: str, period_key: str) -> str:
    """Registry path — relative to BARS_ROOT, because host and container mount
    the same tree at different absolute paths."""
    return f"{symbol.upper()}/{timeframe}/{period_key}.parquet"


def period_key(ts, timeframe: str) -> str:
    _check_timeframe(timeframe)
    return _as_ts(ts).tz_convert(_ET).strftime(_PERIOD_FMT[timeframe])


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce an Alpaca bars frame to the canonical schema.

    Accepts either the MultiIndex (symbol, timestamp) frame from alpaca-py or a
    flat frame that already has a 'ts' column. Returns columns in _COLS order,
    ts as a UTC tz-aware datetime, sorted and de-duplicated on ts.
    """
    df = df.copy()
    if "ts" not in df.columns:
        # from alpaca .df — timestamp is in the index (possibly MultiIndex)
        idx = df.index
        if isinstance(idx, pd.MultiIndex):
            ts = idx.get_level_values("timestamp")
        else:
            ts = idx
        df = df.reset_index(drop=True)
        df["ts"] = pd.to_datetime(ts, utc=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in _COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[_COLS]
    df = df.dropna(subset=["ts"]).drop_duplicates(subset=["ts"], keep="last")
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def split_periods(df: pd.DataFrame, timeframe: str) -> list[tuple[str, pd.DataFrame]]:
    """Split a frame into (period_key, chunk) pairs by the bars' ET calendar date."""
    df = normalize(df)
    if df.empty:
        return []
    keys = df["ts"].dt.tz_convert(_ET).dt.strftime(_PERIOD_FMT[_check_timeframe(timeframe)])
    return [(str(k), g.reset_index(drop=True)) for k, g in df.groupby(keys, sort=True)]


def checksum(symbol: str, timeframe: str, df: pd.DataFrame, provider: str) -> str:
    """sha256 over the NORMALIZED rows plus the provider — deliberately not over
    the Parquet bytes, which change with the zstd/pyarrow version and would make
    the idempotent-skip check never hit."""
    norm = normalize(df)
    h = hashlib.sha256()
    h.update(f"{symbol.upper()}|{timeframe}|{provider}\n".encode())
    h.update(norm.to_csv(index=False).encode())
    return h.hexdigest()


def parquet_provider(path: Path) -> str | None:
    """The provider stamped into a bar file's Parquet metadata (second line of
    defence behind the registry row)."""
    import pyarrow.parquet as pq

    meta = pq.read_schema(path).metadata or {}
    value = meta.get(_PROVIDER_META_KEY)
    return value.decode() if value is not None else None


def lock_path(symbol: str, timeframe: str, period_key: str) -> Path:
    """Lock files live in one flat dir, never beside the data: a `.lock` next to
    every parquet doubles the file count of the bar tree and interleaves with it.
    The leading underscore keeps the dir out of the ticker namespace."""
    return config.BARS_ROOT / "_locks" / f"{symbol.upper()}-{timeframe}-{period_key}.lock"


@contextmanager
def _file_lock(symbol: str, timeframe: str, period_key: str):
    path = lock_path(symbol, timeframe, period_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_atomic(path: Path, df: pd.DataFrame, provider: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[_PROVIDER_META_KEY] = provider.encode()
    table = table.replace_schema_metadata(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    try:
        pq.write_table(table, tmp, compression=config.PARQUET_COMPRESSION)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_bars(symbol: str, timeframe: str, period_key: str, df: pd.DataFrame, *,
               provider: str, replace: bool = False, registry=None) -> int:
    """Write one bar file and register it — the ONE write entry point.

    Same provider merges and de-dupes on ts (idempotent re-fetch); a different
    provider is refused unless ``replace=True``, which overwrites the whole file
    and re-stamps its provenance. The read-merge-write runs under a per-file
    lock and lands via a temp file + atomic rename, so a concurrent reader always
    sees a complete file and a concurrent writer never loses rows. Returns the
    row count of the file after the write.
    """
    _check_timeframe(timeframe)
    df_new = normalize(df)
    if df_new.empty:
        return 0
    reg = _registry_module(registry)
    path = bar_path(symbol, timeframe, period_key)

    with _file_lock(symbol, timeframe, period_key):
        row = reg.get_row(symbol.upper(), timeframe, period_key)
        # an unregistered file still carries its provenance in its own metadata;
        # trusting the missing row would silently merge two sources
        stamped = row.provider if row is not None else (
            parquet_provider(path) if path.exists() else None)
        if stamped is not None and stamped != provider and not replace:
            raise ProviderMismatch(
                f"{symbol.upper()} {timeframe} {period_key} is sourced from "
                f"{stamped!r}, refusing to merge {provider!r} — pass "
                f"replace=True to re-source the whole file")

        if replace or not path.exists():
            combined = df_new
        else:
            combined = normalize(pd.concat([pd.read_parquet(path), df_new],
                                           ignore_index=True))
        _write_atomic(path, combined, provider)

        reg.upsert(
            symbol.upper(), timeframe, period_key,
            path=relative_path(symbol, timeframe, period_key),
            provider=provider,
            row_count=len(combined),
            first_ts=combined["ts"].min().to_pydatetime(),
            last_ts=combined["ts"].max().to_pydatetime(),
            checksum=checksum(symbol, timeframe, combined, provider),
            fetched_at=datetime.now(timezone.utc),
        )
    return len(combined)


def write_frame(symbol: str, timeframe: str, df: pd.DataFrame, *, provider: str,
                replace: bool = False, registry=None) -> int:
    """Split a multi-period frame by period key and write each file. Returns the
    total row count across the touched files."""
    return sum(
        write_bars(symbol, timeframe, key, chunk, provider=provider,
                   replace=replace, registry=registry)
        for key, chunk in split_periods(df, timeframe)
    )


def slice_range(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Filter to [start, end]. A date-like `end` means 'through that day': daily
    bars are anchored at 04:00/05:00Z, so a midnight-UTC <= would silently
    exclude the end day itself (review F9)."""
    if df.empty:
        return df
    if start is not None:
        df = df[df["ts"] >= _as_ts(start)]
    if end is not None:
        end_ts = _as_ts(end)
        if end_ts == end_ts.normalize():
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        df = df[df["ts"] <= end_ts]
    return df.reset_index(drop=True)


def _period_keys(timeframe: str, start, end, directory: Path) -> list[str]:
    """Candidate period keys for [start, end], from the calendar — never a
    directory walk (100 symbols × 5 years of 1min is 126k files, and pyarrow
    dataset discovery over that is the bottleneck)."""
    if start is None:
        # unbounded history: one symbol's own timeframe dir holds ~10 files for
        # daily, far cheaper to list than to enumerate every year since 1792
        keys = sorted(p.stem for p in directory.glob("*.parquet")) if directory.exists() else []
        if end is not None:
            hi_key = period_key(_as_ts(end), timeframe)
            keys = [k for k in keys if k <= hi_key]
        return keys

    lo = _as_ts(start).tz_convert(_ET)
    hi = (_as_ts(end) if end is not None else pd.Timestamp.now(tz="UTC")).tz_convert(_ET)
    if hi < lo:
        return []
    if timeframe == "daily":
        return [f"{y:04d}" for y in range(lo.year, hi.year + 1)]
    # a UTC-expressed window edge lands mid-session in ET, so pad a day either
    # side; slice_range drops whatever the padding pulls in
    lo, hi = lo - pd.Timedelta(days=1), hi + pd.Timedelta(days=1)
    if timeframe == "1hour":
        return [str(p) for p in pd.period_range(lo, hi, freq="M")]

    from quant.data import calendar

    return [d.isoformat() for d in calendar.sessions_in_range(lo.date(), hi.date())]


def read_bars(symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame:
    """Concat the bar files covering [start, end]. Filesystem + calendar only —
    no PostgreSQL, no directory scan."""
    _check_timeframe(timeframe)
    directory = bar_dir(symbol, timeframe)
    frames = []
    for key in _period_keys(timeframe, start, end, directory):
        path = directory / f"{key}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=_COLS)
    return slice_range(normalize(pd.concat(frames, ignore_index=True)), start, end)
