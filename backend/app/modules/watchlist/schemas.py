"""
Pydantic schemas for watchlist management.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    name: str = Field(default="My Watchlist", max_length=100)


class WatchlistResponse(BaseModel):
    id: UUID
    name: str
    items: list["WatchlistItemResponse"] = []
    created_at: datetime
    model_config = {"from_attributes": True}


class WatchlistItemCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    # Stocks only (Direction v3) — any non-"stock" value is rejected with 422
    # so no new crypto rows can appear.
    market_type: Literal["stock"] = "stock"
    notes: Optional[str] = None


class WatchlistItemResponse(BaseModel):
    id: UUID
    symbol: str
    exchange_type: str
    market_type: str = "stock"
    notes: Optional[str]
    synced_with_exchange: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class WatchlistItemWithPrice(BaseModel):
    """Watchlist item enriched with live market price."""
    id: UUID
    symbol: str
    exchange_type: str
    market_type: str
    notes: Optional[str]
    created_at: datetime

    # Live price data (None if unavailable)
    last: Optional[float] = None
    change_24h: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None

    model_config = {"from_attributes": True}
