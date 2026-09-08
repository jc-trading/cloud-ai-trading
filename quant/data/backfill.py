"""Bar backfill — daily full-universe (R0-4) and intraday 1hour/1min (方案 Phase 6).
Both are idempotent + resumable.

Partitioning on re-run makes it resumable and cheap:
  - fresh   (no registry row)       -> batched multi-symbol full-history fetch
  - stale   (registry, old last_ts) -> per-symbol incremental sync (delisted names
                                       return ~nothing, so re-runs stay cheap)
  - current (registry within N days)-> skipped

The intraday half fetches in RANGE requests (a year of bars per call, which
alpaca-py pages through internally at 10,000 bars a page) and splits the frame
by period key in memory — one HTTP request per day would be ~38k requests for
30 symbols x 5 years. It resumes on the gaps a symbol actually has, head and
tail, not on the high-water alone. Run 1hour before 1min.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config
from quant.data import fetch, registry, store, universe
from quant.data.providers import get_historical
from quant.data.stream import SESSION_CLOSE_ET, SESSION_OPEN_ET

_ET = ZoneInfo("America/New_York")
# one range request per year of history; alpaca-py pages inside it, so the
# request count scales with pages (~121 for a symbol-year of 1min), never days
WINDOW_DAYS = 365
_INTRADAY_PERIOD = {"1hour": timedelta(hours=1), "1min": timedelta(minutes=1)}
_OPEN_MINUTE = SESSION_OPEN_ET.hour * 60 + SESSION_OPEN_ET.minute
_CLOSE_MINUTE = SESSION_CLOSE_ET.hour * 60 + SESSION_CLOSE_ET.minute


@dataclass
class BackfillStats:
    total: int = 0
    fresh: int = 0
    stale: int = 0
    current: int = 0
    stored: int = 0
    recovered: int = 0                                # rescued by the single-symbol retry
    empty: list[str] = field(default_factory=list)   # symbols Alpaca returned nothing for


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def run_daily_backfill(symbols: list[str], *, now: datetime | None = None,
                       batch_size: int = 100, skip_fresh_days: int = 4,
                       history_years: int = config.DAILY_HISTORY_YEARS,
                       progress=print) -> BackfillStats:
    now = now or datetime.now(timezone.utc)
    st = BackfillStats(total=len(symbols))
    full_start = now - timedelta(days=365 * history_years + 7)

    fresh, stale = [], []
    for s in symbols:
        last = registry.max_last_ts(s.upper(), "daily")
        if last is None:
            fresh.append(s)
        elif (now - last).days > skip_fresh_days:
            stale.append(s)
        else:
            st.current += 1
    st.fresh, st.stale = len(fresh), len(stale)

    # fresh: batched multi-symbol
    for bi, batch in enumerate(_chunks(fresh, batch_size)):
        result = fetch.fetch_daily_multi(batch, full_start, now=now)
        for sym in batch:
            df = result.get(sym.upper())
            if df is None or df.empty:
                st.empty.append(sym)
                continue
            store.write_frame(sym, "daily", df, provider=config.PROVIDER_HISTORICAL)
            st.stored += 1
        progress(f"  fresh batch {bi + 1}: {len(batch)} symbols, "
                 f"{st.stored} stored so far, {len(st.empty)} empty")

    # stale: per-symbol incremental (cheap; delisted names return ~nothing)
    for s in stale:
        try:
            fetch.sync_daily(s, now=now, history_years=history_years)
            st.stored += 1
        except Exception as e:  # network hiccup on one symbol shouldn't kill the run
            progress(f"  stale {s}: {type(e).__name__} {e}")
            st.empty.append(s)

    # Recovery pass: large multi-symbol batches silently drop some symbols
    # (alpaca-py truncates paginated responses — a symbol whose data ends
    # mid-window can be missing from a 100-symbol batch but present in a 1-symbol
    # request). Retry every "empty" individually; genuinely delisted names stay
    # empty, truncation victims are recovered. See R0-4 investigation.
    if st.empty:
        progress(f"  recovery: retrying {len(st.empty)} empty symbols individually")
        still_empty: list[str] = []
        for s in st.empty:
            try:
                fetch.sync_daily(s, now=now, history_years=history_years)
                if not store.read_bars(s, "daily").empty:
                    st.stored += 1
                    st.recovered += 1
                else:
                    still_empty.append(s)
            except Exception as e:
                progress(f"  recovery {s}: {type(e).__name__} {e}")
                still_empty.append(s)
        st.empty = still_empty
        progress(f"  recovery done: {st.recovered} recovered, {len(st.empty)} truly empty")

    return st



# --------------------------------------------------------------------------
# Intraday (1hour / 1min) — 方案 Phase 6
# --------------------------------------------------------------------------


@dataclass
class SymbolBackfill:
    symbol: str
    kind: str = "fresh"                              # fresh | stale
    rows: int = 0                                    # rows this run ADDED to the files
    files: int = 0
    requests: int = 0
    seconds: float = 0.0
    error: str | None = None


@dataclass
class IntradayStats:
    timeframe: str
    total: int = 0
    fresh: int = 0
    stale: int = 0
    current: int = 0
    stored: int = 0
    recovered: int = 0
    requests: int = 0
    results: list[SymbolBackfill] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(r.rows for r in self.results)

    @property
    def files(self) -> int:
        return sum(r.files for r in self.results)


def resolve_intraday_symbols(registry) -> list[str]:
    """The stream's subscription set — the 对照账户's open positions, then the
    enabled ``market_stream_symbols`` rows, then its owner's stock watchlist.
    No 30-symbol cap here: that is a per-stream-connection limit, REST has none."""
    held: list[str] = []
    watch: list[str] = []
    account = registry.system_account()
    if account is not None:
        held = registry.open_position_symbols(account.account_id)
        watch = registry.watchlist_symbols(account.user_id)
    configured = [row.symbol.upper() for row in registry.stream_symbols()]

    ordered: list[str] = []
    for symbol in (*held, *configured, *watch):
        symbol = symbol.upper()
        if symbol not in ordered:
            ordered.append(symbol)
    return ordered


def _windows(start: datetime, end: datetime):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=WINDOW_DAYS), end)
        yield cur, nxt
        cur = nxt


def session_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the 04:00-20:00 ET window (拍板 2026-09-07) — the same session the WS
    writer and the EOD correction use, so a backfilled day and a corrected day
    hold the same bars. Compared in ET local time, which is DST-correct."""
    if df.empty:
        return df
    et = df["ts"].dt.tz_convert(_ET)
    mins = et.dt.hour * 60 + et.dt.minute
    keep = (mins >= _OPEN_MINUTE) & (mins < _CLOSE_MINUTE)
    return df[keep].reset_index(drop=True)


