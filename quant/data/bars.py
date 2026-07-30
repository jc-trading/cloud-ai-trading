"""get_bars() — the ONE market-data entry point (架构铁律 ②).

Upper layers (engine, backtest, R1 live) call ONLY this. It hides where bars are
stored (Parquet now, could change), applies read-time corporate-action adjustment
(RAW on disk -> adjusted on read), filters trading session, and resamples higher
intraday timeframes from 5m. Storage can change and nothing above moves.

    get_bars(symbol, timeframe, start, end, adjust="split_div", session="regular")
"""

from __future__ import annotations

import warnings
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config
from quant.data import corporate_actions, store

_ET = ZoneInfo("America/New_York")
_DAILY_TF = {"1d", "1day", "d", "daily"}
_BASE_INTRADAY_TF = {"5m", "5min"}
# resampled-from-5m intraday timeframes -> pandas offset alias
_RESAMPLE_TF = {"15m": "15min", "30m": "30min", "1h": "60min", "60m": "60min"}
_REGULAR_OPEN = (9, 30)   # ET
_REGULAR_CLOSE = (16, 0)  # ET (exclusive)


def _slice(df: pd.DataFrame, start, end) -> pd.DataFrame:
    if df.empty:
        return df
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        end_ts = pd.Timestamp(end, tz="UTC")
        if end_ts == end_ts.normalize():
            # date-like end (no time part) means "through that day" — daily bars
            # are anchored at 04:00/05:00Z, so midnight-UTC <= would silently
            # exclude the end day itself (review F9)
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        df = df[df["ts"] <= end_ts]
    return df.reset_index(drop=True)


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


def _read_intraday_range(symbol: str, start, end) -> pd.DataFrame:
    """Concat the per-month 5m parquet files spanning [start, end] (ET months)."""
    lo = pd.Timestamp(start).tz_localize(None) if start is not None else pd.Timestamp("2000-01-01")
    hi = pd.Timestamp(end).tz_localize(None) if end is not None else pd.Timestamp.now()
    frames = []
    period = pd.Period(lo, freq="M")
    last = pd.Period(hi, freq="M")
    while period <= last:
        df = store.read_intraday(symbol, period.year, period.month)
        if not df.empty:
            frames.append(df)
        period += 1
    if not frames:
        return pd.DataFrame(columns=list(config.BAR_COLUMNS))
    return store.normalize(pd.concat(frames, ignore_index=True))


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
    """Resample 5m -> higher intraday timeframe, aligned to the 09:30 ET session
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
    """
    tf = timeframe.lower()
    is_intraday = tf not in _DAILY_TF

    if tf in _DAILY_TF:
        raw = store.read_daily(symbol)
    elif tf in _BASE_INTRADAY_TF:
        raw = _read_intraday_range(symbol, start, end)
    elif tf in _RESAMPLE_TF:
        base = _read_intraday_range(symbol, start, end)
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
        if not is_intraday:
            _warn_if_unadjusted(symbol, raw, actions)
        raw = corporate_actions.adjust(raw, actions, mode=adjust)

    # session filter for base intraday (resampled path already filtered pre-resample)
    if is_intraday and tf in _BASE_INTRADAY_TF and session == "regular":
        raw = _regular_session_filter(raw)

    return _slice(raw, start, end)
