"""
Strategy Backtesting API routes - P2 implementation.
Endpoints for running backtest and retrieving results.
"""

from uuid import UUID
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import DB, require_permission
from app.modules.auth.models import User
from app.modules.strategy.service import StrategyService
from app.modules.analysis.backtester import StrategyBacktester

router = APIRouter(prefix="/strategies", tags=["Strategies"])


class BacktestRequest(BaseModel):
    """Request model for strategy backtest."""
    symbol: str  # e.g., "BTCUSDT"
    days_back: int = 30  # How many days back to test


class BacktestResponse(BaseModel):
    """Backtest result response."""
    strategy_id: UUID
    symbol: str
    start_date: datetime
    end_date: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    total_return: float


def _strategy_to_dict(strategy: object) -> dict:
    """Normalize ORM/Pydantic strategy objects to a dictionary for backtesting."""
    if isinstance(strategy, dict):
        return strategy
    if hasattr(strategy, "model_dump"):
        return strategy.model_dump()
    if hasattr(strategy, "dict"):
        return strategy.dict()
    return {
        column.name: getattr(strategy, column.name)
        for column in strategy.__table__.columns
        if hasattr(strategy, column.name)
    }


@router.post("/{strategy_id}/backtest", response_model=BacktestResponse)
async def run_backtest(
    strategy_id: UUID,
    request: BacktestRequest,
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """
    Run backtest for a strategy against historical data.

    Args:
        strategy_id: ID of strategy to test
        request: Backtest parameters (symbol, days_back)
        user: Current authenticated user
        db: Database session

    Returns:
        BacktestResponse with performance metrics
    """

    # 1. Get strategy
    strategy = await StrategyService.get_strategy(db, user.id, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # 2. Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=request.days_back)

    # 3. Run backtest
    try:
        strategy_config = _strategy_to_dict(strategy)
        initial_capital = Decimal(str(strategy_config.get("initial_capital", 10000)))

        result = await StrategyBacktester.backtest_strategy(
            db=db,
            watchlist_id=strategy_config.get("watchlist_id", ""),  # Or derive from user
            symbol=request.symbol,
            strategy=strategy_config,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
        )

        # 4. Update strategy with backtest results
        backtest_results = {
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "total_return": result.total_return,
            "total_trades": result.total_trades,
            "last_backtest_at": datetime.utcnow().isoformat(),
        }

        # Note: Update the backtest_results in the strategy model
        # This would require updating the service to handle this

        return BacktestResponse(
            strategy_id=strategy_id,
            symbol=request.symbol,
            start_date=start_date,
            end_date=end_date,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            total_return=result.total_return,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")


@router.get("/{strategy_id}/compare")
async def compare_strategies(
    user: User = Depends(require_permission("quant_strategies")),
    db: DB = None,
):
    """
    Compare all user's active strategies based on backtest results.

    Returns list of strategies sorted by Sharpe ratio (best risk-adjusted return first).
    """

    # 1. Get all user strategies with backtest results
    strategies = await StrategyService.get_strategies(db, user.id)

    if not strategies:
        raise HTTPException(status_code=404, detail="No strategies found")

    # 2. Extract backtest results
    backtest_data = {}
    for strategy in strategies:
        if strategy.backtest_results:
            backtest_data[str(strategy.id)] = strategy.backtest_results

    # 3. Compare strategies
    from app.modules.strategy.engine import QuantStrategyEngine

    comparison = QuantStrategyEngine.compare_strategies(
        strategies=[s.model_dump() for s in strategies],
        backtest_results=backtest_data,
    )

    return {
        "comparison": comparison,
        "count": len(comparison),
        "best_strategy": comparison[0] if comparison else None,
    }
