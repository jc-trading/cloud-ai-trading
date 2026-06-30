"""
Pydantic schemas for watchlist management.
"""

from datetime import datetime
from typing import Optional
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
    # "crypto" for BTC/USDT-style symbols, "stock" for AAPL-style
    market_type: str = Field(default="crypto")
    notes: Optional[str] = None

    @property
    def exchange_type(self) -> str:
        return "alpaca" if self.market_type == "stock" else "binance"


class WatchlistItemResponse(BaseModel):
    id: UUID
    symbol: str
    exchange_type: str
    market_type: str = "crypto"
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
