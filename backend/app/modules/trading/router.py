"""
Trading API routes.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Query

from app.dependencies import DB, require_permission
from app.modules.auth.models import User
from app.modules.trading.schemas import (
    TradeCreate,
    TradeResponse,
    TradeFilter,
    SimulatePortfolioResponse,
    TradeSignalResponse,
    TradeSummary,
)
from app.modules.trading.service import TradingService

router = APIRouter(prefix="/trading", tags=["Trading"])


# ── Trades ───────────────────────────────────────────────────────────

@router.post("/trades", response_model=TradeResponse, status_code=201)
async def place_trade(
    data: TradeCreate,
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Place a trade (live or simulated)."""
    if data.trading_mode == "live":
        from app.modules.auth.rbac import ROLE_PERMISSIONS
        if "live_trading" not in ROLE_PERMISSIONS.get(user.role, set()):
            from app.core.exceptions import PermissionDeniedException
            raise PermissionDeniedException("Live trading not permitted for your role")

    trade = await TradingService.place_trade(db, user.id, data)
    return TradeResponse.model_validate(trade)


@router.get("/trades", response_model=list[TradeResponse])
async def list_trades(
    symbol: str | None = None,
    side: str | None = None,
    status: str | None = None,
    trading_mode: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """List user's trades with filters."""
    filters = TradeFilter(
        symbol=symbol, side=side, status=status,
        trading_mode=trading_mode, limit=limit, offset=offset,
    )
    trades = await TradingService.get_trades(db, user.id, filters)
    return [TradeResponse.model_validate(t) for t in trades]


@router.get("/trades/summary", response_model=TradeSummary)
async def trade_summary(
    trading_mode: str | None = None,
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get trade summary statistics."""
    return await TradingService.get_trade_summary(db, user.id, trading_mode)


@router.get("/trades/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: UUID,
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get a specific trade."""
    trade = await TradingService.get_trade(db, user.id, trade_id)
    return TradeResponse.model_validate(trade)


@router.post("/trades/{trade_id}/close", response_model=TradeResponse)
async def close_trade(
    trade_id: UUID,
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Close a simulated trade position."""
    trade = await TradingService.close_trade(db, user.id, trade_id)
    return TradeResponse.model_validate(trade)


# ── Simulate Portfolio ───────────────────────────────────────────────

@router.get("/portfolio/simulate", response_model=SimulatePortfolioResponse)
async def get_simulate_portfolio(
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get simulate trading portfolio."""
    return await TradingService.get_simulate_portfolio(db, user.id)


@router.post("/portfolio/simulate/reset", response_model=SimulatePortfolioResponse)
async def reset_simulate_portfolio(
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Reset simulate portfolio to initial balance."""
    return await TradingService.reset_simulate_portfolio(db, user.id)


@router.get("/portfolio/stats", response_model=TradeSummary)
async def portfolio_stats(
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get portfolio summary statistics."""
    return await TradingService.get_trade_summary(db, user.id, trading_mode="simulate")


# ── Signals ──────────────────────────────────────────────────────────

@router.get("/signals", response_model=list[TradeSignalResponse])
async def list_signals(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get recent trade signals."""
    signals = await TradingService.get_signals(db, user.id, limit)
    return [TradeSignalResponse.model_validate(s) for s in signals]


@router.get("/signals/{symbol}", response_model=list[TradeSignalResponse])
async def list_signals_by_symbol(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_permission("simulate_trading")),
    db: DB = None,
):
    """Get recent trade signals for a symbol."""
    signals = await TradingService.get_signals_by_symbol(db, user.id, symbol, limit)
    return [TradeSignalResponse.model_validate(s) for s in signals]
