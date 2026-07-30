"""
Watchlist models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="My Watchlist")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="watchlists")
    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan", lazy="selectin")
    trading_signals = relationship("TradingSignal", back_populates="watchlist", cascade="all, delete-orphan")
    alert_rules = relationship("AlertRule", back_populates="watchlist", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="watchlist", cascade="all, delete-orphan")
    portfolio_stats = relationship("PortfolioStats", back_populates="watchlist", cascade="all, delete-orphan")
    risk_limits = relationship("RiskLimit", back_populates="watchlist", cascade="all, delete-orphan")
    drawdown_records = relationship("DrawdownRecord", back_populates="watchlist", cascade="all, delete-orphan")

    @property
    def symbols(self) -> list[str]:
        """Get list of symbols in this watchlist."""
        return [item.symbol for item in self.items]

    @property
    def is_active(self) -> bool:
        """Check if watchlist has items."""
        return len(self.items) > 0


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(20))   # e.g., "BTC/USDT" or "AAPL"
    exchange_type: Mapped[str] = mapped_column(String(20), default="binance")
    market_type: Mapped[str] = mapped_column(String(10), default="crypto")  # "crypto" or "stock"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_with_exchange: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    watchlist = relationship("Watchlist", back_populates="items")
