"""1min data for the minute engine — RTH day reads and the need-list backfill.

The 1m backtest and the nightly shortlist only need the symbol-days they will
trade plus INTRADAY_WARMUP_SESSIONS before each, so the store is filled from a
need list instead of whole-pool history. A pair counts as present once its file
is sourced from SIP, whatever its completeness status; an IEX file is left to
the EOD correction rather than overwritten here.

    python -m quant.data.intraday_data --need PATH [--rpm 60] [--minutes 20]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from quant import config
from quant.data import backfill, calendar, eod_correction, store

TIMEFRAME = "1min"


def completed_sessions_before(day: date, n: int) -> list[date]:
    out: list[date] = []
    d = day
    for _ in range(n):
        d = calendar.previous_session(d)
        out.append(d)
    return sorted(out)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(config.BAR_COLUMNS))


def day_bars(symbol: str, day: date) -> pd.DataFrame:
    """One session's RTH 1min bars [open, close) straight from its day file;
    ``attrs["provider"]`` is the file's stamped source (None when absent)."""
    path = store.bar_path(symbol, TIMEFRAME, day.isoformat())
    if not path.exists():
        df = _empty()
        df.attrs["provider"] = None
        return df
    df = store.normalize(pd.read_parquet(path))
    rth_open, rth_close = calendar.rth_bounds(day)
    df = df[(df["ts"] >= rth_open) & (df["ts"] < rth_close)].reset_index(drop=True)
    df.attrs["provider"] = store.parquet_provider(path)
    return df


def prior_rth(symbol: str, day: date, n: int) -> pd.DataFrame:
    frames = [f for f in (day_bars(symbol, d) for d in completed_sessions_before(day, n))
              if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else _empty()


def daily_adv(symbol: str, as_of: date) -> float | None:
    df = store.read_bars(symbol, "daily", as_of - timedelta(days=45), as_of)
    if len(df) < 20:
        return None
    tail = df.tail(20)
    return float((tail["close"].astype("float64") * tail["volume"].astype("float64")).mean())


def need_pairs(symbols_by_session: dict, warmup: int = config.INTRADAY_WARMUP_SESSIONS
               ) -> set[tuple[str, date]]:
    out: set[tuple[str, date]] = set()
    for session, symbols in symbols_by_session.items():
        days = [session, *completed_sessions_before(session, warmup)]
        out |= {(sym, d) for sym in symbols for d in days}
    return out


def missing_pairs(pairs, *, registry) -> dict:
    out: dict[str, list] = {"present": [], "missing": [], "iex_uncorrected": []}
    for sym, d in sorted(pairs):
        row = registry.get_row(sym.upper(), TIMEFRAME, d.isoformat())
        if row is not None and row.provider == config.PROVIDER_HISTORICAL:
            out["present"].append((sym, d))
        elif row is not None and row.provider == config.PROVIDER_REALTIME:
            out["iex_uncorrected"].append((sym, d))
        else:
            out["missing"].append((sym, d))
    return out


def store_1min_bytes() -> int:
    total = 0
    if not config.BARS_ROOT.exists():
        return 0
    with os.scandir(config.BARS_ROOT) as symbols:
        for sym in symbols:
            minute_dir = Path(sym.path) / TIMEFRAME
            if not sym.is_dir() or not minute_dir.is_dir():
                continue
            with os.scandir(minute_dir) as files:
                total += sum(f.stat().st_size for f in files if f.is_file())
    return total


def _free_gb() -> float:
    return shutil.disk_usage(config.BARS_ROOT if config.BARS_ROOT.exists()
                             else config.REPO_ROOT).free / 1e9


def backfill_sessions(pairs, *, provider=None, registry=None,
                      rpm: int = config.INTRADAY_BACKFILL_RPM, deadline_s: float = 1200,
                      now=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Fetch the missing (symbol, session) pairs from SIP, oldest session
    first, at most ``rpm`` requests a minute and for at most ``deadline_s``.
    Fail-closed before the first request on low disk or an over-budget 1min
    store. Idempotent: a re-run skips every pair already sourced from SIP."""
    stats: dict = {"done": False, "written": [], "skipped": 0, "empty": [],
                   "iex_uncorrected": [], "failed": [], "remaining": 0, "requests": 0,
                   "fail_closed": None}
    if _free_gb() < config.MIN_FREE_DISK_GB:
        stats["fail_closed"] = "disk"
        return stats
    if store_1min_bytes() / 1e9 > config.INTRADAY_STORE_MAX_GB:
        stats["fail_closed"] = "budget"
        return stats
    if registry is None:
        from quant.data import registry as registry_module

        registry = registry_module
    now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    todo = sorted(pairs, key=lambda p: (p[1], p[0]))
    started = clock()
    for i, (sym, day) in enumerate(todo):
        row = registry.get_row(sym.upper(), TIMEFRAME, day.isoformat())
        if row is not None and row.provider == config.PROVIDER_HISTORICAL:
            stats["skipped"] += 1
            continue
        if row is not None and row.provider == config.PROVIDER_REALTIME:
            stats["iex_uncorrected"].append([sym, day.isoformat()])
            continue
        open_ts, close_ts = calendar.session_bounds(day)
        if close_ts + pd.Timedelta(minutes=config.SIP_DELAY_MINUTES) > now_ts:
            stats["remaining"] += 1
            continue
        if clock() - started > deadline_s:
            stats["remaining"] += len(todo) - i
            break
        if provider is None:
            from quant.data.providers import get_historical

            provider = get_historical(config.PROVIDER_HISTORICAL)
        t0 = clock()
        stats["requests"] += 1
        try:
            df = backfill.session_only(
                eod_correction._fetch_day(provider, sym, TIMEFRAME, open_ts, close_ts, now_ts))
            if df.empty:
                stats["empty"].append([sym, day.isoformat()])
            else:
                store.write_bars(sym, TIMEFRAME, day.isoformat(), df,
                                 provider=provider.provider_key, registry=registry)
                stats["written"].append([sym, day.isoformat()])
        except Exception as e:
            stats["failed"].append(f"{sym} {day}: {type(e).__name__}: {e}"[:200])
        sleep(max(0.0, 60.0 / rpm - (clock() - t0)))
    stats["done"] = (stats["fail_closed"] is None and not stats["failed"]
                     and stats["remaining"] == 0)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill a 1min need list from SIP")
    ap.add_argument("--need", required=True, help="parquet with columns symbol, session")
    ap.add_argument("--rpm", type=int, default=config.INTRADAY_BACKFILL_RPM)
    ap.add_argument("--minutes", type=float, default=20)
    args = ap.parse_args(argv)

    from quant.data import registry
    from quant.data.providers import get_historical

    path = Path(args.need)
    need = pd.read_parquet(path)
    pairs = {(str(s), pd.Timestamp(d).date()) for s, d in zip(need["symbol"], need["session"])}
    stats = backfill_sessions(pairs, provider=get_historical(), registry=registry,
                              rpm=args.rpm, deadline_s=args.minutes * 60)
    print(f"STATS {json.dumps(stats, sort_keys=True)}", flush=True)
    ok = stats["fail_closed"] is None and not stats["failed"] and stats["remaining"] == 0
    out = path.with_name(f"{path.stem}.{'done' if ok else 'partial'}.json")
    out.write_text(json.dumps(stats, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
