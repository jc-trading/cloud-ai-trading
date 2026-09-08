"""Phase 4 migration: synthetic old-layout files must land in the new tree, pass
their own validation, and re-run to an identical result (idempotent + resumable)."""

from __future__ import annotations

import pandas as pd
import pytest

from quant import config
from quant.data import migrate_layout, store

_SIP = config.PROVIDER_HISTORICAL


def _daily(days) -> pd.DataFrame:
    ts = pd.DatetimeIndex(
        [pd.Timestamp(f"{d} 00:00", tz="America/New_York") for d in days]
    ).tz_convert("UTC")
    n = len(days)
    return pd.DataFrame({
        "ts": ts, "open": [10.0 + i for i in range(n)], "high": [11.0 + i for i in range(n)],
        "low": [9.0 + i for i in range(n)], "close": [10.5 + i for i in range(n)],
        "volume": [1000.0 * (i + 1) for i in range(n)], "vwap": [10.0 + i for i in range(n)],
        "trade_count": [7.0] * n,
    })


@pytest.fixture
def old_tree(monkeypatch, tmp_path, tmp_store):
    old_dir = tmp_path / "cat-data" / "bars" / "1d"
    old_dir.mkdir(parents=True)
    monkeypatch.setattr(migrate_layout, "OLD_DAILY_DIR", old_dir)
    # AAA spans a year boundary, BBB sits inside one year
    _daily(["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05"]).to_parquet(
        old_dir / "AAA.parquet", index=False)
    _daily(["2026-02-02", "2026-02-03"]).to_parquet(old_dir / "BBB.parquet", index=False)
    return tmp_store


def _tree_snapshot() -> dict[str, bytes]:
    return {str(p.relative_to(config.BARS_ROOT)): p.read_bytes()
            for p in sorted(config.BARS_ROOT.rglob("*.parquet"))}


def test_migration_splits_by_et_year_and_validates(old_tree):
    stats = migrate_layout.run(progress=lambda _: None)

    assert stats.total == 2 and stats.migrated == 2 and stats.skipped == 0
    assert stats.files_written == 3 and stats.rows == 6
    assert stats.invalid == [] and stats.empty == []

    assert sorted(p.stem for p in store.bar_dir("AAA", "daily").glob("*.parquet")) \
        == ["2025", "2026"]
    assert len(store.read_bars("AAA", "daily", "2025-01-01", "2026-12-31")) == 4
    assert old_tree.get_row("AAA", "daily", "2025").row_count == 2
    assert old_tree.get_row("AAA", "daily", "2026").row_count == 2
    assert old_tree.get_row("BBB", "daily", "2026").provider == _SIP
    assert store.parquet_provider(store.bar_path("BBB", "daily", "2026")) == _SIP


def test_second_run_is_a_no_op(old_tree):
    migrate_layout.run(progress=lambda _: None)
    before = _tree_snapshot()
    rows_before = dict(old_tree.rows)

    stats = migrate_layout.run(progress=lambda _: None)

    assert stats.migrated == 0 and stats.skipped == 2 and stats.files_written == 0
    assert stats.invalid == []
    assert _tree_snapshot() == before
    assert old_tree.rows == rows_before


def test_resumes_after_a_partial_run(old_tree):
    migrate_layout.run(["AAA"], progress=lambda _: None)
    stats = migrate_layout.run(progress=lambda _: None)

    assert stats.skipped == 1 and stats.migrated == 1      # AAA kept, BBB written
    assert stats.invalid == []


def test_validation_reports_a_missing_year(old_tree, monkeypatch):
    migrate_layout.run(progress=lambda _: None)
    store.bar_path("AAA", "daily", "2025").unlink()
    old_tree.delete("AAA", "daily", "2025")

    stats = migrate_layout.run(["AAA"], progress=lambda _: None)
    assert stats.invalid == []                             # the gap was refilled

    # a year that is registered but whose rows vanished must NOT pass silently
    old_tree.rows[("AAA", "daily", "2025")] = old_tree.rows[
        ("AAA", "daily", "2025")].__class__(
        **{**old_tree.rows[("AAA", "daily", "2025")].__dict__, "row_count": 1})
    stats = migrate_layout.run(["AAA"], progress=lambda _: None)
    assert any("row_count sum" in p for p in stats.invalid)
