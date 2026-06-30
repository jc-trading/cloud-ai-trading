"""Market data API routes."""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.database import get_db
from app.modules.auth.models import User
from app.modules.market_data.schemas import (
    CandleWithIndicators,
    OHLCVCandleResponse,
    OHLCVHistoryRequest,
)
from app.modules.market_data.service import MarketDataService
from app.modules.watchlist.models import Watchlist
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-data", tags=["market_data"])


@router.get("/ohlcv/{watchlist_id}/{symbol}")
async def get_ohlcv_history(
    watchlist_id: UUID,
    symbol: str,
    timeframe: str = Query("1h", description="1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(100, ge=1, le=1000),
    start_time: datetime | None = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Get OHLCV candle history for a symbol."""
    try:
        # Verify user owns this watchlist
        stmt = select(Watchlist).where(
            (Watchlist.id == watchlist_id) & (Watchlist.user_id == current_user.id)
        )
        result = await session.execute(stmt)
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            raise HTTPException(status_code=404, detail="Watchlist not found")

        candles = await MarketDataService.get_ohlcv_history(
            session, watchlist_id, symbol, timeframe, limit, start_time
        )

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(candles),
            "candles": candles,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching OHLCV history: {e}")
        raise HTTPException(status_code=500, detail="Error fetching OHLCV history")


@router.get("/candle/{watchlist_id}/{symbol}")
async def get_latest_candle(
    watchlist_id: UUID,
    symbol: str,
    timeframe: str = Query("1h", description="1m, 5m, 15m, 1h, 4h, 1d"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Get latest OHLCV candle with indicators for a symbol."""
    try:
        # Verify user owns this watchlist
        stmt = select(Watchlist).where(
            (Watchlist.id == watchlist_id) & (Watchlist.user_id == current_user.id)
        )
        result = await session.execute(stmt)
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            raise HTTPException(status_code=404, detail="Watchlist not found")

        candle_with_indicators = await MarketDataService.get_candle_with_indicators(
            session, watchlist_id, symbol, timeframe
        )

        if not candle_with_indicators:
            raise HTTPException(status_code=404, detail="No candle data found")

        return candle_with_indicators.model_dump()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching latest candle: {e}")
        raise HTTPException(status_code=500, detail="Error fetching latest candle")


@router.get("/summary/{watchlist_id}")
async def get_watchlist_market_summary(
    watchlist_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Get market data summary for all items in a watchlist."""
    try:
        # Verify user owns this watchlist
        stmt = select(Watchlist).where(
            (Watchlist.id == watchlist_id) & (Watchlist.user_id == current_user.id)
        )
        result = await session.execute(stmt)
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            raise HTTPException(status_code=404, detail="Watchlist not found")

        # Get all watchlist items
        items = [item for item in watchlist.items]

        summaries = []
        for item in items:
            candle_data = await MarketDataService.get_candle_with_indicators(
                session, watchlist_id, item.symbol, "1h"
            )

            if candle_data:
                summaries.append({
                    "symbol": item.symbol,
                    "current_price": float(candle_data.candle.close),
                    "high_24h": float(candle_data.candle.high),
                    "low_24h": float(candle_data.candle.low),
                    "volume_24h": float(candle_data.candle.volume),
                    "indicators": candle_data.indicators.model_dump() if candle_data.indicators else None,
                })

        return {
            "watchlist_id": str(watchlist_id),
            "watchlist_name": watchlist.name,
            "count": len(summaries),
            "items": summaries,
            "updated_at": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching watchlist summary: {e}")
        raise HTTPException(status_code=500, detail="Error fetching watchlist summary")


@router.post("/refresh/{watchlist_id}/{symbol}")
async def refresh_market_data(
    watchlist_id: UUID,
    symbol: str,
    timeframe: str = Query("1h", description="1m, 5m, 15m, 1h, 4h, 1d"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger a refresh of market data for a symbol."""
    try:
        # Verify user owns this watchlist
        stmt = select(Watchlist).where(
            (Watchlist.id == watchlist_id) & (Watchlist.user_id == current_user.id)
        )
        result = await session.execute(stmt)
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            raise HTTPException(status_code=404, detail="Watchlist not found")

        # Trigger background task
        from app.tasks.market_data_tasks import fetch_binance_ohlcv

        task = fetch_binance_ohlcv.delay(str(watchlist_id), symbol)

        return {
            "status": "refreshing",
            "task_id": task.id,
            "symbol": symbol,
            "watchlist_id": str(watchlist_id),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering data refresh: {e}")
        raise HTTPException(status_code=500, detail="Error triggering data refresh")


@router.get("/indicators/{watchlist_id}/{symbol}")
async def get_indicators(
    watchlist_id: UUID,
    symbol: str,
    timeframe: str = Query("1h", description="1m, 5m, 15m, 1h, 4h, 1d"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Get latest technical indicators for a symbol."""
    try:
        # Verify user owns this watchlist
        stmt = select(Watchlist).where(
            (Watchlist.id == watchlist_id) & (Watchlist.user_id == current_user.id)
        )
        result = await session.execute(stmt)
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            raise HTTPException(status_code=404, detail="Watchlist not found")

        candle_data = await MarketDataService.get_candle_with_indicators(
            session, watchlist_id, symbol, timeframe
        )

        if not candle_data or not candle_data.indicators:
            raise HTTPException(status_code=404, detail="No indicator data found")

        return candle_data.indicators.model_dump()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching indicators: {e}")
        raise HTTPException(status_code=500, detail="Error fetching indicators")
