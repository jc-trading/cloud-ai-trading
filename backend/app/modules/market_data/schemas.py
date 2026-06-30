"""Pydantic schemas for market data."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# OHLCV Candle Schemas
class OHLCVCandle(BaseModel):
    """OHLCV candle data."""

    symbol: str
    timeframe: str  # 1m, 5m, 15m, 1h, 4h, 1d
    open_time: datetime
    close_time: datetime
    open: Decimal = Field(..., decimal_places=8)
    high: Decimal = Field(..., decimal_places=8)
    low: Decimal = Field(..., decimal_places=8)
    close: Decimal = Field(..., decimal_places=8)
    volume: Decimal = Field(..., decimal_places=8)
    quote_volume: Decimal = Field(..., decimal_places=8)
    trades_count: Optional[int] = None
    taker_buy_base_volume: Optional[Decimal] = Field(None, decimal_places=8)
    taker_buy_quote_volume: Optional[Decimal] = Field(None, decimal_places=8)

    model_config = {"json_encoders": {Decimal: lambda v: float(v)}}


class OHLCVCandleResponse(OHLCVCandle):
    """OHLCV candle response with ID."""

    id: UUID
    watchlist_id: UUID
    created_at: datetime
    updated_at: datetime


class OHLCVHistoryRequest(BaseModel):
    """Request to fetch OHLCV history."""

    symbol: str
    timeframe: str = Field(default="1h", description="1m, 5m, 15m, 1h, 4h, 1d")
    limit: int = Field(default=100, ge=1, le=1000)
    start_time: Optional[datetime] = None


# Technical Indicator Schemas
class TechnicalIndicators(BaseModel):
    """Technical indicators for a candle."""

    # Trend Indicators
    ema_12: Optional[Decimal] = Field(None, decimal_places=8)
    ema_26: Optional[Decimal] = Field(None, decimal_places=8)
    ema_50: Optional[Decimal] = Field(None, decimal_places=8)
    ema_200: Optional[Decimal] = Field(None, decimal_places=8)

    # Momentum Indicators
    rsi_14: Optional[Decimal] = Field(None, decimal_places=2)
    macd: Optional[Decimal] = Field(None, decimal_places=8)
    macd_signal: Optional[Decimal] = Field(None, decimal_places=8)
    macd_histogram: Optional[Decimal] = Field(None, decimal_places=8)

    # Volatility Indicators
    atr_14: Optional[Decimal] = Field(None, decimal_places=8)
    bb_upper: Optional[Decimal] = Field(None, decimal_places=8)
    bb_middle: Optional[Decimal] = Field(None, decimal_places=8)
    bb_lower: Optional[Decimal] = Field(None, decimal_places=8)
    bb_width: Optional[Decimal] = Field(None, decimal_places=8)
    bb_position: Optional[Decimal] = Field(None, decimal_places=2)

    model_config = {"json_encoders": {Decimal: lambda v: float(v)}}


class TechnicalIndicatorsResponse(TechnicalIndicators):
    """Technical indicators response with metadata."""

    id: UUID
    ohlcv_candle_id: UUID
    symbol: str
    timeframe: str
    timestamp: datetime
    created_at: datetime
    updated_at: datetime


class CandleWithIndicators(BaseModel):
    """OHLCV candle with calculated indicators."""

    candle: OHLCVCandleResponse
    indicators: Optional[TechnicalIndicatorsResponse] = None


# Market Data Event Schemas
class MarketDataEvent(BaseModel):
    """Market data event."""

    symbol: str
    event_type: str  # price_update, indicator_alert, etc
    price: Optional[Decimal] = Field(None, decimal_places=8)
    event_data: Optional[dict] = None


class MarketDataEventResponse(MarketDataEvent):
    """Market data event response with metadata."""

    id: UUID
    watchlist_id: UUID
    timestamp: datetime
    created_at: datetime


# Price Update Schemas
class RealtimePriceUpdate(BaseModel):
    """Real-time price update from WebSocket."""

    symbol: str
    price: Decimal = Field(..., decimal_places=8)
    bid: Decimal = Field(..., decimal_places=8)
    ask: Decimal = Field(..., decimal_places=8)
    high: Decimal = Field(..., decimal_places=8)
    low: Decimal = Field(..., decimal_places=8)
    volume: Decimal = Field(..., decimal_places=8)
    quote_volume: Decimal = Field(..., decimal_places=8)
    timestamp: datetime

    model_config = {"json_encoders": {Decimal: lambda v: float(v)}}


# Market Data Summary Schemas
class MarketDataSummary(BaseModel):
    """Summary of market data for a symbol."""

    symbol: str
    current_price: Decimal = Field(..., decimal_places=8)
    high_24h: Decimal = Field(..., decimal_places=8)
    low_24h: Decimal = Field(..., decimal_places=8)
    volume_24h: Decimal = Field(..., decimal_places=8)
    change_percent: Decimal = Field(..., decimal_places=2)
    last_update: datetime

    model_config = {"json_encoders": {Decimal: lambda v: float(v)}}


class WatchlistMarketData(BaseModel):
    """Market data for all items in a watchlist."""

    watchlist_id: UUID
    items: list[MarketDataSummary]
    updated_at: datetime
