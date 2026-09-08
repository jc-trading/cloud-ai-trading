"""One-time layout migration (方案 Phase 4): the flat per-symbol daily Parquet
files move into the provenance-tracked per-period tree.

    cat-data/bars/1d/{SYM}.parquet  ->  stock-market-data/{SYM}/daily/{YYYY}.parquet

Idempotent and resumable: a year whose registry row already carries the same
normalized checksum is skipped, so a re-run after an interruption only writes
what is missing. Every symbol is then validated against its OLD file — row
totals, first/last ts and the exact set of years — and any mismatch aborts the
run with the offending list rather than leaving a half-migrated tree.

The source files are NOT deleted: they stay as the rollback path until the new
tree has run a full week of nightly signal cycles.

    docker compose exec -T backend python -m quant.data.migrate_layout
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from quant import config
from quant.data import store

OLD_DAILY_DIR = config.DATA_ROOT / "bars" / "1d"
_TF = "daily"


@dataclass
class MigrateStats:
    total: int = 0
    migrated: int = 0                                 # symbols with >=1 file written
    skipped: int = 0                                  # symbols already fully migrated
    files_written: int = 0
    rows: int = 0
    empty: list[str] = field(default_factory=list)    # old file had no rows
    invalid: list[str] = field(default_factory=list)  # validation failures


def old_symbols() -> list[str]:
    return sorted(p.stem.upper() for p in OLD_DAILY_DIR.glob("*.parquet"))


def _validate(symbol: str, old: pd.DataFrame, expected_keys: list[str], reg) -> list[str]:
    """Compare the migrated tree against the source file. Checking the year SET
    (not just totals) is what catches one year missing while another gained the
    same number of rows."""
    problems: list[str] = []
    rows = {key: reg.get_row(symbol, _TF, key) for key in expected_keys}
    missing = [k for k, r in rows.items() if r is None]
    if missing:
        return [f"{symbol}: no registry row for {missing}"]

    on_disk = sorted(p.stem for p in store.bar_dir(symbol, _TF).glob("*.parquet"))
    if on_disk != expected_keys:
        problems.append(f"{symbol}: year files {on_disk} != expected {expected_keys}")

    total = sum(r.row_count for r in rows.values())
    if total != len(old):
        problems.append(f"{symbol}: row_count sum {total} != old file {len(old)}")

    last = max(pd.Timestamp(r.last_ts) for r in rows.values())
    first = min(pd.Timestamp(r.first_ts) for r in rows.values())
    if last != old["ts"].max():
        problems.append(f"{symbol}: last_ts {last} != old {old['ts'].max()}")
    if first != old["ts"].min():
        problems.append(f"{symbol}: first_ts {first} != old {old['ts'].min()}")
    return problems


def migrate_symbol(symbol: str, path: Path, *, provider: str, registry=None,
                   stats: MigrateStats) -> None:
    old = store.normalize(pd.read_parquet(path))
    if old.empty:
        stats.empty.append(symbol)
        return

    reg = store._registry_module(registry)
    written = 0
    chunks = store.split_periods(old, _TF)
    for key, chunk in chunks:
        row = reg.get_row(symbol, _TF, key)
        if (row is not None and row.provider == provider
                and row.checksum == store.checksum(symbol, _TF, chunk, provider)
                and store.bar_path(symbol, _TF, key).exists()):
            continue
        store.write_bars(symbol, _TF, key, chunk, provider=provider, registry=registry)
        written += 1

    stats.files_written += written
    stats.rows += len(old)
    if written:
        stats.migrated += 1
    else:
        stats.skipped += 1

    stats.invalid += _validate(symbol, old, [key for key, _ in chunks], reg)


def run(symbols: list[str] | None = None, *,
        provider: str = config.PROVIDER_HISTORICAL, registry=None,
        progress=print) -> MigrateStats:
    symbols = symbols if symbols is not None else old_symbols()
    st = MigrateStats(total=len(symbols))
    for i, symbol in enumerate(symbols, start=1):
        migrate_symbol(symbol, OLD_DAILY_DIR / f"{symbol}.parquet",
                       provider=provider, registry=registry, stats=st)
        if i % 50 == 0 or i == st.total:
            progress(f"  {i}/{st.total} symbols: {st.migrated} migrated, "
                     f"{st.skipped} already current, {st.files_written} files, "
                     f"{st.rows} rows, {len(st.invalid)} problems")
    return st


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="CAT daily bar layout migration")
    ap.add_argument("--symbols", default=None, help="comma-separated subset (default: all)")
    ap.add_argument("--limit", type=int, default=None, help="cap symbols (smoke test)")
    ap.add_argument("--provider", default=config.PROVIDER_HISTORICAL,
                    help="provenance key stamped on every migrated file")
    args = ap.parse_args()

    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else old_symbols())
    if args.limit:
        syms = syms[:args.limit]
    print(f"migrating {len(syms)} symbols: {OLD_DAILY_DIR} -> {config.BARS_ROOT} "
          f"(provider={args.provider})")
    stats = run(syms, provider=args.provider)
    print(f"DONE: migrated={stats.migrated} already_current={stats.skipped} "
          f"files={stats.files_written} rows={stats.rows} empty={len(stats.empty)}")
    if stats.empty:
        print(f"  empty source files: {stats.empty}")
    if stats.invalid:
        print(f"VALIDATION FAILED ({len(stats.invalid)}):")
        for problem in stats.invalid:
            print(f"  {problem}")
        sys.exit(1)
    print("validation OK: row totals, first/last ts and year sets match the old files")
