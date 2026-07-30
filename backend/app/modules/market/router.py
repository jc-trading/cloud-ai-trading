"""
Market data API routes — US stocks only (Direction v3; crypto plane deleted in
R1-8, crypto endpoints removed in the QA fix round).
"""

from fastapi import APIRouter, HTTPException, Query
from app.modules.market.schemas import TickerResponse, CandleResponse
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


@router.get("/{symbol:path}/candles", response_model=list[CandleResponse])
async def get_candles(
    symbol: str,
    interval: str = Query(default="1h", pattern="^(1m|5m|15m|1h|4h|1d)$"),
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
