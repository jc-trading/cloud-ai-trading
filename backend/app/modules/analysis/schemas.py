"""
Pydantic schemas for AI analysis.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=50)
    exchange_type: str = Field(default="binance")
    strategy_id: Optional[UUID] = None


class AnalysisResponse(BaseModel):
    id: UUID
    user_id: UUID
    symbol: str
    exchange_type: str
    analysis_type: str
    indicators_snapshot: dict
    claude_response: dict
    action: str
    confidence: int
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward_ratio: Optional[float]
    tokens_used: int
    api_cost: float
    # --- Decision fields (migration 010) ---
    asset_class: str
    verdict: str
    verdict_reason: Optional[str]
    data_completeness: dict
    ai_invoked: bool
    ai_skip_reason: Optional[str]
    position_id: Optional[UUID]
    created_at: datetime
    model_config = {"from_attributes": True}


class AnalysisSummary(BaseModel):
    total_analyses: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    avg_confidence: float = 0.0
