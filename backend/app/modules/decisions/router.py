"""
Unified Decision feed API routes.

Feeds the dashboard: one transparent, auditable Decision per tracked symbol
(crypto + equity unified), plus per-symbol history. Reads the Decision fields
persisted on ai_analysis_results (migration 010).
"""

from fastapi import APIRouter, Depends, Query

from app.dependencies import DB, CurrentUser
from app.modules.auth.models import User
from app.modules.analysis.models import AssetClass
from app.modules.decisions.schemas import DecisionResponse
from app.modules.decisions.service import DecisionService

router = APIRouter(prefix="/decisions", tags=["Decisions"])


@router.get("", response_model=list[DecisionResponse])
async def list_decisions(
    user: CurrentUser,
    asset_class: AssetClass | None = Query(
        default=None, description="Filter by asset class: crypto | equity"
    ),
    db: DB = None,
):
    """Latest Decision per tracked symbol (crypto + equity unified).

    Optional ``?asset_class=crypto|equity`` narrows the feed to one class.
    """
    decisions = await DecisionService.get_latest_per_symbol(db, user.id, asset_class)
    return [DecisionResponse.model_validate(d) for d in decisions]


@router.get("/{symbol:path}", response_model=list[DecisionResponse])
async def decision_history(
    user: CurrentUser,
    symbol: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: DB = None,
):
    """Full Decision history for one symbol, newest-first."""
    decisions = await DecisionService.get_history(db, user.id, symbol, limit)
    return [DecisionResponse.model_validate(d) for d in decisions]
