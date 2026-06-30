"""Market data models for OHLCV candles, technical indicators, and events."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class OHLCVCandle(Base):
    """OHLCV (Open, High, Low, Close, Volume) candle data."""

    __tablename__ = "ohlcv_candles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)  # 1m, 5m, 15m, 1h, 4h, 1d

    open_time = Column(DateTime(timezone=True), nullable=False, index=True)  # UTC
    close_time = Column(DateTime(timezone=True), nullable=False)

    open_price = Column(Numeric(precision=18, scale=8), nullable=False)
    high_price = Column(Numeric(precision=18, scale=8), nullable=False)
    low_price = Column(Numeric(precision=18, scale=8), nullable=False)
    close_price = Column(Numeric(precision=18, scale=8), nullable=False)

    volume = Column(Numeric(precision=20, scale=8), nullable=False)  # Base asset volume
    quote_volume = Column(Numeric(precision=20, scale=8), nullable=False)  # USDT volume

    trades_count = Column(Integer(), nullable=True)
    taker_buy_base_volume = Column(Numeric(precision=20, scale=8), nullable=True)
    taker_buy_quote_volume = Column(Numeric(precision=20, scale=8), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    watchlist = relationship("Watchlist", back_populates="ohlcv_candles")
    technical_indicators = relationship("TechnicalIndicator", back_populates="ohlcv_candle", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('watchlist_id', 'symbol', 'timeframe', 'open_time', name='uq_ohlcv_unique_candle'),
        Index('ix_ohlcv_candles_watchlist_id', 'watchlist_id'),
        Index('ix_ohlcv_candles_symbol', 'symbol'),
        Index('ix_ohlcv_candles_timeframe', 'timeframe'),
        Index('ix_ohlcv_candles_open_time', 'open_time'),
    )


class TechnicalIndicator(Base):
    """Technical indicators calculated for OHLCV candles."""

    __tablename__ = "technical_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    ohlcv_candle_id = Column(UUID(as_uuid=True), ForeignKey("ohlcv_candles.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)

    # Trend Indicators
    ema_12 = Column(Numeric(precision=18, scale=8), nullable=True)
    ema_26 = Column(Numeric(precision=18, scale=8), nullable=True)
    ema_50 = Column(Numeric(precision=18, scale=8), nullable=True)
    ema_200 = Column(Numeric(precision=18, scale=8), nullable=True)

    # Momentum Indicators
    rsi_14 = Column(Numeric(precision=10, scale=2), nullable=True)
    macd = Column(Numeric(precision=18, scale=8), nullable=True)
    macd_signal = Column(Numeric(precision=18, scale=8), nullable=True)
    macd_histogram = Column(Numeric(precision=18, scale=8), nullable=True)

    # Volatility Indicators
    atr_14 = Column(Numeric(precision=18, scale=8), nullable=True)
    bb_upper = Column(Numeric(precision=18, scale=8), nullable=True)
    bb_middle = Column(Numeric(precision=18, scale=8), nullable=True)
    bb_lower = Column(Numeric(precision=18, scale=8), nullable=True)
    bb_width = Column(Numeric(precision=18, scale=8), nullable=True)
    bb_position = Column(Numeric(precision=5, scale=2), nullable=True)  # % position

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    watchlist = relationship("Watchlist", back_populates="technical_indicators")
    ohlcv_candle = relationship("OHLCVCandle", back_populates="technical_indicators")

    __table_args__ = (
        UniqueConstraint('ohlcv_candle_id', name='uq_indicators_per_candle'),
        Index('ix_technical_indicators_symbol_timeframe', 'symbol', 'timeframe'),
        Index('ix_technical_indicators_timestamp', 'timestamp'),
    )


class MarketDataEvent(Base):
    """Market data events (price updates, indicator alerts, etc)."""

    __tablename__ = "market_data_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # price_update, indicator_alert, etc
    event_data = Column(JSON(), nullable=True)
    price = Column(Numeric(precision=18, scale=8), nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    watchlist = relationship("Watchlist", back_populates="market_data_events")

    __table_args__ = (
        Index('ix_market_data_events_watchlist_id', 'watchlist_id'),
        Index('ix_market_data_events_symbol', 'symbol'),
        Index('ix_market_data_events_timestamp', 'timestamp'),
    )
