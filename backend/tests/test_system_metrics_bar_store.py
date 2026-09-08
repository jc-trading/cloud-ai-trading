"""The bar-store size gauge added to collect_system_metrics (方案 Phase 8)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.system import metrics as metrics_module
from app.modules.system.metrics import SystemMetrics


@pytest.fixture(autouse=True)
def clear_cache():
    metrics_module._bar_store_cache.clear()
    yield
    metrics_module._bar_store_cache.clear()


def test_sizes_every_file_under_the_bar_root(tmp_path):
    (tmp_path / "AAPL" / "1min").mkdir(parents=True)
    (tmp_path / "AAPL" / "1min" / "2026-09-04.parquet").write_bytes(b"x" * 100)
    (tmp_path / "AAPL" / "daily" / "2026").parent.mkdir(parents=True)
    (tmp_path / "AAPL" / "daily" / "2026.parquet").write_bytes(b"y" * 50)

    got = SystemMetrics.get_bar_store_metrics(str(tmp_path))

    assert got["bytes"] == 150
    assert got["files"] == 2


def test_missing_bar_root_is_zero_not_none(tmp_path):
    got = SystemMetrics.get_bar_store_metrics(str(tmp_path / "nope"))
    assert got == {"path": str(tmp_path / "nope"), "bytes": 0, "files": 0}


def test_measurement_is_cached_for_an_hour(tmp_path):
    (tmp_path / "a.parquet").write_bytes(b"x" * 10)
    first = SystemMetrics.get_bar_store_metrics(str(tmp_path))

    (tmp_path / "b.parquet").write_bytes(b"y" * 90)
    assert SystemMetrics.get_bar_store_metrics(str(tmp_path)) == first

    metrics_module._bar_store_cache.clear()
    assert SystemMetrics.get_bar_store_metrics(str(tmp_path))["bytes"] == 100
