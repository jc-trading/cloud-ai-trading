"""
Market data API routes.
"""

from fastapi import APIRouter, Query
from app.modules.market.schemas import TickerResponse, CandleResponse
from app.modules.market.service import MarketService

router = APIRouter(prefix="/market", tags=["Market Data"])


@router.get("/tickers", response_model=list[TickerResponse])
async def get_tickers(symbols: str = Query(default=None, description="Comma-separated crypto symbols e.g. BTC/USDT,ETH/USDT")):
    """Get crypto market tickers from CoinGecko."""
    symbol_list = [s.strip() for s in symbols.split(",")] if symbols else None
    return await MarketService.get_tickers(symbol_list)


@router.get("/tickers/stocks", response_model=list[TickerResponse])
async def get_stock_tickers(symbols: str = Query(default=None, description="Comma-separated US stock symbols e.g. AAPL,MSFT")):
    """Get US stock tickers from Alpaca Data API v2."""
    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    return await MarketService.get_stock_tickers(symbol_list)


@router.get("/search")
async def search_symbols(q: str = Query(..., min_length=1)):
    """Search for available trading symbols (crypto + US stocks)."""
    results = await MarketService.search_symbols(q)
    return {"results": results}


@router.get("/search/stocks")
async def search_stock_suggestions(q: str = Query(..., min_length=1)):
    """Autocomplete: search US stocks by symbol or name, returns live price data."""
    return await MarketService.search_stock_suggestions(q)


@router.get("/search/crypto")
async def search_crypto_suggestions(q: str = Query(..., min_length=1)):
    """Autocomplete: search crypto by symbol or name, returns live price data."""
    return await MarketService.search_crypto_suggestions(q)


@router.get("/{symbol:path}/candles", response_model=list[CandleResponse])
async def get_candles(
    symbol: str,
    interval: str = Query(default="1h", pattern="^(1m|5m|15m|1h|4h|1d)$"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get OHLCV candle data for a symbol (crypto or US stock)."""
    return await MarketService.get_candles(symbol, interval, limit)


@router.get("/{symbol:path}", response_model=TickerResponse)
async def get_symbol(symbol: str):
    """Get ticker for a specific symbol (e.g., BTC/USDT or AAPL)."""
    return await MarketService.get_ticker(symbol)
