"""
Market data API routes — US stocks only (Direction v3; crypto plane deleted in
R1-8, crypto endpoints removed in the QA fix round).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import AdminUser, DB, require_permission
from app.modules.auth.models import User
from app.modules.market.schemas import (
    BarsResponse,
    CandleResponse,
    StreamSymbolCreate,
    StreamSymbolResponse,
    TickerResponse,
)
from app.modules.market.service import MarketService

router = APIRouter(prefix="/market", tags=["Market Data"])


@router.get("/tickers/stocks", response_model=list[TickerResponse])
async def get_stock_tickers(symbols: str = Query(default=None, description="Comma-separated US stock symbols e.g. AAPL,MSFT")):
    """Get US stock tickers (Alpaca candles + Finnhub real-time quotes)."""
    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    return await MarketService.get_stock_tickers(symbol_list)


@router.get("/search/stocks")
async def search_stock_suggestions(q: str = Query(..., min_length=1)):
    """Autocomplete: search US stocks by symbol or name, returns live price data."""
    return await MarketService.search_stock_suggestions(q)


@router.get("/stream-symbols", response_model=list[StreamSymbolResponse])
async def list_stream_symbols(admin: AdminUser, db: DB):
    """Realtime 1min subscription set (market-stream picks changes up within 5 min)."""
    return await MarketService.list_stream_symbols(db)


@router.post("/stream-symbols", response_model=StreamSymbolResponse, status_code=201)
async def upsert_stream_symbol(data: StreamSymbolCreate, admin: AdminUser, db: DB):
    """Add a symbol to the subscription set, or update/re-enable an existing one."""
    return await MarketService.upsert_stream_symbol(db, data.symbol, data.priority, data.note)


@router.delete("/stream-symbols/{symbol}", response_model=StreamSymbolResponse)
async def disable_stream_symbol(symbol: str, admin: AdminUser, db: DB):
    """Stop subscribing to a symbol. The row is kept (enabled=false) so its
    priority/note survive a re-enable and the history stays visible."""
    return await MarketService.disable_stream_symbol(db, symbol)


@router.get("/{symbol:path}/bars", response_model=BarsResponse)
async def get_bars(
    symbol: str,
    db: DB,
    timeframe: str = Query(default="daily", pattern="^(1min|1hour|daily)$"),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    user: User = Depends(require_permission("view_market_data")),
):
    """Raw stored bars for a symbol, with the provenance of the newest file."""
    if "/" in symbol:
        raise HTTPException(status_code=422, detail="crypto pairs are not supported")
    return await MarketService.get_bars(db, symbol, timeframe, start, end, limit)


@router.get("/{symbol:path}/candles", response_model=list[CandleResponse])
async def get_candles(
    symbol: str,
    interval: str = Query(default="1h", pattern="^(1m|5m|15m|1h|1d)$"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """OHLCV candles for a US stock symbol."""
    if "/" in symbol:
        raise HTTPException(status_code=422, detail="crypto pairs are not supported")
    return await MarketService.get_stock_candles(symbol, interval, limit)


@router.get("/{symbol:path}", response_model=TickerResponse)
async def get_symbol(symbol: str):
    """Ticker for one US stock symbol (e.g. AAPL)."""
    if "/" in symbol:
        raise HTTPException(status_code=422, detail="crypto pairs are not supported")
    return await MarketService.get_ticker(symbol)
