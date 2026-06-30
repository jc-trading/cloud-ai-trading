"""
Pydantic schemas for quantitative strategies.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = Field(default="1h", pattern="^(1m|5m|15m|1h|4h|1d)$")

    indicators_config: dict = Field(default_factory=lambda: {
        "rsi": {"period": 14, "overbought": 70, "oversold": 30},
        "macd": {"fast": 12, "slow": 26, "signal": 9},
        "ema": {"short": 20, "long": 50},
        "bollinger": {"period": 20, "std_dev": 2},
    })

    entry_conditions: dict = Field(default_factory=lambda: {
        "rsi_oversold": True,
        "macd_crossover": True,
        "price_above_ema": False,
    })

    exit_conditions: dict = Field(default_factory=lambda: {
        "rsi_overbought": True,
        "macd_crossunder": True,
        "take_profit_hit": True,
        "stop_loss_hit": True,
    })

    position_sizing: dict = Field(default_factory=lambda: {
        "type": "fixed_percentage",
        "value": 5.0,
    })

    stop_loss_pct: float = Field(default=3.0, ge=0.5, le=50.0)
    take_profit_pct: float = Field(default=8.0, ge=1.0, le=100.0)
    max_positions: int = Field(default=5, ge=1, le=50)
    cooldown_hours: int = Field(default=24, ge=1, le=168)


class StrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    symbols: Optional[list[str]] = None
    timeframe: Optional[str] = None
    indicators_config: Optional[dict] = None
    entry_conditions: Optional[dict] = None
    exit_conditions: Optional[dict] = None
    position_sizing: Optional[dict] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_positions: Optional[int] = None
    cooldown_hours: Optional[int] = None
    is_active: Optional[bool] = None


class StrategyResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str]
    symbols: list | dict
    timeframe: str
    indicators_config: dict
    entry_conditions: dict
    exit_conditions: dict
    position_sizing: dict
    stop_loss_pct: float
    take_profit_pct: float
    max_positions: int
    cooldown_hours: int
    is_active: bool
    backtest_results: Optional[dict]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
