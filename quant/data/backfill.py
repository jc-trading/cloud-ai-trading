"""Full-universe daily backfill (R0-4). Idempotent + resumable.

Per the R0 open-issue note, only DAILY bars are backfilled here (the R0-9 daily
simulator does not use 5m); the 5m full backfill is deferred to just before R1.

Partitioning on re-run makes it resumable and cheap:
  - fresh   (no manifest)          -> batched multi-symbol full-history fetch
  - stale   (manifest, old last_ts)-> per-symbol incremental sync (delisted names
                                       return ~nothing, so re-runs stay cheap)
  - current (manifest within N days)-> skipped
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from quant import config
from quant.data import fetch, manifest, store, universe


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
        last = manifest.get_last_ts(s, "1d")
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
            store.write_daily(sym, df)
            stored = store.read_daily(sym)
            fetch._upsert_from_store(sym, "1d", stored, now, session="regular")
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
                added = fetch.sync_daily(s, now=now, history_years=history_years)
                if store.daily_path(s).exists() and not store.read_daily(s).empty:
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


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="CAT daily full-universe backfill")
    ap.add_argument("--start", default="2016-01-01", help="window start for universe union")
    ap.add_argument("--end", default=None, help="window end (default: today)")
    ap.add_argument("--limit", type=int, default=None, help="cap symbols (smoke test)")
    args = ap.parse_args()

    import pandas as pd
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
