"""market_data_files / market_stream_symbols access — the PG replacement for
the SQLite bar manifest (Phase 3, 2026-09-08).

Shape mirrors the retired ``manifest.py`` one-to-one for the file registry
(``get_row / upsert / delete``), plus ``max_last_ts`` — the incremental-fetch
high-water mark, now derived from the registry instead of stored separately —
``stream_symbols`` for the runtime realtime-subscription set, and the handful
of read-only lookups the stream consumer needs from the ledger (the 对照账户,
its open positions, its owner's watchlist) plus the ``heartbeats`` upsert the
watchdog reads — raw SQL here rather than ``app.*`` ORM, which ``quant/`` must
never import.

Raw psycopg 3 on purpose: ``quant/`` stays framework-free (no SQLAlchemy). The
driver is imported lazily inside the calls and NO connection is opened at
import time, so the Parquet read path never depends on PostgreSQL being up. A
writer that does need it fails closed with an explicit error rather than
silently skipping the provenance row. One connection is reused per process
(30 symbols × one upsert per minute needs no more), reconnecting if it drops.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

from dotenv import dotenv_values

from quant import config

_TABLE = "market_data_files"
_STREAM_TABLE = "market_stream_symbols"
_HEARTBEAT_TABLE = "heartbeats"
# a dropped-packet host would otherwise hang the writer indefinitely; fail
# closed within a bounded wait instead
CONNECT_TIMEOUT_S = 5

_conn: Any = None


@dataclass(frozen=True)
class FileRow:
    symbol: str
    timeframe: str
    period_key: str
    path: str
    provider: str
    row_count: int
    first_ts: datetime | None
    last_ts: datetime | None
    checksum: str
    fetched_at: datetime | None
    status: str
    meta: dict | None


@dataclass(frozen=True)
class StreamSymbol:
    symbol: str
    priority: int
    note: str | None


@dataclass(frozen=True)
class SystemAccount:
    account_id: Any
    user_id: Any


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def _dsn() -> str:
    # inside the backend container the URL arrives as an ENV var (compose
    # passthrough) and no .env is mounted; on the host it falls back to repo
    # .env. The environment wins so a host run can target the compose postgres
    # on 5433 without editing the shared secret file.
    cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
    dsn = os.environ.get("DATABASE_URL_SYNC") or cfg.get("DATABASE_URL_SYNC")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL_SYNC missing from .env / environment — the bar file "
            "registry requires PostgreSQL"
        )
    return dsn


def _connect():
    import psycopg

    try:
        return psycopg.connect(_dsn(), autocommit=True,
                               connect_timeout=CONNECT_TIMEOUT_S)
    except Exception as exc:
        raise RuntimeError(
            f"cannot reach PostgreSQL for the bar file registry: {exc}"
        ) from exc


def _run(sql: str, params: tuple, *, fetch: str | None = None):
    """Execute one statement on the process-wide connection, retrying once if it died.

    Only connection-level failures are retried; a real SQL error (constraint
    violation) propagates on the first attempt.
    """
    import psycopg

    global _conn
    for attempt in (0, 1):
        if _conn is None or _conn.closed:
            _conn = _connect()
        try:
            with _conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return cur.rowcount
        except (psycopg.OperationalError, psycopg.InterfaceError):
            close()
            if attempt:
                raise


def close() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def get_row(symbol: str, timeframe: str, period_key: str) -> FileRow | None:
    r = _run(
        f"SELECT symbol, timeframe, period_key, path, provider, row_count, "
        f"first_ts, last_ts, checksum, fetched_at, status, meta FROM {_TABLE} "
        f"WHERE symbol = %s AND timeframe = %s AND period_key = %s",
        (symbol, timeframe, period_key),
        fetch="one",
    )
    if r is None:
        return None
    return FileRow(
        symbol=r[0], timeframe=r[1], period_key=r[2], path=r[3], provider=r[4],
        row_count=r[5], first_ts=_utc(r[6]), last_ts=_utc(r[7]), checksum=r[8],
        fetched_at=_utc(r[9]), status=r[10], meta=r[11],
    )


def upsert(
    symbol: str,
    timeframe: str,
    period_key: str,
    *,
    path: str,
    provider: str,
    row_count: int,
    first_ts: datetime,
    last_ts: datetime,
    checksum: str,
    fetched_at: datetime,
    status: str = "ok",
    meta: dict | None = None,
) -> None:
    from psycopg.types.json import Jsonb

    _run(
        f"INSERT INTO {_TABLE} (id, symbol, timeframe, period_key, path, provider, "
        f"row_count, first_ts, last_ts, checksum, fetched_at, status, meta) "
        f"VALUES (gen_random_uuid(), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        f"ON CONFLICT (symbol, timeframe, period_key) DO UPDATE SET "
        f"path = EXCLUDED.path, provider = EXCLUDED.provider, "
        f"row_count = EXCLUDED.row_count, first_ts = EXCLUDED.first_ts, "
        f"last_ts = EXCLUDED.last_ts, checksum = EXCLUDED.checksum, "
        f"fetched_at = EXCLUDED.fetched_at, status = EXCLUDED.status, "
        f"meta = EXCLUDED.meta, updated_at = now()",
        (symbol, timeframe, period_key, path, provider, int(row_count),
         _utc(first_ts), _utc(last_ts), checksum, _utc(fetched_at), status,
         Jsonb(meta) if meta is not None else None),
    )


def delete(symbol: str, timeframe: str, period_key: str) -> int:
    return _run(
        f"DELETE FROM {_TABLE} WHERE symbol = %s AND timeframe = %s AND period_key = %s",
        (symbol, timeframe, period_key),
    )


def update_status(symbol: str, timeframe: str, period_key: str, *, status: str,
                  meta: dict | None = None) -> int:
    """Stamp the EOD correction's verdict on an existing row without touching
    the provenance ``write_bars`` owns. ``meta`` MERGES into whatever is stored
    (a later run that has no feed comparison to add must not erase the one an
    earlier run measured); None leaves the stats untouched."""
    from psycopg.types.json import Jsonb

    if meta is None:
        return _run(
            f"UPDATE {_TABLE} SET status = %s, updated_at = now() "
            f"WHERE symbol = %s AND timeframe = %s AND period_key = %s",
            (status, symbol.upper(), timeframe, period_key),
        )
    return _run(
        f"UPDATE {_TABLE} SET status = %s, "
        f"meta = COALESCE(meta, '{{}}'::jsonb) || %s, updated_at = now() "
        f"WHERE symbol = %s AND timeframe = %s AND period_key = %s",
        (status, Jsonb(meta), symbol.upper(), timeframe, period_key),
    )


def symbols_with_file(timeframe: str, period_key: str) -> list[str]:
    rows = _run(
        f"SELECT symbol FROM {_TABLE} WHERE timeframe = %s AND period_key = %s "
        f"ORDER BY symbol ASC",
        (timeframe, period_key),
        fetch="all",
    )
    return [str(r[0]).upper() for r in rows]


def max_last_ts(symbol: str, timeframe: str) -> datetime | None:
    r = _run(
        f"SELECT max(last_ts) FROM {_TABLE} WHERE symbol = %s AND timeframe = %s",
        (symbol, timeframe),
        fetch="one",
    )
    return _utc(r[0]) if r else None


def stream_symbols() -> list[StreamSymbol]:
    rows = _run(
        f"SELECT symbol, priority, note FROM {_STREAM_TABLE} "
        f"WHERE enabled IS TRUE ORDER BY priority ASC, symbol ASC",
        (),
        fetch="all",
    )
    return [StreamSymbol(symbol=r[0], priority=r[1], note=r[2]) for r in rows]


def system_account() -> SystemAccount | None:
    """The 对照账户, by the same stable identity rule as
    ``SimLedgerService.system_account``: the oldest ``is_system`` row wins."""
    r = _run(
        "SELECT id, user_id FROM sim_accounts WHERE is_system IS TRUE "
        "ORDER BY created_at ASC LIMIT 1",
        (),
        fetch="one",
    )
    return SystemAccount(account_id=r[0], user_id=r[1]) if r else None


def open_position_symbols(account_id) -> list[str]:
    rows = _run(
        "SELECT DISTINCT symbol FROM sim_positions "
        "WHERE account_id = %s AND status = 'open' ORDER BY symbol ASC",
        (account_id,),
        fetch="all",
    )
    return [str(r[0]).upper() for r in rows]


def watchlist_symbols(user_id) -> list[str]:
    rows = _run(
        "SELECT DISTINCT wi.symbol FROM watchlist_items wi "
        "JOIN watchlists w ON w.id = wi.watchlist_id "
        "WHERE w.user_id = %s AND wi.market_type = 'stock' ORDER BY 1",
        (user_id,),
        fetch="all",
    )
    return [str(r[0]).upper() for r in rows]


def resolve_stream_symbols(
    source, *, cap: int | None = None, extra: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """The subscription set every 1min path resolves the same way: the 对照账户's
    open positions, then the enabled ``market_stream_symbols`` rows, then its
    owner's stock watchlist, then ``extra`` (the EOD correction adds the day's
    already-stored files). Deduped, uppercased, first occurrence wins.

    ``source`` is the registry to read from — this module in production, a fake
    in tests. Returns ``(kept, dropped)``; ``cap`` truncates and names what fell
    off so the caller can log it. REST callers pass no cap: the 30-symbol limit
    is counted per WebSocket connection, not per request.
    """
    held: list[str] = []
    watch: list[str] = []
    account = source.system_account()
    if account is not None:
        held = source.open_position_symbols(account.account_id)
        watch = source.watchlist_symbols(account.user_id)
    configured = [row.symbol for row in source.stream_symbols()]

    ordered: list[str] = []
    for symbol in (*held, *configured, *watch, *extra):
        symbol = symbol.upper()
        if symbol not in ordered:
            ordered.append(symbol)
    if cap is not None and len(ordered) > cap:
        return ordered[:cap], ordered[cap:]
    return ordered, []


def beat(name: str, meta: dict | None = None) -> None:
    from psycopg.types.json import Jsonb

    _run(
        f"INSERT INTO {_HEARTBEAT_TABLE} (id, name, last_beat_at, meta) "
        f"VALUES (gen_random_uuid(), %s, now(), %s) "
        f"ON CONFLICT (name) DO UPDATE SET "
        f"last_beat_at = EXCLUDED.last_beat_at, meta = EXCLUDED.meta",
        (name, Jsonb(meta) if meta is not None else None),
    )
