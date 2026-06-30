"""
Exchange connection service: manage API keys, create adapters.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_api_key, decrypt_api_key
from app.core.exceptions import NotFoundException, ExchangeConnectionError
from app.modules.exchange.models import ExchangeConnection, ExchangeType, TradingMode
from app.modules.exchange.schemas import ExchangeCreate, ExchangeUpdate
from app.modules.exchange.adapters.base import ExchangeAdapter
from app.modules.exchange.adapters.binance import BinanceAdapter
from app.modules.exchange.adapters.alpaca import AlpacaAdapter


class ExchangeService:

    @staticmethod
    def _get_adapter(connection: ExchangeConnection) -> ExchangeAdapter:
        """Create the appropriate exchange adapter from a connection."""
        api_key = decrypt_api_key(connection.api_key_encrypted)
        api_secret = decrypt_api_key(connection.api_secret_encrypted)

        if connection.exchange_type == ExchangeType.BINANCE:
            return BinanceAdapter(api_key, api_secret)
        elif connection.exchange_type == ExchangeType.ALPACA:
            # Use paper trading by default; live if connection is in LIVE mode
            paper = connection.trading_mode.value != "live"
            return AlpacaAdapter(api_key, api_secret, paper=paper)
        else:
            raise ExchangeConnectionError(
                detail=f"Exchange '{connection.exchange_type.value}' is coming soon!"
            )

    @staticmethod
    async def create_connection(
        db: AsyncSession, user_id: UUID, data: ExchangeCreate
    ) -> ExchangeConnection:
        """Create a new exchange connection with encrypted credentials."""
        connection = ExchangeConnection(
            user_id=user_id,
            exchange_type=data.exchange_type,
            api_key_encrypted=encrypt_api_key(data.api_key),
            api_secret_encrypted=encrypt_api_key(data.api_secret),
            passphrase_encrypted=(
                encrypt_api_key(data.passphrase) if data.passphrase else None
            ),
            permissions=data.permissions,
            trading_mode=data.trading_mode,
            ip_whitelist=data.ip_whitelist,
        )
        db.add(connection)
        await db.flush()
        await db.refresh(connection)
        return connection

    @staticmethod
    async def get_connections(
        db: AsyncSession, user_id: UUID
    ) -> list[ExchangeConnection]:
        """Get all exchange connections for a user."""
        result = await db.execute(
            select(ExchangeConnection)
            .where(ExchangeConnection.user_id == user_id)
            .order_by(ExchangeConnection.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_connection(
        db: AsyncSession, user_id: UUID, connection_id: UUID
    ) -> ExchangeConnection:
        """Get a specific exchange connection."""
        result = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.id == connection_id,
                ExchangeConnection.user_id == user_id,
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            raise NotFoundException("Exchange connection")
        return connection

    @staticmethod
    async def update_connection(
        db: AsyncSession,
        user_id: UUID,
        connection_id: UUID,
        data: ExchangeUpdate,
    ) -> ExchangeConnection:
        """Update an exchange connection."""
        connection = await ExchangeService.get_connection(db, user_id, connection_id)
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(connection, key, value)
        await db.flush()
        await db.refresh(connection)
        return connection

    @staticmethod
    async def delete_connection(
        db: AsyncSession, user_id: UUID, connection_id: UUID
    ) -> None:
        """Delete an exchange connection."""
        connection = await ExchangeService.get_connection(db, user_id, connection_id)
        await db.delete(connection)

    @staticmethod
    async def test_connection(
        db: AsyncSession, user_id: UUID, connection_id: UUID
    ) -> dict:
        """Test an exchange connection."""
        connection = await ExchangeService.get_connection(db, user_id, connection_id)
        adapter = ExchangeService._get_adapter(connection)

        try:
            success = await adapter.test_connection()
            balance = await adapter.get_balance() if success else None
            return {
                "success": success,
                "message": "Connection successful" if success else "Connection failed",
                "balance": balance,
            }
        except Exception as e:
            return {"success": False, "message": str(e), "balance": None}
        finally:
            await adapter._close()

    @staticmethod
    async def get_balance(
        db: AsyncSession, user_id: UUID, connection_id: UUID
    ) -> dict:
        """Get balance from exchange."""
        connection = await ExchangeService.get_connection(db, user_id, connection_id)
        adapter = ExchangeService._get_adapter(connection)

        try:
            balances = await adapter.get_balance()
            # Calculate approximate USDT total
            total_usdt = balances.get("USDT", 0.0)
            return {
                "exchange_type": connection.exchange_type,
                "balances": balances,
                "total_usdt": total_usdt,
            }
        finally:
            await adapter._close()
