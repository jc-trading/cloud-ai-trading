"""Risk management API routes - P3 Phase 3A."""

from uuid import UUID
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import DB, require_permission
from app.modules.auth.models import User
from app.modules.risk.engine import RiskEngine
from app.modules.risk.models import RiskLimit

router = APIRouter(prefix="/risk", tags=["Risk Management"])


# Request/Response Models
class RiskLimitUpdate(BaseModel):
    """Request model for updating risk limits."""

    max_position_size_percent: float = 5.0
    max_loss_per_trade_percent: float = 2.0
    max_portfolio_loss_percent: float = 10.0
    daily_loss_limit_percent: float = 3.0
    max_open_positions: int = 10
    max_concentration_percent: float = 30.0
    min_signal_strength: int = 50
    min_confidence_threshold: float = 60.0
    max_position_age_days: int = 7
    max_consecutive_losses: int = 3
    risk_level: str = "medium"  # low, medium, high
    position_sizing_method: str = "risk_weighted"


class RiskLimitResponse(BaseModel):
    """Response model for risk limits."""

    watchlist_id: UUID
    max_position_size_percent: float
    max_loss_per_trade_percent: float
    max_portfolio_loss_percent: float
    daily_loss_limit_percent: float
    max_open_positions: int
    max_concentration_percent: float
    min_signal_strength: int
    min_confidence_threshold: float
    risk_level: str
    position_sizing_method: str


class PositionSizingRequest(BaseModel):
    """Request model for position sizing calculation."""

    symbol: str
    entry_price: Decimal
    signal_strength: int  # 0-100
    account_equity: Decimal


class PositionSizingResponse(BaseModel):
    """Response model for position sizing."""

    symbol: str
    position_size: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    max_loss: Decimal
    risk_reward_ratio: Decimal
    reason: str


class PositionValidationRequest(BaseModel):
    """Request model for position validation."""

    symbol: str
    position_size: Decimal


class PositionValidationResponse(BaseModel):
    """Response model for position validation."""

    is_allowed: bool
    reason: str


@router.get("/limits/{watchlist_id}", response_model=RiskLimitResponse)
async def get_risk_limits(
    watchlist_id: UUID,
    user: User = Depends(require_permission("risk_management")),
    db: DB = None,
):
    """
    Get current risk limits for a watchlist.

    Args:
        watchlist_id: ID of watchlist
        user: Current authenticated user
        db: Database session

    Returns:
        RiskLimitResponse with current limits
    """
    # TODO: Verify user has access to this watchlist

    from sqlalchemy import select

    stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist_id)
    result = await db.execute(stmt)
    risk_limit = result.scalar_one_or_none()

    if not risk_limit:
        raise HTTPException(status_code=404, detail="Risk limits not found")

    return RiskLimitResponse(
        watchlist_id=risk_limit.watchlist_id,
        max_position_size_percent=float(risk_limit.max_position_size_percent),
        max_loss_per_trade_percent=float(risk_limit.max_loss_per_trade_percent),
        max_portfolio_loss_percent=float(risk_limit.max_portfolio_loss_percent),
        daily_loss_limit_percent=float(risk_limit.daily_loss_limit_percent),
        max_open_positions=risk_limit.max_open_positions,
        max_concentration_percent=float(risk_limit.max_concentration_percent),
        min_signal_strength=risk_limit.min_signal_strength,
        min_confidence_threshold=float(risk_limit.min_confidence_threshold),
        risk_level=risk_limit.risk_level,
        position_sizing_method=risk_limit.position_sizing_method,
    )


