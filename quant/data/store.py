"""Parquet store for RAW (unadjusted) bars — design §4.1–4.3.

Layout (proven optimal by the research doc §4.1–4.2):
  cat-data/bars/1d/{SYMBOL}.parquet            daily, per-symbol, full history
  cat-data/bars/5m/{SYMBOL}/{YYYY-MM}.parquet  intraday, per-symbol per-month

We store RAW so a file is append-only and frozen once written — a split never
rewrites history (read-time adjustment lives in corporate_actions.py). Writes
merge-and-dedupe on ts so re-fetching an overlapping window is idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant import config

# canonical column order stored on disk
_COLS = list(config.BAR_COLUMNS)  # ts, open, high, low, close, volume, vwap, trade_count


def daily_path(symbol: str) -> Path:
    return config.BARS_DIR / "1d" / f"{symbol.upper()}.parquet"


def intraday_path(symbol: str, year: int, month: int) -> Path:
    return config.BARS_DIR / "5m" / symbol.upper() / f"{year:04d}-{month:02d}.parquet"


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


def _merge_write(path: Path, df_new: pd.DataFrame) -> int:
    """Merge df_new into the parquet at path (dedupe on ts) and write. Returns
    total row count in the file after the merge."""
    df_new = normalize(df_new)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, df_new], ignore_index=True)
        combined = normalize(combined)
    else:
        combined = df_new
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(path, compression=config.PARQUET_COMPRESSION, index=False)
    return len(combined)


def write_daily(symbol: str, df: pd.DataFrame) -> int:
    return _merge_write(daily_path(symbol), df)


def read_daily(symbol: str) -> pd.DataFrame:
    path = daily_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    return pd.read_parquet(path)


def write_intraday(symbol: str, df: pd.DataFrame) -> int:
    """Split df by (year, month) of ts and merge into per-month files.
    Returns total rows written across all touched month files."""
    df = normalize(df)
    if df.empty:
        return 0
    total = 0
    ym = pd.DataFrame({"y": df["ts"].dt.year, "m": df["ts"].dt.month})
    for (year, month), chunk in df.groupby([ym["y"], ym["m"]]):
        total += _merge_write(intraday_path(symbol, int(year), int(month)), chunk)
    return total


def read_intraday(symbol: str, year: int, month: int) -> pd.DataFrame:
    path = intraday_path(symbol, year, month)
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    return pd.read_parquet(path)
