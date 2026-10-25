"""market_data_files / market_stream_symbols access — the PG replacement for
the SQLite bar manifest (Phase 3, 2026-09-08).

Shape mirrors the retired ``manifest.py`` one-to-one for the file registry
(``get_row / upsert / delete``), plus ``max_last_ts`` — the incremental-fetch
high-water mark, now derived from the registry instead of stored separately —
``stream_symbols`` for the runtime realtime-subscription set, and the handful
of read-only lookups the stream consumer needs from the ledger (the managed
strategy groups, their open positions, their owners' watchlists, the 1m watch
rows) plus the ``heartbeats`` upsert the watchdog reads — raw SQL here rather
than ``app.*`` ORM, which ``quant/`` must never import.

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
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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
class ManagedGroup:
    group_id: Any
    name: str
    timeframe: str
    status: str
    account_id: Any
    user_id: Any
    created_at: datetime | None


@dataclass(frozen=True)
class WatchRow:
    group_id: Any
    group_name: str
    symbol: str
    rank: int
    reason: str


@dataclass(frozen=True)
class WatchEntry:
    symbol: str
    tier: str
    groups: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class IntradayGroupRow:
    group_id: Any
    name: str
    strategy_key: str
    params: dict
    screener: dict


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


def run_many(sql: str, rows: list[tuple]) -> int:
    """``_run`` for a batch: one executemany on the process-wide connection,
    retried once on a dropped connection. Returns the affected row count."""
    import psycopg

    global _conn
    for attempt in (0, 1):
        if _conn is None or _conn.closed:
            _conn = _connect()
        try:
            with _conn.cursor() as cur:
                cur.executemany(sql, rows)
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


def managed_groups() -> list[ManagedGroup]:
    rows = _run(
        "SELECT g.id, g.name, g.timeframe, g.status, a.id, a.user_id, g.created_at "
        "FROM strategy_groups g JOIN sim_accounts a ON a.id = g.sim_account_id "
        "WHERE g.status IN ('active', 'paused') ORDER BY g.created_at, g.id",
        (),
        fetch="all",
    )
    return [ManagedGroup(group_id=r[0], name=r[1], timeframe=r[2], status=r[3],
                         account_id=r[4], user_id=r[5], created_at=_utc(r[6]))
            for r in rows]


def active_1m_groups() -> list[IntradayGroupRow]:
    rows = _run(
        "SELECT id, name, strategy_key, params_json, screener_json FROM strategy_groups "
        "WHERE status = 'active' AND timeframe = '1m' ORDER BY created_at, id",
        (),
        fetch="all",
    )
    return [IntradayGroupRow(group_id=r[0], name=r[1], strategy_key=r[2], params=r[3],
                             screener=r[4]) for r in rows]


def intraday_watch_rows(session: date) -> list[WatchRow]:
    """The active 1m groups' shortlist rows for one session. Empty while the
    ``intraday_watch`` table does not exist yet — checked on every call, never
    cached, so rows are picked up as soon as the migration lands."""
    if _run("SELECT to_regclass('public.intraday_watch')", (), fetch="one")[0] is None:
        return []
    rows = _run(
        "SELECT w.group_id, g.name, w.symbol, w.rank, w.reason FROM intraday_watch w "
        "JOIN strategy_groups g ON g.id = w.group_id "
        "WHERE w.session_date = %s AND g.status = 'active' AND g.timeframe = '1m' "
        "ORDER BY g.created_at, g.id, w.rank",
        (session,),
        fetch="all",
    )
    return [WatchRow(group_id=r[0], group_name=r[1], symbol=str(r[2]).upper(),
                     rank=r[3], reason=r[4]) for r in rows]


def replace_watch_rows(session: date, group_id, rows: list[tuple]) -> int:
    """Replace one group's watch rows for ``session`` with ``rows`` of
    ``(symbol, rank, score, reason)`` in a single transaction, so a reader never
    sees the old and new lists mixed. Not retried: a dropped connection mid-way
    rolls back and the next nightly slot rebuilds."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = _connect()
    with _conn.transaction():
        with _conn.cursor() as cur:
            cur.execute("DELETE FROM intraday_watch WHERE session_date = %s AND group_id = %s",
                        (session, group_id))
            cur.executemany(
                "INSERT INTO intraday_watch (id, session_date, group_id, symbol, rank, score, "
                "reason, created_at) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, now())",
                [(session, group_id, symbol, rank, score, reason)
                 for symbol, rank, score, reason in rows])
    return len(rows)


