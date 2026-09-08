"""
Pydantic schemas for market data.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TickerResponse(BaseModel):
    symbol: str
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    high: float
    low: float
    volume: float
    quote_volume: Optional[float] = None
    change_24h: Optional[float] = None
    timestamp: Optional[int] = None
    market_type: str = "crypto"  # "crypto" or "stock"


class CandleResponse(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleQuery(BaseModel):
    interval: str = "1h"  # 1m, 5m, 15m, 1h, 1d
    limit: int = 100


class SymbolDetailResponse(BaseModel):
    symbol: str
    ticker: TickerResponse
    candles: list[CandleResponse]


class BarPoint(BaseModel):
    """One stored bar, RAW — Alpaca's field names so a consumer can swap sources."""
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    vwap: Optional[float] = None
    n: Optional[int] = None


class BarsResponse(BaseModel):
    symbol: str
    timeframe: str
    provider: Optional[str] = None   # market_data_files: alpaca:sip | alpaca:iex
    last_ts: Optional[datetime] = None
    bars: list[BarPoint]


class StreamSymbolCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    priority: int = Field(default=100, ge=0, le=1000)
    note: Optional[str] = Field(default=None, max_length=200)


class StreamSymbolResponse(BaseModel):
    symbol: str
    priority: int
    enabled: bool
    note: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}
