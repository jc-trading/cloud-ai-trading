"""Pydantic schemas for trading operations."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TradeCreate(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=50)
    side: str = Field(..., pattern="^(buy|sell)$")
    order_type: str = Field(default="market", pattern="^(market|limit|stop_limit)$")
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(None, gt=0)
    trading_mode: str = Field(default="simulate", pattern="^(live|simulate)$")
    exchange_connection_id: Optional[UUID] = None
    strategy_id: Optional[UUID] = None


class TradeResponse(BaseModel):
    id: UUID
    watchlist_id: UUID
    symbol: str
    side: str
    quantity: float
    price: float
    entry_price: float
    current_price: float
    status: str
    position_type: str
    pnl: float
    pnl_percentage: float
    return_pct: float
    opened_at: datetime
    timestamp: datetime
    closed_at: Optional[datetime]
    created_at: datetime


class TradeFilter(BaseModel):
    symbol: Optional[str] = None
    side: Optional[str] = None
    status: Optional[str] = None
    trading_mode: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class SimulatePortfolioResponse(BaseModel):
    user_id: UUID
    initial_balance: float = 0.0
    balance: float = 0.0
    current_balance: float
    total_invested: float
    current_value: float
    total_pnl: float
    unrealized_pnl: float
    realized_pnl: float
    total_return_percent: float
    total_trades: int
    open_trades: int
    closed_trades: int
    win_count: int
    loss_count: int
    win_rate: float = 0.0
    positions: list[TradeResponse] = []


class TradeSignalResponse(BaseModel):
    id: UUID
    watchlist_id: UUID
    symbol: str
    signal_type: str
    signal_strength: float
    confidence: float
    indicators_used: Optional[dict] = None
    recommendation: Optional[str] = None
    strategy: Optional[str] = None
    signal_timestamp: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class TradeSummary(BaseModel):
    total_trades: int = 0
    open_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    best_trade_pnl: Optional[float] = None
    worst_trade_pnl: Optional[float] = None
