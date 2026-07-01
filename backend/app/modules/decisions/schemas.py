"""
Pydantic schemas for the unified Decision feed.

Transparent payload for the dashboard: the verdict, the reasoning behind it
(claude_response), and the data it was pulled from (indicators_snapshot), so a
human can audit every call end to end.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class DecisionResponse(BaseModel):
    id: UUID
    symbol: str
    asset_class: str
    verdict: str
    verdict_reason: Optional[str]
    action: str
    confidence: int
    # Transparency: the data pulled + the AI reasoning behind the verdict.
    indicators_snapshot: dict
    claude_response: dict
    ai_invoked: bool
    data_completeness: dict
    created_at: datetime

    model_config = {"from_attributes": True}
