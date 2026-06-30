"""
Pydantic schemas for market data.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


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
    interval: str = "1h"  # 1m, 5m, 15m, 1h, 4h, 1d
    limit: int = 100


class SymbolDetailResponse(BaseModel):
    symbol: str
    ticker: TickerResponse
    candles: list[CandleResponse]
