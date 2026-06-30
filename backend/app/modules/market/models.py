"""
Market data models (K-line candles).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Numeric, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketCandle(Base):
    __tablename__ = "market_candles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)  # e.g., "BTC/USDT"
    exchange_type: Mapped[str] = mapped_column(String(20), default="binance")
    interval: Mapped[str] = mapped_column(String(10))  # 1m, 5m, 15m, 1h, 4h, 1d

    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    open: Mapped[float] = mapped_column(Numeric(20, 8))
    high: Mapped[float] = mapped_column(Numeric(20, 8))
    low: Mapped[float] = mapped_column(Numeric(20, 8))
    close: Mapped[float] = mapped_column(Numeric(20, 8))
    volume: Mapped[float] = mapped_column(Numeric(30, 8))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_candles_symbol_interval_time", "symbol", "interval", "open_time", unique=True),
    )