def _earliest_stored(symbol: str, timeframe: str) -> pd.Timestamp | None:
    """Start of the earliest stored period for a symbol, from its own directory
    (one listdir, the same idiom store._period_keys uses for an unbounded read)."""
    directory = store.bar_dir(symbol, timeframe)
    if not directory.exists():
        return None
    keys = sorted(path.stem for path in directory.glob("*.parquet"))
    return pd.Timestamp(keys[0], tz=_ET).tz_convert("UTC") if keys else None


def missing_ranges(symbol: str, timeframe: str, full_start: datetime,
                   last: datetime | None, end: datetime,
                   period: timedelta) -> list[tuple[datetime, datetime]]:
    """The windows a symbol is still missing: the HEAD gap below its earliest
    stored file plus the TAIL above the registry high-water.

    The high-water alone is not a resume point here — the WS writer and the EOD
    correction seed today's file long before any history exists, so a naive
    ``start = max(last_ts)`` would declare five years of history already done.
    An empty list means the symbol is current.
    """
    if last is None:
        return [(full_start, end)]
    earliest = _earliest_stored(symbol, timeframe)
    if earliest is None:
        return [(full_start, end)]
    ranges: list[tuple[datetime, datetime]] = []
    if earliest > full_start:
        ranges.append((full_start, min(earliest, end)))
    if last < end - period:
        ranges.append((max(last - period, full_start), end))
    return ranges


def _backfill_symbol(symbol: str, timeframe: str,
                     ranges: list[tuple[datetime, datetime]], *, provider, registry,
                     now: datetime, kind: str) -> SymbolBackfill:
    res = SymbolBackfill(symbol=symbol, kind=kind)
    started = time.monotonic()
    keys: set[str] = set()
    for range_start, range_end in ranges:
        for win_start, win_end in _windows(range_start, range_end):
            df = provider.fetch_bars(symbol, timeframe, win_start, win_end, now=now)
            res.requests += 1
            df = session_only(store.normalize(df))
            if df.empty:
                continue
            for key, chunk in store.split_periods(df, timeframe):
                row = registry.get_row(symbol, timeframe, key)
                before = row.row_count if row is not None else 0
                res.rows += store.write_bars(
                    symbol, timeframe, key, chunk, provider=provider.provider_key,
                    registry=registry) - before
                keys.add(key)
    res.files = len(keys)
    res.seconds = time.monotonic() - started
    return res


