"""Binance WebSocket client for real-time price streaming and OHLCV data collection."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set

import aiohttp
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)


class BinanceWebSocketClient:
    """Real-time WebSocket client for Binance."""

    BASE_WS_URL = "wss://stream.binance.com:9443/ws"
    REST_API_URL = "https://api.binance.com/api"
    PUBLIC_REST_URLS = [
        "https://data-api.binance.vision/api",
        "https://api.binance.com/api",
    ]

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        """Initialize Binance WebSocket client."""
        self.api_key = api_key
        self.api_secret = api_secret
        # Public market data is fetched via aiohttp to avoid python-binance's
        # startup ping against api.binance.com, which is blocked in some regions.
        self.rest_client = (
            BinanceClient(api_key=api_key, api_secret=api_secret)
            if api_key
            else None
        )

        self.ws_session: Optional[aiohttp.ClientSession] = None
        self.ws_connection: Optional[aiohttp.ClientWebSocketResponse] = None

        self.subscribed_symbols: Set[str] = set()
        self.price_callbacks: List[Callable[[Dict], None]] = []
        self.kline_callbacks: List[Callable[[Dict], None]] = []

        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1.0

    async def connect(self) -> None:
        """Connect to Binance WebSocket."""
        try:
            self.ws_session = aiohttp.ClientSession()
            logger.info("Binance WebSocket client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket session: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Binance WebSocket."""
        if self.ws_connection:
            await self.ws_connection.close()
        if self.ws_session:
            await self.ws_session.close()
        logger.info("Binance WebSocket client disconnected")

    async def subscribe_ticker(self, symbol: str) -> None:
        """Subscribe to real-time price updates for a symbol."""
        if not symbol or symbol in self.subscribed_symbols:
            return

        stream = f"{symbol.lower()}@ticker"
        await self._subscribe_stream(stream)
        self.subscribed_symbols.add(symbol)
        logger.info(f"Subscribed to ticker: {symbol}")

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        """Subscribe to kline (candlestick) data for a symbol."""
        if not symbol:
            return

        stream = f"{symbol.lower()}@kline_{interval}"
        await self._subscribe_stream(stream)
        self.subscribed_symbols.add(symbol)
        logger.info(f"Subscribed to kline: {symbol} {interval}")

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from all streams for a symbol."""
        if symbol not in self.subscribed_symbols:
            return

        self.subscribed_symbols.discard(symbol)
        await self._update_subscriptions()
        logger.info(f"Unsubscribed from: {symbol}")

    async def subscribe_multiple_tickers(self, symbols: List[str]) -> None:
        """Subscribe to multiple ticker streams."""
        for symbol in symbols:
            await self.subscribe_ticker(symbol)

    async def _subscribe_stream(self, stream: str) -> None:
        """Subscribe to a specific stream."""
        if not self.ws_connection or self.ws_connection.closed:
            await self._establish_connection()

        subscription = {"method": "SUBSCRIBE", "params": [stream], "id": 1}
        try:
            await self.ws_connection.send_json(subscription)
        except Exception as e:
            logger.error(f"Failed to subscribe to stream {stream}: {e}")
            await self._handle_reconnect()

    async def _establish_connection(self) -> None:
        """Establish WebSocket connection with all current subscriptions."""
        if not self.ws_session:
            await self.connect()

        try:
            self.ws_connection = await self.ws_session.ws_connect(self.BASE_WS_URL)
            self.reconnect_attempts = 0
            logger.info("WebSocket connection established")

            # Resubscribe to all symbols
            await self._update_subscriptions()
        except Exception as e:
            logger.error(f"Failed to establish WebSocket connection: {e}")
            await self._handle_reconnect()

    async def _update_subscriptions(self) -> None:
        """Update all subscriptions on the current connection."""
        if not self.ws_connection or self.ws_connection.closed:
            return

        streams = [f"{symbol.lower()}@ticker" for symbol in self.subscribed_symbols]
        if not streams:
            return

        subscription = {"method": "SUBSCRIBE", "params": streams, "id": 1}
        try:
            await self.ws_connection.send_json(subscription)
        except Exception as e:
            logger.error(f"Failed to update subscriptions: {e}")

    async def _handle_reconnect(self) -> None:
        """Handle reconnection logic with exponential backoff."""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return

        self.reconnect_attempts += 1
        wait_time = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 60)
        logger.warning(f"Reconnecting in {wait_time}s (attempt {self.reconnect_attempts})")

        await asyncio.sleep(wait_time)
        await self._establish_connection()

    async def listen(self) -> None:
        """Listen to WebSocket messages."""
        try:
            if not self.ws_connection or self.ws_connection.closed:
                await self._establish_connection()

            async for message in self.ws_connection:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(message.data)
                        await self._process_message(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse message: {e}")
                elif message.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {message}")
                    await self._handle_reconnect()
                    break
                elif message.type == aiohttp.WSMsgType.CLOSED:
                    logger.warning("WebSocket connection closed")
                    await self._handle_reconnect()
                    break

        except Exception as e:
            logger.error(f"Error in listen loop: {e}")
            await self._handle_reconnect()

    async def _process_message(self, data: Dict) -> None:
        """Process incoming WebSocket message."""
        if "e" not in data:
            return

        event_type = data["e"]

        if event_type == "24hrTicker":
            await self._handle_ticker(data)
        elif event_type == "kline":
            await self._handle_kline(data)

    async def _handle_ticker(self, data: Dict) -> None:
        """Handle 24hr ticker update."""
        try:
            ticker_data = {
                "symbol": data.get("s"),
                "price": Decimal(data.get("c", "0")),
                "bid": Decimal(data.get("b", "0")),
                "ask": Decimal(data.get("a", "0")),
                "volume": Decimal(data.get("v", "0")),
                "quote_volume": Decimal(data.get("q", "0")),
                "high": Decimal(data.get("h", "0")),
                "low": Decimal(data.get("l", "0")),
                "timestamp": datetime.fromtimestamp(data.get("E", 0) / 1000, tz=timezone.utc),
            }

            # Call registered callbacks
            for callback in self.price_callbacks:
                try:
                    await callback(ticker_data) if asyncio.iscoroutinefunction(callback) else callback(ticker_data)
                except Exception as e:
                    logger.error(f"Error in price callback: {e}")

        except Exception as e:
            logger.error(f"Error handling ticker: {e}")

    async def _handle_kline(self, data: Dict) -> None:
        """Handle kline (candlestick) update."""
        try:
            kline_data = data.get("k", {})

            candle = {
                "symbol": data.get("s"),
                "interval": kline_data.get("i"),
                "is_closed": kline_data.get("x"),  # Whether the candle is closed
                "open_time": datetime.fromtimestamp(kline_data.get("t", 0) / 1000, tz=timezone.utc),
                "close_time": datetime.fromtimestamp(kline_data.get("T", 0) / 1000, tz=timezone.utc),
                "open": Decimal(kline_data.get("o", "0")),
                "high": Decimal(kline_data.get("h", "0")),
                "low": Decimal(kline_data.get("l", "0")),
                "close": Decimal(kline_data.get("c", "0")),
                "volume": Decimal(kline_data.get("v", "0")),
                "quote_volume": Decimal(kline_data.get("q", "0")),
                "trades_count": kline_data.get("n", 0),
                "taker_buy_base_volume": Decimal(kline_data.get("V", "0")),
                "taker_buy_quote_volume": Decimal(kline_data.get("Q", "0")),
            }

            # Call registered callbacks
            for callback in self.kline_callbacks:
                try:
                    await callback(candle) if asyncio.iscoroutinefunction(callback) else callback(candle)
                except Exception as e:
                    logger.error(f"Error in kline callback: {e}")

        except Exception as e:
            logger.error(f"Error handling kline: {e}")

    def register_price_callback(self, callback: Callable[[Dict], None]) -> None:
        """Register a callback for price updates."""
        self.price_callbacks.append(callback)

    def register_kline_callback(self, callback: Callable[[Dict], None]) -> None:
        """Register a callback for kline updates."""
        self.kline_callbacks.append(callback)

    async def get_historical_klines(
        self, symbol: str, interval: str = "1m", limit: int = 100, start_time: Optional[datetime] = None
    ) -> List[Dict]:
        """Fetch historical kline data via REST API."""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = int(start_time.timestamp() * 1000)

        try:
            klines = None
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12)
            ) as session:
                for base_url in self.PUBLIC_REST_URLS:
                    try:
                        async with session.get(
                            f"{base_url}/v3/klines", params=params
                        ) as response:
                            if response.status == 200:
                                klines = await response.json()
                                break
                            logger.warning(
                                "Binance kline endpoint returned %s from %s",
                                response.status,
                                base_url,
                            )
                    except Exception as exc:
                        logger.warning(
                            "Binance kline endpoint failed for %s: %s",
                            base_url,
                            exc,
                        )

            if klines is None and self.rest_client:
                klines = self.rest_client.get_klines(**params)
            if klines is None:
                return []

            return [
                {
                    "symbol": symbol,
                    "interval": interval,
                    "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
                    "close_time": datetime.fromtimestamp(int(k[6]) / 1000, tz=timezone.utc),
                    "open": Decimal(k[1]),
                    "high": Decimal(k[2]),
                    "low": Decimal(k[3]),
                    "close": Decimal(k[4]),
                    "volume": Decimal(k[5]),
                    "quote_volume": Decimal(k[7]),
                    "trades_count": int(k[8]),
                    "taker_buy_base_volume": Decimal(k[9]),
                    "taker_buy_quote_volume": Decimal(k[10]),
                }
                for k in klines
            ]
        except BinanceAPIException as e:
            logger.error(f"Binance API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching historical klines: {e}")
            return []

    async def get_exchange_info(self) -> Optional[Dict]:
        """Get exchange information."""
        if not self.rest_client:
            return None

        try:
            return self.rest_client.get_exchange_info()
        except Exception as e:
            logger.error(f"Error fetching exchange info: {e}")
            return None