@router.patch("/limits/{watchlist_id}")
async def update_risk_limits(
    watchlist_id: UUID,
    request: RiskLimitUpdate,
    user: User = Depends(require_permission("risk_management")),
    db: DB = None,
):
    """
    Update risk limits for a watchlist.

    Args:
        watchlist_id: ID of watchlist
        request: Risk limit update parameters
        user: Current authenticated user
        db: Database session

    Returns:
        Updated RiskLimitResponse
    """
    # TODO: Verify user has access to this watchlist

    from sqlalchemy import select

    stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist_id)
    result = await db.execute(stmt)
    risk_limit = result.scalar_one_or_none()

    if not risk_limit:
        # Create new risk limit
        risk_limit = RiskLimit(
            watchlist_id=watchlist_id,
            max_position_size_percent=Decimal(str(request.max_position_size_percent)),
            max_loss_per_trade_percent=Decimal(str(request.max_loss_per_trade_percent)),
            max_portfolio_loss_percent=Decimal(str(request.max_portfolio_loss_percent)),
            daily_loss_limit_percent=Decimal(str(request.daily_loss_limit_percent)),
            max_open_positions=request.max_open_positions,
            max_concentration_percent=Decimal(str(request.max_concentration_percent)),
            min_signal_strength=request.min_signal_strength,
            min_confidence_threshold=Decimal(str(request.min_confidence_threshold)),
            risk_level=request.risk_level,
            position_sizing_method=request.position_sizing_method,
        )
        db.add(risk_limit)
    else:
        # Update existing
        risk_limit.max_position_size_percent = Decimal(str(request.max_position_size_percent))
        risk_limit.max_loss_per_trade_percent = Decimal(str(request.max_loss_per_trade_percent))
        risk_limit.max_portfolio_loss_percent = Decimal(str(request.max_portfolio_loss_percent))
        risk_limit.daily_loss_limit_percent = Decimal(str(request.daily_loss_limit_percent))
        risk_limit.max_open_positions = request.max_open_positions
        risk_limit.max_concentration_percent = Decimal(str(request.max_concentration_percent))
        risk_limit.min_signal_strength = request.min_signal_strength
        risk_limit.min_confidence_threshold = Decimal(str(request.min_confidence_threshold))
        risk_limit.risk_level = request.risk_level
        risk_limit.position_sizing_method = request.position_sizing_method

    await db.flush()

    return RiskLimitResponse(
        watchlist_id=risk_limit.watchlist_id,
        max_position_size_percent=float(risk_limit.max_position_size_percent),
        max_loss_per_trade_percent=float(risk_limit.max_loss_per_trade_percent),
        max_portfolio_loss_percent=float(risk_limit.max_portfolio_loss_percent),
        daily_loss_limit_percent=float(risk_limit.daily_loss_limit_percent),
        max_open_positions=risk_limit.max_open_positions,
        max_concentration_percent=float(risk_limit.max_concentration_percent),
        min_signal_strength=risk_limit.min_signal_strength,
        min_confidence_threshold=float(risk_limit.min_confidence_threshold),
        risk_level=risk_limit.risk_level,
        position_sizing_method=risk_limit.position_sizing_method,
    )


@router.post("/{watchlist_id}/position-size", response_model=PositionSizingResponse)
async def calculate_position_size(
    watchlist_id: UUID,
    request: PositionSizingRequest,
    user: User = Depends(require_permission("risk_management")),
    db: DB = None,
):
    """
    Calculate optimal position size for a new trade.

    Args:
        watchlist_id: ID of watchlist
        request: Position sizing parameters
        user: Current authenticated user
        db: Database session

    Returns:
        PositionSizingResponse with size, SL, TP
    """
    # TODO: Verify user has access to this watchlist
    # TODO: Get current positions for concentration check

    try:
        recommendation = await RiskEngine.calculate_position_size(
            session=db,
            watchlist_id=str(watchlist_id),
            symbol=request.symbol,
            entry_price=request.entry_price,
            signal_strength=request.signal_strength,
            account_equity=request.account_equity,
            current_positions={},  # TODO: Fetch from database
        )

        return PositionSizingResponse(
            symbol=request.symbol,
            position_size=recommendation.position_size,
            stop_loss_price=recommendation.stop_loss_price,
            take_profit_price=recommendation.take_profit_price,
            max_loss=recommendation.max_loss,
            risk_reward_ratio=recommendation.risk_reward_ratio,
            reason=recommendation.reason,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Position sizing failed: {str(e)}")


@router.post("/{watchlist_id}/validate-position", response_model=PositionValidationResponse)
async def validate_position(
    watchlist_id: UUID,
    request: PositionValidationRequest,
    user: User = Depends(require_permission("risk_management")),
    db: DB = None,
):
    """
    Validate if a position can be opened.

    Args:
        watchlist_id: ID of watchlist
        request: Position validation parameters
        user: Current authenticated user
        db: Database session

    Returns:
        PositionValidationResponse with allowed flag and reason
    """
    # TODO: Verify user has access to this watchlist
    # TODO: Get account equity
    # TODO: Get current positions

    try:
        is_allowed, reason = await RiskEngine.validate_new_position(
            session=db,
            watchlist_id=str(watchlist_id),
            symbol=request.symbol,
            position_size=request.position_size,
            account_equity=Decimal("100000"),  # TODO: Get from account
            current_positions={},  # TODO: Fetch from database
        )

        return PositionValidationResponse(
            is_allowed=is_allowed,
            reason=reason,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Position validation failed: {str(e)}")
