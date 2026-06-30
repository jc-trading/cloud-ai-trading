"""
Pydantic schemas for exchange connections.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.exchange.models import ExchangeType, TradingMode


class ExchangeCreate(BaseModel):
    exchange_type: ExchangeType
    api_key: str = Field(..., min_length=10)
    api_secret: str = Field(..., min_length=10)
    passphrase: Optional[str] = None  # For OKX
    permissions: list[str] = Field(default=["read"])
    trading_mode: TradingMode = TradingMode.SIMULATE
    ip_whitelist: Optional[list[str]] = None


class ExchangeUpdate(BaseModel):
    permissions: Optional[list[str]] = None
    trading_mode: Optional[TradingMode] = None
    ip_whitelist: Optional[list[str]] = None
    is_active: Optional[bool] = None


class ExchangeResponse(BaseModel):
    id: UUID
    exchange_type: ExchangeType
    permissions: list[str]
    trading_mode: TradingMode
    ip_whitelist: Optional[list[str]]
    is_active: bool
    last_synced_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ExchangeTestResult(BaseModel):
    success: bool
    message: str
    balance: Optional[dict] = None


class BalanceResponse(BaseModel):
    exchange_type: ExchangeType
    balances: dict  # {"USDT": 1000.0, "BTC": 0.5, ...}
    total_usdt: float