def watch_session(now=None) -> date:
    """The session the stream is watching: today until its extended close,
    then the next trading day."""
    from quant.data import calendar

    now = _utc(now) if now is not None else datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    if calendar.is_trading_day(day) and now < calendar.session_bounds(day)[1]:
        return day
    return calendar.next_session(day)


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


def resolve_watch(
    source, *, session: date | None = None, now=None, cap: int | None = None,
    extra: Iterable[str] = (),
) -> tuple[list[WatchEntry], list[WatchEntry]]:
    """The subscription set every 1min path resolves the same way, tier by tier:
    the 1m groups' open positions, their shortlists for ``session`` (round-robin
    across groups), the other groups' open positions, the enabled
    ``market_stream_symbols`` rows, the group owners' stock watchlists, then
    ``extra`` (the EOD correction adds the day's already-stored files).
    Deduped and uppercased: the first occurrence sets tier and reason, every
    occurrence adds its group.

    ``source`` is the registry to read from — this module in production, a fake
    in tests. Returns ``(kept, dropped)``; ``cap`` truncates and names what fell
    off so the caller can log it. REST callers pass no cap: the 30-symbol limit
    is counted per WebSocket connection, not per request.
    """
    groups = source.managed_groups()
    session = session or watch_session(now)
    held_1m: list[tuple[str, str]] = []
    held_1d: list[tuple[str, str]] = []
    owners: list = []
    for g in groups:
        held = held_1m if g.timeframe == "1m" else held_1d
        held.extend((s, g.name) for s in source.open_position_symbols(g.account_id))
        if g.user_id not in owners:
            owners.append(g.user_id)

    queues: dict[str, list] = {}
    for row in source.intraday_watch_rows(session):
        queues.setdefault(row.group_name, []).append(row)
    shortlist = []
    for i in range(max((len(q) for q in queues.values()), default=0)):
        shortlist.extend(q[i] for q in queues.values() if i < len(q))

    seq: list[tuple[str, str, str | None, str]] = []
    seq += [(s, "held_1m", name, "open position") for s, name in held_1m]
    seq += [(r.symbol, "shortlist_1m", r.group_name, r.reason) for r in shortlist]
    seq += [(s, "held_1d", name, "open position") for s, name in held_1d]
    seq += [(row.symbol, "configured", None,
             f"market_stream_symbols priority {row.priority}")
            for row in source.stream_symbols()]
    for user_id in owners:
        seq += [(s, "watchlist", None, "owner watchlist")
                for s in source.watchlist_symbols(user_id)]
    seq += [(s, "extra", None, "stored file") for s in extra]

    order: list[str] = []
    first: dict[str, tuple[str, str]] = {}
    seen_groups: dict[str, list[str]] = {}
    for symbol, tier, group, reason in seq:
        symbol = symbol.upper()
        if symbol not in first:
            order.append(symbol)
            first[symbol] = (tier, reason)
            seen_groups[symbol] = []
        if group is not None and group not in seen_groups[symbol]:
            seen_groups[symbol].append(group)
    entries = [WatchEntry(symbol=s, tier=first[s][0], groups=tuple(seen_groups[s]),
                          reason=first[s][1]) for s in order]
    if cap is not None and len(entries) > cap:
        return entries[:cap], entries[cap:]
    return entries, []


def resolve_stream_symbols(
    source, *, cap: int | None = None, extra: Iterable[str] = (), now=None,
) -> tuple[list[str], list[str]]:
    """``resolve_watch`` as two symbol lists ``(kept, dropped)``."""
    kept, dropped = resolve_watch(source, now=now, cap=cap, extra=extra)
    return [e.symbol for e in kept], [e.symbol for e in dropped]


def beat(name: str, meta: dict | None = None) -> None:
    from psycopg.types.json import Jsonb

    _run(
        f"INSERT INTO {_HEARTBEAT_TABLE} (id, name, last_beat_at, meta) "
        f"VALUES (gen_random_uuid(), %s, now(), %s) "
        f"ON CONFLICT (name) DO UPDATE SET "
        f"last_beat_at = EXCLUDED.last_beat_at, meta = EXCLUDED.meta",
        (name, Jsonb(meta) if meta is not None else None),
    )
