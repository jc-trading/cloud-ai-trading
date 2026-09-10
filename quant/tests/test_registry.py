"""market_data_files / market_stream_symbols registry against a live PostgreSQL.

The constraints and the upsert semantics under test are server-side, so these
run against the compose stack's DB — which is the shared dev database, so every
symbol they touch carries the ``ZZREG`` prefix, the teardown deletes on that
prefix rather than on the two names it happens to know, and it then asserts the
prefix is gone. The registry is autocommit: there is no transaction to roll
back, so the sweep is the only guarantee. Skipped when PG is not reachable,
which keeps the host research venv green without a database.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from quant.data import registry

PREFIX = "ZZREG"
SYM = f"{PREFIX}A"
OTHER = f"{PREFIX}B"
_TABLES = ("_TABLE", "_STREAM_TABLE")

FIRST_TS = datetime(2025, 1, 2, 5, 0, tzinfo=timezone.utc)
LAST_TS = datetime(2025, 12, 31, 5, 0, tzinfo=timezone.utc)


def _purge() -> None:
    for attr in _TABLES:
        registry._run(f"DELETE FROM {getattr(registry, attr)} WHERE symbol LIKE %s",
                      (f"{PREFIX}%",))


def _leftovers() -> dict[str, int]:
    counts = {}
    for attr in _TABLES:
        table = getattr(registry, attr)
        n = registry._run(f"SELECT count(*) FROM {table} WHERE symbol LIKE %s",
                          (f"{PREFIX}%",), fetch="one")
        counts[table] = n[0]
    return counts


@pytest.fixture()
def pg():
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg not installed")
    try:
        registry._run("SELECT 1", (), fetch="one")
    except RuntimeError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    _purge()
    try:
        yield
    finally:
        _purge()
        left = _leftovers()
        assert not any(left.values()), f"test rows left in the shared dev DB: {left}"


def _upsert(period_key: str = "2025", **over) -> None:
    kw = dict(
        path=f"{SYM}/daily/{period_key}.parquet", provider="alpaca:sip",
        row_count=251, first_ts=FIRST_TS, last_ts=LAST_TS, checksum="a" * 64,
        fetched_at=LAST_TS,
    )
    kw.update(over)
    registry.upsert(SYM, "daily", period_key, **kw)


def test_upsert_get_roundtrip(pg):
    assert registry.get_row(SYM, "daily", "2025") is None
    _upsert(meta={"completeness": 0.99})
    row = registry.get_row(SYM, "daily", "2025")
    assert row.path == f"{SYM}/daily/2025.parquet"
    assert row.provider == "alpaca:sip"
    assert row.row_count == 251
    assert row.first_ts == FIRST_TS
    assert row.last_ts == LAST_TS
    assert row.status == "ok"
    assert row.meta["completeness"] == 0.99


def test_upsert_replaces_same_key(pg):
    _upsert()
    _upsert(provider="alpaca:iex", row_count=300, status="stale",
            checksum="b" * 64, meta=None)
    row = registry.get_row(SYM, "daily", "2025")
    assert (row.provider, row.row_count, row.status) == ("alpaca:iex", 300, "stale")
    assert row.meta is None
    n = registry._run(
        f"SELECT count(*) FROM {registry._TABLE} WHERE symbol = %s", (SYM,),
        fetch="one",
    )
    assert n[0] == 1


def test_naive_timestamps_are_stored_as_utc(pg):
    _upsert(first_ts=FIRST_TS.replace(tzinfo=None), last_ts=LAST_TS.replace(tzinfo=None))
    row = registry.get_row(SYM, "daily", "2025")
    assert row.first_ts == FIRST_TS
    assert row.last_ts == LAST_TS


def test_delete(pg):
    _upsert()
    assert registry.delete(SYM, "daily", "2025") == 1
    assert registry.get_row(SYM, "daily", "2025") is None
    assert registry.delete(SYM, "daily", "2025") == 0


def test_max_last_ts(pg):
    assert registry.max_last_ts(SYM, "daily") is None
    _upsert("2024", last_ts=datetime(2024, 12, 31, 5, 0, tzinfo=timezone.utc))
    _upsert("2025")
    assert registry.max_last_ts(SYM, "daily") == LAST_TS
    assert registry.max_last_ts(SYM, "1min") is None


def test_stream_symbols_enabled_and_ordered(pg):
    registry._run(
        f"INSERT INTO {registry._STREAM_TABLE} (symbol, priority, enabled, note) "
        f"VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
        (SYM, 50, True, "high priority", OTHER, 10, False, None),
    )
    rows = registry.stream_symbols()
    assert [r.symbol for r in rows if r.symbol in (SYM, OTHER)] == [SYM]
    picked = next(r for r in rows if r.symbol == SYM)
    assert (picked.priority, picked.note) == (50, "high priority")

    registry._run(
        f"UPDATE {registry._STREAM_TABLE} SET enabled = TRUE WHERE symbol = %s",
        (OTHER,),
    )
    ours = [r.symbol for r in registry.stream_symbols() if r.symbol in (SYM, OTHER)]
    assert ours == [OTHER, SYM]


def test_fails_closed_without_postgres(monkeypatch):
    pytest.importorskip("psycopg")
    registry.close()
    monkeypatch.setattr(registry, "_dsn",
                        lambda: "postgresql://nobody@127.0.0.1:1/nodb")
    with pytest.raises(RuntimeError, match="cannot reach PostgreSQL"):
        registry.get_row(SYM, "daily", "2025")
    registry.close()


def test_fails_closed_within_connect_timeout(monkeypatch):
    # a refused port fails instantly and proves nothing; 10.255.255.1 drops the
    # packets, so without connect_timeout this call blocks indefinitely
    pytest.importorskip("psycopg")
    registry.close()
    monkeypatch.setattr(registry, "CONNECT_TIMEOUT_S", 1)
    monkeypatch.setattr(registry, "_dsn",
                        lambda: "postgresql://nobody@10.255.255.1:5432/nodb")
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="cannot reach PostgreSQL"):
            registry.get_row(SYM, "daily", "2025")
    finally:
        elapsed = time.monotonic() - started
        registry.close()
    assert elapsed < 5, f"connect_timeout not honoured: blocked {elapsed:.1f}s"
