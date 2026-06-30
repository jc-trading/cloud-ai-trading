"""
Abstract base class for exchange adapters.
All exchange integrations must implement this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderRequest:
    symbol: str
    side: str  # "buy" or "sell"
    order_type: str  # "market", "limit", "stop_limit"
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    fee: Optional[float] = None
    message: str = ""


class ExchangeAdapter(ABC):
    """Abstract interface for exchange connections."""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if the API credentials are valid."""
        ...

    @abstractmethod
    async def get_balance(self) -> dict:
        """Get account balances. Returns {symbol: amount}."""
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        """Get current ticker for a symbol."""
        ...

    @abstractmethod
    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[dict]:
        """Get OHLCV candle data."""
        ...

    @abstractmethod
    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Get order book depth."""
        ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a trading order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """Get open orders."""
        ...

    @abstractmethod
    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """Get order history."""
        ...

    @abstractmethod
    async def get_available_symbols(self) -> list[str]:
        """Get all tradable symbols."""
        ...
