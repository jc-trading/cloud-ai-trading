"""bar_manifest — per (symbol, timeframe) high-water mark for incremental fetch.

R0 uses a local SQLite file (design D2); R1 migrates this to PostgreSQL. The
manifest records the last CLOSED bar stored so the next fetch starts at
``last_ts + 1 bar`` (design §4.4 guard 2) instead of re-pulling history.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quant import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_manifest (
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    first_ts        TEXT,               -- ISO8601 UTC of first stored bar
    last_ts         TEXT,               -- ISO8601 UTC of last CLOSED stored bar
    row_count       INTEGER NOT NULL DEFAULT 0,
    last_fetched_at TEXT,               -- ISO8601 UTC of last successful fetch
    session         TEXT,               -- 'regular' | 'all'
    source          TEXT,               -- e.g. 'alpaca'
    PRIMARY KEY (symbol, timeframe)
);
"""


@dataclass(frozen=True)
class ManifestRow:
    symbol: str
    timeframe: str
    first_ts: datetime | None
    last_ts: datetime | None
    row_count: int
    last_fetched_at: datetime | None
    session: str | None
    source: str | None


def _parse(ts: str | None) -> datetime | None:
    if ts is None:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.MANIFEST_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(_SCHEMA)
    return conn


def get_row(symbol: str, timeframe: str, *, db_path: Path | None = None) -> ManifestRow | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT symbol, timeframe, first_ts, last_ts, row_count, "
            "last_fetched_at, session, source FROM bar_manifest "
            "WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        r = cur.fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    return ManifestRow(
        symbol=r[0], timeframe=r[1], first_ts=_parse(r[2]), last_ts=_parse(r[3]),
        row_count=r[4], last_fetched_at=_parse(r[5]), session=r[6], source=r[7],
    )


def get_last_ts(symbol: str, timeframe: str, *, db_path: Path | None = None) -> datetime | None:
    row = get_row(symbol, timeframe, db_path=db_path)
    return row.last_ts if row else None


def upsert(
    symbol: str,
    timeframe: str,
    *,
    first_ts: datetime,
    last_ts: datetime,
    row_count: int,
    fetched_at: datetime,
    session: str,
    source: str,
    db_path: Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO bar_manifest (symbol, timeframe, first_ts, last_ts, "
            "row_count, last_fetched_at, session, source) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol, timeframe) DO UPDATE SET "
            "first_ts=excluded.first_ts, last_ts=excluded.last_ts, "
            "row_count=excluded.row_count, last_fetched_at=excluded.last_fetched_at, "
            "session=excluded.session, source=excluded.source",
            (symbol, timeframe, _iso(first_ts), _iso(last_ts), int(row_count),
             _iso(fetched_at), session, source),
        )
        conn.commit()
    finally:
        conn.close()
