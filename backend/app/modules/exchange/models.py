"""
Exchange connection models.
"""

import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ExchangeType(str, enum.Enum):
    BINANCE = "binance"
    ALPACA = "alpaca"
    BITGET = "bitget"
    OKX = "okx"


class TradingMode(str, enum.Enum):
    LIVE = "live"
    SIMULATE = "simulate"


class ExchangeConnection(Base):
    __tablename__ = "exchange_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    exchange_type: Mapped[ExchangeType] = mapped_column(
        SAEnum(ExchangeType, values_callable=lambda e: [m.value for m in e], native_enum=False)
    )
    api_key_encrypted: Mapped[str] = mapped_column(String(512))
    api_secret_encrypted: Mapped[str] = mapped_column(String(512))
    passphrase_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)

    permissions: Mapped[dict] = mapped_column(JSONB, default=lambda: ["read"])
    trading_mode: Mapped[TradingMode] = mapped_column(
        SAEnum(TradingMode, values_callable=lambda e: [m.value for m in e], native_enum=False),
        default=TradingMode.SIMULATE,
    )
    ip_whitelist: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="exchange_connections")

    def __repr__(self) -> str:
        return f"<ExchangeConnection {self.exchange_type.value} user={self.user_id}>"