def run_intraday_backfill(symbols: list[str], timeframe: str,
                          years: int = config.INTRADAY_HISTORY_YEARS, *,
                          provider=None, registry=None, now: datetime | None = None,
                          progress=print) -> IntradayStats:
    """Backfill 1hour / 1min history for a symbol set, resumably.

    Each symbol fetches only the windows it is missing (see ``missing_ranges``)
    in year-long range requests, split by period key in memory and written one
    file at a time; the store dedupes on ts, so overlap is free and a re-run
    writes nothing. A symbol that fails is skipped, retried once in a recovery
    pass and then reported — one bad name never aborts the batch.
    """
    if timeframe not in _INTRADAY_PERIOD:
        raise ValueError(f"unsupported intraday timeframe {timeframe!r}, "
                         f"expected one of {tuple(_INTRADAY_PERIOD)}")
    now = now or datetime.now(timezone.utc)
    if registry is None:
        from quant.data import registry as registry_module

        registry = registry_module
    provider = provider or get_historical()
    # free-tier SIP is legal only for data at least 15 minutes old
    end = now - timedelta(minutes=config.SIP_DELAY_MINUTES)
    full_start = now - timedelta(days=365 * years)
    period = _INTRADAY_PERIOD[timeframe]

    st = IntradayStats(timeframe=timeframe, total=len(symbols))
    pending: list[tuple[str, list[tuple[datetime, datetime]], str]] = []
    for raw in symbols:
        symbol = raw.upper()
        last = registry.max_last_ts(symbol, timeframe)
        ranges = missing_ranges(symbol, timeframe, full_start, last, end, period)
        if not ranges:
            st.current += 1
            continue
        if last is None:
            st.fresh += 1
        else:
            st.stale += 1
        pending.append((symbol, ranges, "fresh" if last is None else "stale"))
    progress(f"{timeframe}: {st.total} symbols — {st.fresh} fresh, {st.stale} stale, "
             f"{st.current} current (skipped)")

    for i, (symbol, ranges, kind) in enumerate(pending, 1):
        res = _run_one(symbol, timeframe, ranges, provider=provider,
                       registry=registry, now=now, kind=kind)
        st.results.append(res)
        st.requests += res.requests
        if res.error:
            progress(f"  [{i}/{len(pending)}] {symbol} {kind}: FAILED {res.error}")
        else:
            st.stored += 1
            progress(f"  [{i}/{len(pending)}] {symbol} {kind}: {res.rows} new rows, "
                     f"{res.files} files, {res.requests} req, {res.seconds:.1f}s")

    # Recovery pass: a transient 429/5xx on one window loses that symbol only.
    # The retry re-reads the registry high-water, so whatever the failed run did
    # manage to write is kept and the retry resumes from it.
    failed = [r for r in st.results if r.error]
    if failed:
        progress(f"  recovery: retrying {len(failed)} failed symbols")
        for res in failed:
            last = registry.max_last_ts(res.symbol, timeframe)
            ranges = missing_ranges(res.symbol, timeframe, full_start, last, end, period)
            retry = _run_one(res.symbol, timeframe, ranges, provider=provider,
                             registry=registry, now=now, kind=res.kind)
            res.rows += retry.rows
            res.files = max(res.files, retry.files)
            res.requests += retry.requests
            res.seconds += retry.seconds
            res.error = retry.error
            st.requests += retry.requests
            if res.error is None:
                st.stored += 1
                st.recovered += 1
        progress(f"  recovery done: {st.recovered} recovered")

    st.failed = [r.symbol for r in st.results if r.error]
    return st


def _run_one(symbol: str, timeframe: str, ranges: list[tuple[datetime, datetime]], *,
             provider, registry, now: datetime, kind: str) -> SymbolBackfill:
    try:
        return _backfill_symbol(symbol, timeframe, ranges, provider=provider,
                                registry=registry, now=now, kind=kind)
    except Exception as exc:
        return SymbolBackfill(symbol=symbol, kind=kind,
                              error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="CAT bar backfill (daily / intraday)")
    ap.add_argument("--intraday", choices=("1hour", "1min"), default=None,
                    help="backfill this intraday timeframe instead of daily")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset (default: the stream's subscription set)")
    ap.add_argument("--years", type=int, default=config.INTRADAY_HISTORY_YEARS,
                    help="intraday history depth in years")
    ap.add_argument("--start", default="2016-01-01", help="window start for universe union")
    ap.add_argument("--end", default=None, help="window end (default: today)")
    ap.add_argument("--limit", type=int, default=None, help="cap symbols (smoke test)")
    args = ap.parse_args()

    if args.intraday:
        import sys

        syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
                if args.symbols else resolve_intraday_symbols(registry))
        print(f"intraday backfill {args.intraday}: {len(syms)} symbols, "
              f"{args.years}y — {','.join(syms)}")
        stats = run_intraday_backfill(syms, args.intraday, args.years)
        print(f"DONE: stored={stats.stored} rows={stats.rows} files={stats.files} "
              f"requests={stats.requests} fresh={stats.fresh} stale={stats.stale} "
              f"current={stats.current} recovered={stats.recovered} "
              f"failed={len(stats.failed)}")
        for r in stats.results:
            print(f"  {r.symbol:<6} {r.kind:<5} rows={r.rows:<8} files={r.files:<5} "
                  f"req={r.requests:<4} {r.seconds:.1f}s {r.error or ''}")
        sys.exit(1 if stats.failed else 0)

    end = args.end or pd.Timestamp.now("UTC").date().isoformat()
    universe.download_constituents()
    syms = universe.all_symbols_in_range(args.start, end)
    if args.limit:
        syms = syms[:args.limit]
    print(f"backfilling {len(syms)} symbols (union {args.start}..{end})")
    stats = run_daily_backfill(syms)
    print(f"DONE: stored={stats.stored} current={stats.current} fresh={stats.fresh} "
          f"stale={stats.stale} empty={len(stats.empty)}")
    if stats.empty:
        print(f"  empty (no Alpaca data — likely delisted/renamed): {stats.empty[:30]}"
              + (" ..." if len(stats.empty) > 30 else ""))
