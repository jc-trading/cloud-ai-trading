"""Shared fixtures: an in-memory stand-in for the PostgreSQL file registry.

``write_bars`` registers every file it writes, so the store tests would otherwise
need a live PostgreSQL. The fake mirrors ``quant.data.registry``'s surface and is
injected through the module seams (store._registry, fetch/backfill.registry), so
the unit tests run on the host venv with no database at all.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from quant import config
from quant.data import backfill, fetch, store
from quant.data.registry import FileRow


class FakeRegistry:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], FileRow] = {}

    def get_row(self, symbol: str, timeframe: str, period_key: str) -> FileRow | None:
        return self.rows.get((symbol.upper(), timeframe, period_key))

    def upsert(self, symbol: str, timeframe: str, period_key: str, *, path: str,
               provider: str, row_count: int, first_ts: datetime, last_ts: datetime,
               checksum: str, fetched_at: datetime, status: str = "ok",
               meta: dict | None = None) -> None:
        self.rows[(symbol.upper(), timeframe, period_key)] = FileRow(
            symbol=symbol.upper(), timeframe=timeframe, period_key=period_key,
            path=path, provider=provider, row_count=row_count, first_ts=first_ts,
            last_ts=last_ts, checksum=checksum, fetched_at=fetched_at,
            status=status, meta=meta)

    def delete(self, symbol: str, timeframe: str, period_key: str) -> int:
        return int(self.rows.pop((symbol.upper(), timeframe, period_key), None) is not None)

    def update_status(self, symbol: str, timeframe: str, period_key: str, *,
                      status: str, meta: dict | None = None) -> int:
        key = (symbol.upper(), timeframe, period_key)
        row = self.rows.get(key)
        if row is None:
            return 0
        merged = row.meta if meta is None else {**(row.meta or {}), **meta}
        self.rows[key] = replace(row, status=status, meta=merged)
        return 1

    def symbols_with_file(self, timeframe: str, period_key: str) -> list[str]:
        return sorted(k[0] for k in self.rows
                      if k[1] == timeframe and k[2] == period_key)

    def max_last_ts(self, symbol: str, timeframe: str) -> datetime | None:
        stamps = [r.last_ts for k, r in self.rows.items()
                  if k[0] == symbol.upper() and k[1] == timeframe and r.last_ts]
        return max(stamps) if stamps else None


class _NoRegistry:
    def __getattr__(self, name):
        raise AssertionError(
            f"registry.{name}() reached the live PostgreSQL from a test — ask for "
            "the tmp_store / fake_registry fixture instead of leaving rows in the "
            "shared dev database"
        )


@pytest.fixture(autouse=True)
def _no_live_registry(monkeypatch) -> None:
    """``store._registry`` resolves to the real module on first use, so a test
    that writes bars without the fake would silently register them in the shared
    dev DB. Autouse runs before the fixtures that inject the fake over it."""
    monkeypatch.setattr(store, "_registry", _NoRegistry())


@pytest.fixture
def fake_registry(monkeypatch) -> FakeRegistry:
    reg = FakeRegistry()
    monkeypatch.setattr(store, "_registry", reg)
    monkeypatch.setattr(fetch, "registry", reg)
    monkeypatch.setattr(backfill, "registry", reg)
    return reg


@pytest.fixture
def tmp_store(monkeypatch, tmp_path, fake_registry) -> FakeRegistry:
    """An isolated bar tree + registry: BARS_ROOT and the corporate-action cache
    both point into tmp_path."""
    monkeypatch.setattr(config, "BARS_ROOT", tmp_path / "stock-market-data")
    monkeypatch.setattr(config, "ACTIONS_DB", tmp_path / "actions.db")
    return fake_registry
