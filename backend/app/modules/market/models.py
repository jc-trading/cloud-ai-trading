"""Market data registry (migration 016, 2026-09-08).

PostgreSQL holds NO bars — bars live in Parquet under BARS_ROOT. These two
tables are the metadata around that store:

  - market_data_files    one row per Parquet file: provenance (a file has
                         exactly one provider), row count, ts range, checksum.
                         Also the incremental-fetch high-water mark, which
                         replaces the retired SQLite bar manifest.
                         Its ONLY writer is quant.data.store.write_bars; the
                         backend is read-only here.
  - market_stream_symbols the runtime-controlled realtime subscription set
                         (lower priority subscribes first). This one IS
                         operational config, so the backend may write it.
"""

from uuid import uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Index, Integer, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class MarketDataFile(Base):
    __tablename__ = "market_data_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)       # daily|1hour|1min
    period_key = Column(String(10), nullable=False)      # %Y | %Y-%m | %Y-%m-%d (ET)

    path = Column(Text, nullable=False)                  # relative to BARS_ROOT
    provider = Column(String(30), nullable=False)        # alpaca:sip|alpaca:iex
    row_count = Column(Integer, nullable=False, server_default="0")
    first_ts = Column(DateTime(timezone=True), nullable=False)
    last_ts = Column(DateTime(timezone=True), nullable=False)
    checksum = Column(String(64), nullable=False)        # sha256 of canonical rows + provider
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False, server_default="ok")  # ok|stale|partial
    meta = Column(JSONB, nullable=True)                  # EOD feed-comparison stats

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "period_key",
                         name="uq_market_data_file"),
        Index("ix_market_data_files_symbol_timeframe", "symbol", "timeframe"),
        Index("ix_market_data_files_provider", "provider"),
    )


class MarketStreamSymbol(Base):
    __tablename__ = "market_stream_symbols"

    symbol = Column(String(20), primary_key=True)
    priority = Column(Integer, nullable=False, server_default="100")
    enabled = Column(Boolean, nullable=False, server_default=text("true"))
    note = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


SECURITY_STATUSES = ("active", "inactive")
SECURITY_ASSET_TYPES = ("stock", "adr", "etf", "etn", "preferred", "warrant", "unit",
                        "right", "debt", "test", "unknown")


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class Security(Base):
    """One listing of the whole-US-equity universe (P2). Written ONLY by
    quant.data.securities.refresh; ``shares_checked_at`` by quant.data.edgar."""

    __tablename__ = "securities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    market = Column(String(10), nullable=False, server_default="us")
    code = Column(String(20), nullable=False)
    name = Column(String(300), nullable=True)
    exchange = Column(String(10), nullable=True)
    asset_type = Column(String(12), nullable=False)
    status = Column(String(10), nullable=False, server_default="active")
    tradable = Column(Boolean, nullable=False)
    cik = Column(Integer, nullable=True)
    first_seen = Column(Date, nullable=False)
    last_seen = Column(Date, nullable=False)
    inactive_since = Column(Date, nullable=True)
    shares_checked_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("market", "code", name="uq_security_market_code"),
        CheckConstraint(_in("status", SECURITY_STATUSES), name="ck_security_status"),
        CheckConstraint("(status = 'inactive') = (inactive_since IS NOT NULL)",
                        name="ck_security_inactive_since"),
        CheckConstraint(_in("asset_type", SECURITY_ASSET_TYPES),
                        name="ck_security_asset_type"),
        Index("ix_securities_cik", "cik"),
    )


class SecurityShareFact(Base):
    """SEC companyconcept share-count facts, kept with their filing date so a
    screen for session D only sees what was public on D (P2)."""

    __tablename__ = "security_share_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    cik = Column(Integer, nullable=False)
    concept = Column(String(80), nullable=False)
    end_date = Column(Date, nullable=False)
    filed = Column(Date, nullable=False)
    accn = Column(String(25), nullable=False)
    form = Column(String(12), nullable=True)
    value = Column(BigInteger, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("cik", "concept", "accn", "end_date", name="uq_share_fact"),
        CheckConstraint("value > 0", name="ck_share_fact_value"),
        Index("ix_share_facts_cik_filed", "cik", "filed"),
    )
