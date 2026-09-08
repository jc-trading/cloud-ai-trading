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
    Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint,
    func, text,
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
