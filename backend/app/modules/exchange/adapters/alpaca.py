"""
Alpaca exchange adapter for US stocks.
Uses Alpaca REST API v2 directly (no CCXT — better support for stocks).

Paper trading: https://paper-api.alpaca.markets
Live trading:  https://api.alpaca.markets
Market data:   https://data.alpaca.markets
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.modules.exchange.adapters.base import (
    ExchangeAdapter,
    OrderRequest,
    OrderResult,
)

logger = logging.getLogger("cloud_ai_trading.alpaca")

# ── Interval mapping ──────────────────────────────────────────────
INTERVAL_MAP = {
    "1m":  "1Min",
    "5m":  "5Min",
    "15m": "15Min",
    "1h":  "1Hour",
    "4h":  "4Hour",
    "1d":  "1Day",
}

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"


class AlpacaAdapter(ExchangeAdapter):
    """Alpaca Markets integration for US stocks (paper + live)."""

    def __init__(self, api_key: str, api_secret: str, paper: Optional[bool] = None):
        # paper=None → follow the system-wide ALPACA_MODE (.env). Callers with
        # their own mode source still pass it explicitly: the exchange service
        # from the connection's trading_mode, execution FORCING paper=True.
        if paper is None:
            from app.config import settings
            paper = settings.ALPACA_MODE != "live"
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.trading_url = ALPACA_PAPER_URL if paper else ALPACA_LIVE_URL
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0, headers=self.headers)
        return self._client

    async def _close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Account / Auth ────────────────────────────────────────────

    async def test_connection(self) -> bool:
        try:
            client = self._get_client()
            resp = await client.get(f"{self.trading_url}/v2/account")
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Alpaca connection test failed: {e}")
            return False

    async def get_balance(self) -> dict:
        """Return account equity/cash from Alpaca account endpoint."""
        try:
            client = self._get_client()
            resp = await client.get(f"{self.trading_url}/v2/account")
            resp.raise_for_status()
            acct = resp.json()
            return {
                "USD": float(acct.get("cash", 0)),
                "buying_power": float(acct.get("buying_power", 0)),
                "equity": float(acct.get("equity", 0)),
                "portfolio_value": float(acct.get("portfolio_value", 0)),
            }
        except Exception as e:
            logger.error(f"Alpaca get_balance failed: {e}")
            raise

    # ── Market Data ───────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> dict:
        """Get latest quote + bar for a single US stock symbol."""
        try:
            client = self._get_client()
            # Use snapshot endpoint — returns quote + bar + trade in one call
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                params={"symbols": symbol, "feed": "iex"},
            )
            resp.raise_for_status()
            data = resp.json()
            snap = data.get(symbol)
            if not snap:
                raise ValueError(f"No snapshot data for {symbol}")
            return _format_snapshot(symbol, snap)
        except Exception as e:
            logger.error(f"Alpaca get_ticker failed for {symbol}: {e}")
            raise

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[dict]:
        """Get OHLCV bars for a US stock symbol."""
        try:
            timeframe = INTERVAL_MAP.get(interval, "1Hour")
            client = self._get_client()
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                params={
                    "symbols": symbol,
                    "timeframe": timeframe,
                    "limit": limit,
                    "feed": "iex",
                    "sort": "desc",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            bars = data.get("bars", {}).get(symbol, [])
            # Sort ascending (oldest first)
            bars = sorted(bars, key=lambda b: b["t"])
            return [
                {
                    "timestamp": int(
                        datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000
                    ),
                    "open": b["o"],
                    "high": b["h"],
                    "low": b["l"],
                    "close": b["c"],
                    "volume": b["v"],
                }
                for b in bars
            ]
        except Exception as e:
            logger.error(f"Alpaca get_candles failed for {symbol}: {e}")
            return []

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Alpaca doesn't expose a full order book — return latest quote as bid/ask."""
        try:
            client = self._get_client()
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/quotes/latest",
                params={"symbols": symbol, "feed": "iex"},
            )
            resp.raise_for_status()
            data = resp.json()
            q = data.get("quotes", {}).get(symbol, {})
            return {
                "bids": [[q.get("bp", 0), q.get("bs", 0)]],
                "asks": [[q.get("ap", 0), q.get("as", 0)]],
                "timestamp": None,
            }
        except Exception as e:
            logger.error(f"Alpaca order book failed: {e}")
            return {"bids": [], "asks": [], "timestamp": None}

    # ── Trading ───────────────────────────────────────────────────

    async def place_order(self, order: OrderRequest) -> OrderResult:
        try:
            client = self._get_client()
            payload: dict = {
                "symbol": order.symbol,
                "qty": str(order.quantity),
                "side": order.side,
                "type": order.order_type if order.order_type != "stop_limit" else "stop_limit",
                "time_in_force": "day",
            }
            if order.order_type == "limit":
                payload["limit_price"] = str(order.price)
            elif order.order_type == "stop_limit":
                payload["limit_price"] = str(order.price)
                payload["stop_price"] = str(order.stop_price)

            resp = await client.post(
                f"{self.trading_url}/v2/orders", json=payload
            )
            resp.raise_for_status()
            result = resp.json()
            return OrderResult(
                success=True,
                order_id=result.get("id"),
                filled_price=float(result.get("filled_avg_price") or 0) or None,
                filled_quantity=float(result.get("filled_qty") or 0) or None,
                fee=0.0,  # Alpaca is commission-free
                message="Order placed successfully",
            )
        except Exception as e:
            logger.error(f"Alpaca place_order failed: {e}")
            return OrderResult(success=False, message=str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            client = self._get_client()
            resp = await client.delete(f"{self.trading_url}/v2/orders/{order_id}")
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Alpaca cancel_order failed: {e}")
            return False

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        try:
            client = self._get_client()
            params: dict = {"status": "open", "limit": 100}
            if symbol:
                params["symbols"] = symbol
            resp = await client.get(f"{self.trading_url}/v2/orders", params=params)
            resp.raise_for_status()
            orders = resp.json()
            return [_format_order(o) for o in orders]
        except Exception as e:
            logger.error(f"Alpaca get_open_orders failed: {e}")
            return []

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        try:
            client = self._get_client()
            params: dict = {"status": "closed", "limit": limit}
            if symbol:
                params["symbols"] = symbol
            resp = await client.get(f"{self.trading_url}/v2/orders", params=params)
            resp.raise_for_status()
            orders = resp.json()
            return [_format_order(o) for o in orders]
        except Exception as e:
            logger.error(f"Alpaca get_order_history failed: {e}")
            return []

    async def get_available_symbols(self) -> list[str]:
        """Get all tradable US stock symbols from Alpaca assets."""
        try:
            client = self._get_client()
            resp = await client.get(
                f"{ALPACA_LIVE_URL}/v2/assets",
                params={"status": "active", "asset_class": "us_equity"},
            )
            resp.raise_for_status()
            assets = resp.json()
            return [a["symbol"] for a in assets if a.get("tradable")]
        except Exception as e:
            logger.error(f"Alpaca get_available_symbols failed: {e}")
            return []

    # ── Bulk market data (for market overview page) ───────────────

    async def get_bulk_snapshots(self, symbols: list[str]) -> list[dict]:
        """Get snapshots for multiple symbols in one API call."""
        try:
            client = self._get_client()
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                params={"symbols": ",".join(symbols), "feed": "iex"},
            )
            resp.raise_for_status()
            data = resp.json()
            result = []
            for sym in symbols:
                snap = data.get(sym)
                if snap:
                    result.append(_format_snapshot(sym, snap))
            return result
        except Exception as e:
            logger.error(f"Alpaca bulk snapshots failed: {e}")
            return []


