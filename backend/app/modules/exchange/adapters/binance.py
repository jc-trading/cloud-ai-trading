"""
Binance exchange adapter using CCXT.
"""

import logging
from typing import Optional

import ccxt.async_support as ccxt

from app.modules.exchange.adapters.base import (
    ExchangeAdapter,
    OrderRequest,
    OrderResult,
)

logger = logging.getLogger("cloud_ai_trading.binance")


class BinanceAdapter(ExchangeAdapter):
    """Binance exchange integration via CCXT."""

    def __init__(self, api_key: str, api_secret: str):
        self.exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
            }
        )

    async def _close(self):
        """Close the exchange connection."""
        await self.exchange.close()

    async def test_connection(self) -> bool:
        try:
            await self.exchange.fetch_balance()
            return True
        except Exception as e:
            logger.error(f"Binance connection test failed: {e}")
            return False

    async def get_balance(self) -> dict:
        try:
            balance = await self.exchange.fetch_balance()
            # Filter only non-zero balances
            result = {}
            for currency, data in balance.get("total", {}).items():
                if data and float(data) > 0:
                    result[currency] = float(data)
            return result
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            raise

    async def get_ticker(self, symbol: str) -> dict:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {
                "symbol": ticker["symbol"],
                "last": ticker["last"],
                "bid": ticker["bid"],
                "ask": ticker["ask"],
                "high": ticker["high"],
                "low": ticker["low"],
                "volume": ticker["baseVolume"],
                "quote_volume": ticker["quoteVolume"],
                "change_24h": ticker["percentage"],
                "timestamp": ticker["timestamp"],
            }
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            raise

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[dict]:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, interval, limit=limit)
            return [
                {
                    "timestamp": candle[0],
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": candle[5],
                }
                for candle in ohlcv
            ]
        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
            raise

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        try:
            book = await self.exchange.fetch_order_book(symbol, limit)
            return {
                "bids": book["bids"][:limit],
                "asks": book["asks"][:limit],
                "timestamp": book.get("timestamp"),
            }
        except Exception as e:
            logger.error(f"Failed to fetch order book for {symbol}: {e}")
            raise

    async def place_order(self, order: OrderRequest) -> OrderResult:
        try:
            params = {}
            if order.order_type == "market":
                result = await self.exchange.create_order(
                    symbol=order.symbol,
                    type="market",
                    side=order.side,
                    amount=order.quantity,
                    params=params,
                )
            elif order.order_type == "limit":
                result = await self.exchange.create_order(
                    symbol=order.symbol,
                    type="limit",
                    side=order.side,
                    amount=order.quantity,
                    price=order.price,
                    params=params,
                )
            elif order.order_type == "stop_limit":
                params["stopPrice"] = order.stop_price
                result = await self.exchange.create_order(
                    symbol=order.symbol,
                    type="limit",
                    side=order.side,
                    amount=order.quantity,
                    price=order.price,
                    params=params,
                )
            else:
                return OrderResult(
                    success=False, message=f"Unsupported order type: {order.order_type}"
                )

            return OrderResult(
                success=True,
                order_id=result.get("id"),
                filled_price=result.get("average"),
                filled_quantity=result.get("filled"),
                fee=result.get("fee", {}).get("cost"),
                message="Order placed successfully",
            )
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return OrderResult(success=False, message=str(e))

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        try:
            orders = await self.exchange.fetch_open_orders(symbol)
            return [
                {
                    "id": o["id"],
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "type": o["type"],
                    "price": o["price"],
                    "amount": o["amount"],
                    "filled": o["filled"],
                    "status": o["status"],
                    "timestamp": o["timestamp"],
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            raise

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        try:
            orders = await self.exchange.fetch_closed_orders(symbol, limit=limit)
            return [
                {
                    "id": o["id"],
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "type": o["type"],
                    "price": o["price"],
                    "average": o.get("average"),
                    "amount": o["amount"],
                    "filled": o["filled"],
                    "fee": o.get("fee"),
                    "status": o["status"],
                    "timestamp": o["timestamp"],
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"Failed to fetch order history: {e}")
            raise

    async def get_available_symbols(self) -> list[str]:
        try:
            await self.exchange.load_markets()
            return list(self.exchange.symbols)
        except Exception as e:
            logger.error(f"Failed to fetch symbols: {e}")
            raise
