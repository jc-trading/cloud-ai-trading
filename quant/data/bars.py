"""get_bars() — the ONE market-data entry point (架构铁律 ②).

Upper layers (engine, backtest, R1 live) call ONLY this. It hides where bars are
stored (Parquet now, could change), applies read-time corporate-action adjustment
(RAW on disk -> adjusted on read), filters trading session, and resamples the
higher intraday timeframes from 1min. Storage can change and nothing above moves.

    get_bars(symbol, timeframe, start, end, adjust="split_div", session="regular")

Stored timeframes are daily / 1hour / 1min (config.TIMEFRAMES); 5m/15m/30m are
resampled from 1min on read.
"""

from __future__ import annotations

import warnings
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config
from quant.data import corporate_actions, store

_ET = ZoneInfo("America/New_York")
# public timeframe vocabulary -> the stored timeframe it reads
_NATIVE_TF = {
    "1d": "daily", "1day": "daily", "d": "daily", "daily": "daily",
    "1h": "1hour", "60m": "1hour", "1hour": "1hour",
    "1m": "1min", "1min": "1min",
}
# resampled-from-1min intraday timeframes -> pandas offset alias
_RESAMPLE_TF = {"5m": "5min", "5min": "5min", "15m": "15min", "30m": "30min"}
_REGULAR_OPEN = (9, 30)   # ET
_REGULAR_CLOSE = (16, 0)  # ET (exclusive)

_slice = store.slice_range


_SUSPICIOUS_JUMP = 0.5   # |1-day close move| beyond this with zero actions -> warn


def _warn_if_unadjusted(symbol: str, df: pd.DataFrame, actions: pd.DataFrame) -> None:
    """Review B1 guard: adjustment requested but this symbol has ZERO cached
    corporate actions AND its series contains a split-sized single-day jump —
    almost certainly an unsynced action. Warn instead of silently returning RAW."""
    if actions is not None and not actions.empty:
        return
    closes = df["close"]
    if len(closes) < 2:
        return
    jumps = closes.pct_change().abs()
    if (jumps > _SUSPICIOUS_JUMP).any():
        worst = float(jumps.max())
        warnings.warn(
            f"{symbol}: adjust requested but no corporate actions are cached and the "
            f"daily series has a {worst:.0%} single-day jump — actions likely not "
            f"synced (run python -m quant.data.corporate_actions); prices are RAW",
            stacklevel=3)


def _regular_session_filter(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    et = df["ts"].dt.tz_convert(_ET)
    mins = et.dt.hour * 60 + et.dt.minute
    open_m = _REGULAR_OPEN[0] * 60 + _REGULAR_OPEN[1]
    close_m = _REGULAR_CLOSE[0] * 60 + _REGULAR_CLOSE[1]
    keep = (mins >= open_m) & (mins < close_m)
    return df[keep].reset_index(drop=True)


def _resample(df: pd.DataFrame, alias: str) -> pd.DataFrame:
    """Resample 1min -> higher intraday timeframe, aligned to the 09:30 ET session
    open (empty overnight buckets are dropped)."""
    if df.empty:
        return df
    idx = df.set_index(df["ts"].dt.tz_convert(_ET))
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "trade_count": "sum"}
    out = idx.resample(alias, label="left", closed="left",
                       origin="start_day", offset="9h30min").agg(agg)
    # volume-weighted vwap over the bucket
    vw = (idx["vwap"] * idx["volume"]).resample(
        alias, label="left", closed="left", origin="start_day", offset="9h30min").sum()
    out["vwap"] = (vw / out["volume"]).where(out["volume"] > 0)
    out = out.dropna(subset=["open"]).reset_index()
    out = out.rename(columns={"ts": "ts"})
    out["ts"] = out["ts"].dt.tz_convert("UTC")
    return store.normalize(out)


def get_bars(symbol: str, timeframe: str = "1d", start: date | str | None = None,
             end: date | str | None = None, *, adjust: str = "split_div",
             session: str = "regular") -> pd.DataFrame:
    """Return bars for symbol/timeframe over [start, end].

    adjust : 'split_div' (default) | 'split' | 'none'
    session: 'regular' (09:30-16:00 ET) | 'all' (include pre/post) — intraday only.

    Intraday timeframes are RAW only: corporate_actions.adjust derives each
    dividend factor from the last close before the ex-date *inside the frame it
    is given*, which a windowed intraday read cannot supply. Requesting an
    adjustment on an intraday timeframe raises rather than returning a number
    that silently depends on the window.
    """
    tf = timeframe.lower()
    stored_tf = _NATIVE_TF.get(tf)
    is_intraday = stored_tf != "daily"
    if is_intraday and adjust != "none":
        raise ValueError(
            f"adjust={adjust!r} is not supported for intraday timeframe "
            f"{timeframe!r} — intraday bars are served RAW; pass adjust='none'")

    if stored_tf == "daily":
        # the FULL history, then adjust, then window: the dividend factor comes
        # from the last close before each ex-date, so adjusting a frame that was
        # already truncated at `end` moves every price in it
        raw = store.read_bars(symbol, stored_tf, None, None)
    elif stored_tf is not None:
        raw = store.read_bars(symbol, stored_tf, start, end)
        if session == "regular":
            raw = _regular_session_filter(raw)
    elif tf in _RESAMPLE_TF:
        base = store.read_bars(symbol, "1min", start, end)
        if session == "regular":
            base = _regular_session_filter(base)
        raw = _resample(base, _RESAMPLE_TF[tf])
    else:
        raise ValueError(f"unsupported timeframe {timeframe!r}")

    if raw.empty:
        return raw

    # read-time adjustment (RAW on disk -> adjusted)
    if adjust != "none":
        actions = corporate_actions.load_actions(symbol)
        _warn_if_unadjusted(symbol, raw, actions)
        raw = corporate_actions.adjust(raw, actions, mode=adjust)

    return _slice(raw, start, end)
