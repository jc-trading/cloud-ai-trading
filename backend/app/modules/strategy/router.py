"""
Strategy API routes.
"""

from uuid import UUID
from fastapi import APIRouter, Depends

from app.dependencies import DB, require_permission
from app.modules.auth.models import User
from app.modules.strategy.schemas import (
    StrategyCreate,
    StrategyUpdate,
    StrategyResponse,
)
from app.modules.strategy.service import StrategyService

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=list[StrategyResponse])
async def list_strategies(
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Get all strategies for current user."""
    strategies = await StrategyService.get_strategies(db, user.id)
    return [StrategyResponse.model_validate(s) for s in strategies]


@router.post("", response_model=StrategyResponse, status_code=201)
async def create_strategy(
    data: StrategyCreate,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Create a new quantitative strategy."""
    strategy = await StrategyService.create_strategy(db, user.id, data)
    return StrategyResponse.model_validate(strategy)


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: UUID,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Get a specific strategy."""
    strategy = await StrategyService.get_strategy(db, user.id, strategy_id)
    return StrategyResponse.model_validate(strategy)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: UUID,
    data: StrategyUpdate,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Update a strategy."""
    strategy = await StrategyService.update_strategy(db, user.id, strategy_id, data)
    return StrategyResponse.model_validate(strategy)


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: UUID,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Delete a strategy."""
    await StrategyService.delete_strategy(db, user.id, strategy_id)


@router.post("/{strategy_id}/toggle", response_model=StrategyResponse)
async def toggle_strategy(
    strategy_id: UUID,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """Activate or deactivate a strategy."""
    strategy = await StrategyService.toggle_active(db, user.id, strategy_id)
    return StrategyResponse.model_validate(strategy)
