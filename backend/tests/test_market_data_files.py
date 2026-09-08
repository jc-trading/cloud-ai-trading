"""ORM checks for the migration-016 market registry tables.

Runs against the live PostgreSQL of the compose stack (same DATABASE_URL_SYNC
alembic uses) because the constraints under test are server-side. Every write
stays inside a transaction that is rolled back, so the tables are left exactly
as they were; skipped when no PG is reachable.
"""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, pool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.market.models import MarketDataFile, MarketStreamSymbol

_FIRST_TS = datetime(2025, 1, 2, 5, 0, tzinfo=timezone.utc)
_LAST_TS = datetime(2025, 12, 31, 5, 0, tzinfo=timezone.utc)


def _file(**over) -> MarketDataFile:
    kw = dict(
        symbol="ZZTEST", timeframe="daily", period_key="2025",
        path="ZZTEST/daily/2025.parquet", provider="alpaca:sip", row_count=251,
        first_ts=_FIRST_TS, last_ts=_LAST_TS, checksum="a" * 64,
        fetched_at=_LAST_TS,
    )
    kw.update(over)
    return MarketDataFile(**kw)


@pytest.fixture()
def session():
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        pytest.skip("DATABASE_URL_SYNC not set — needs the compose stack")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        conn = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        if trans.is_active:
            trans.rollback()
        conn.close()
        engine.dispose()


class TestMarketDataFile:
    def test_roundtrip_defaults(self, session):
        session.add(_file())
        session.flush()
        row = session.query(MarketDataFile).filter_by(symbol="ZZTEST").one()
        assert row.id is not None
        assert row.status == "ok"
        assert row.meta is None
        assert row.last_ts == _LAST_TS
        assert row.created_at is not None

    def test_meta_jsonb_roundtrip(self, session):
        session.add(_file(meta={"completeness": 0.98, "iex_missing_minutes": 12}))
        session.flush()
        session.expire_all()
        row = session.query(MarketDataFile).filter_by(symbol="ZZTEST").one()
        assert row.meta["iex_missing_minutes"] == 12

    def test_unique_symbol_timeframe_period(self, session):
        session.add(_file())
        session.flush()
        session.add(_file(path="ZZTEST/daily/2025-dup.parquet", provider="alpaca:iex"))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_same_symbol_other_period_allowed(self, session):
        session.add(_file())
        session.add(_file(period_key="2024", path="ZZTEST/daily/2024.parquet"))
        session.add(_file(timeframe="1min", period_key="2025-06-02",
                          path="ZZTEST/1min/2025-06-02.parquet",
                          provider="alpaca:iex"))
        session.flush()
        assert session.query(MarketDataFile).filter_by(symbol="ZZTEST").count() == 3


class TestMarketStreamSymbol:
    def test_defaults(self, session):
        session.add(MarketStreamSymbol(symbol="ZZTEST"))
        session.flush()
        row = session.get(MarketStreamSymbol, "ZZTEST")
        assert row.priority == 100
        assert row.enabled is True
        assert row.note is None

    def test_symbol_is_primary_key(self, session):
        session.add(MarketStreamSymbol(symbol="ZZTEST", priority=1))
        session.flush()
        session.add(MarketStreamSymbol(symbol="ZZTEST", priority=2))
        with pytest.raises(IntegrityError):
            session.flush()
