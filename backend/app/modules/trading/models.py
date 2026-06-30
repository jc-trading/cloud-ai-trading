"""Trading models for signals, alerts, and portfolio tracking."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class TradingSignal(Base):
    """Trading signal generated from technical indicators."""

    __tablename__ = "trading_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    signal_type = Column(String(20), nullable=False, index=True)
    signal_strength = Column(Numeric(precision=5, scale=2), nullable=False)
    confidence = Column(Numeric(precision=5, scale=2), nullable=False)
    indicators_used = Column(JSON(), nullable=True)
    recommendation = Column(String(500), nullable=True)
    strategy = Column(String(50), nullable=True)
    signal_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    watchlist = relationship("Watchlist", back_populates="trading_signals")

    __table_args__ = (
        UniqueConstraint('watchlist_id', 'symbol', 'signal_timestamp', 'strategy', name='uq_signal_unique'),
    )


class AlertRule(Base):
    """Alert rule configuration for notifications."""

    __tablename__ = "alert_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    rule_type = Column(String(50), nullable=False)
    rule_config = Column(JSON(), nullable=False)
    alert_channels = Column(JSON(), nullable=False)
    enabled = Column(Boolean(), nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    watchlist = relationship("Watchlist", back_populates="alert_rules")
    alerts = relationship("Alert", back_populates="alert_rule", cascade="all, delete-orphan")


class Alert(Base):
    """Alert notification history."""

    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    alert_type = Column(String(50), nullable=False)
    message = Column(String(1000), nullable=False)
    status = Column(String(20), nullable=False, default="sent", index=True)
    alert_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    alert_rule = relationship("AlertRule", back_populates="alerts")


class Position(Base):
    """Trading position (open or closed)."""

    __tablename__ = "positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    entry_price = Column(Numeric(precision=18, scale=8), nullable=False)
    quantity = Column(Numeric(precision=20, scale=8), nullable=False)
    entry_date = Column(DateTime(timezone=True), nullable=False, index=True)
    exit_price = Column(Numeric(precision=18, scale=8), nullable=True)
    exit_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="open", index=True)
    position_type = Column(String(10), nullable=False, default="LONG")
    notes = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    watchlist = relationship("Watchlist", back_populates="positions")
    metrics = relationship("PositionMetric", back_populates="position", cascade="all, delete-orphan")

    @property
    def entry_value(self) -> Decimal:
        return Decimal(self.entry_price) * Decimal(self.quantity)

    @property
    def realized_pnl(self) -> Decimal:
        if self.status == "closed" and self.exit_price:
            return (Decimal(self.exit_price) - Decimal(self.entry_price)) * Decimal(self.quantity)
        return Decimal(0)


class PortfolioStats(Base):
    """Portfolio statistics and performance metrics."""

    __tablename__ = "portfolio_stats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    total_invested = Column(Numeric(precision=20, scale=2), nullable=False, default=0)
    current_value = Column(Numeric(precision=20, scale=2), nullable=False, default=0)
    unrealized_pnl = Column(Numeric(precision=20, scale=2), nullable=False, default=0)
    realized_pnl = Column(Numeric(precision=20, scale=2), nullable=False, default=0)
    total_return_percent = Column(Numeric(precision=10, scale=2), nullable=False, default=0)
    win_rate = Column(Numeric(precision=5, scale=2), nullable=True)
    max_drawdown = Column(Numeric(precision=5, scale=2), nullable=True)
    total_trades = Column(Integer(), nullable=False, default=0)
    winning_trades = Column(Integer(), nullable=False, default=0)
    losing_trades = Column(Integer(), nullable=False, default=0)
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    watchlist = relationship("Watchlist", back_populates="portfolio_stats")

    __table_args__ = (
        UniqueConstraint('watchlist_id', name='uq_portfolio_stats_per_watchlist'),
    )

    @property
    def total_pnl(self) -> Decimal:
        return Decimal(self.realized_pnl) + Decimal(self.unrealized_pnl)