# ── Formatters ────────────────────────────────────────────────────

def _format_snapshot(symbol: str, snap: dict) -> dict:
    """Map Alpaca snapshot → our TickerResponse shape."""
    daily_bar = snap.get("dailyBar") or {}
    latest_trade = snap.get("latestTrade") or {}
    latest_quote = snap.get("latestQuote") or {}
    prev_daily_bar = snap.get("prevDailyBar") or {}

    last_price = float(latest_trade.get("p") or daily_bar.get("c") or 0)
    prev_close = float(prev_daily_bar.get("c") or 0)
    change_24h = (
        ((last_price - prev_close) / prev_close * 100) if prev_close else None
    )

    return {
        "symbol": symbol,
        "last": last_price,
        "bid": float(latest_quote.get("bp") or 0) or None,
        "ask": float(latest_quote.get("ap") or 0) or None,
        "high": float(daily_bar.get("h") or 0),
        "low": float(daily_bar.get("l") or 0),
        "volume": float(daily_bar.get("v") or 0),
        "quote_volume": None,
        "change_24h": round(change_24h, 4) if change_24h is not None else None,
        "timestamp": None,
        "market_type": "stock",
    }


def _format_order(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "symbol": o.get("symbol"),
        "side": o.get("side"),
        "type": o.get("type"),
        "price": float(o.get("limit_price") or 0) or None,
        "amount": float(o.get("qty") or 0),
        "filled": float(o.get("filled_qty") or 0),
        "status": o.get("status"),
        "timestamp": o.get("submitted_at"),
    }
