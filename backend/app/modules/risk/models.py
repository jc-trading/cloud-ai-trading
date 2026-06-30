"""Risk management database models - P3 Phase 3A."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, Numeric, String, Index, JSON, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class RiskLimit(Base):
    """Portfolio-level risk limit configuration."""

    __tablename__ = "risk_limits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)

    # Per-position limits
    max_position_size_percent = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("5.0"))
    max_loss_per_trade_percent = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("2.0"))

    # Portfolio-level limits
    max_portfolio_loss_percent = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("10.0"))
    daily_loss_limit_percent = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("3.0"))
    max_open_positions = Column(Integer, nullable=False, default=10)
    max_concentration_percent = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("30.0"))

    # Signal quality threshold
    min_signal_strength = Column(Integer, nullable=False, default=50)
    min_confidence_threshold = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("60.0"))

    # Time-based limits
    max_position_age_days = Column(Integer, nullable=False, default=7)
    max_consecutive_losses = Column(Integer, nullable=False, default=3)

    # Position sizing configuration
    risk_level = Column(String(20), nullable=False, default="medium")  # low, medium, high
    position_sizing_method = Column(String(20), nullable=False, default="risk_weighted")  # kelly, fixed, risk_weighted

    # Status
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    watchlist = relationship("Watchlist", back_populates="risk_limits")
    position_metrics = relationship("PositionMetric", back_populates="risk_limit", cascade="all, delete-orphan")
    drawdown_records = relationship("DrawdownRecord", back_populates="risk_limit", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_risk_limits_watchlist_id", "watchlist_id"),
    )


class PositionMetric(Base):
    """Real-time metrics for individual positions."""

    __tablename__ = "position_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True)
    risk_limit_id = Column(UUID(as_uuid=True), ForeignKey("risk_limits.id", ondelete="SET NULL"), nullable=True)

    # Current P&L tracking
    current_pnl = Column(Numeric(precision=20, scale=8), nullable=False, default=Decimal("0"))
    pnl_percent = Column(Numeric(precision=8, scale=4), nullable=False, default=Decimal("0"))

    # Excursion tracking (Max Favorable/Adverse)
    max_favorable_excursion = Column(Numeric(precision=20, scale=8), nullable=False, default=Decimal("0"))
    max_adverse_excursion = Column(Numeric(precision=20, scale=8), nullable=False, default=Decimal("0"))

    # Price and time tracking
    current_price = Column(Numeric(precision=18, scale=8), nullable=False)
    days_in_trade = Column(Integer, nullable=False, default=0)

    # Risk metrics
    position_size_percent = Column(Numeric(precision=5, scale=2), nullable=True)
    concentration_impact = Column(Numeric(precision=5, scale=2), nullable=True)

    # Exit information
    exit_reason = Column(String(50), nullable=True)  # TP, SL, signal_reversal, manual, time_based

    # Timestamps
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    position = relationship("Position", back_populates="metrics")
    risk_limit = relationship("RiskLimit", back_populates="position_metrics")

    __table_args__ = (
        Index("ix_position_metrics_position_id", "position_id"),
        Index("ix_position_metrics_recorded_at", "recorded_at"),
    )


class DrawdownRecord(Base):
    """Historical portfolio drawdown tracking."""

    __tablename__ = "drawdown_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    risk_limit_id = Column(UUID(as_uuid=True), ForeignKey("risk_limits.id", ondelete="CASCADE"), nullable=False, index=True)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)

    # Portfolio value tracking
    peak_equity = Column(Numeric(precision=20, scale=8), nullable=False)
    trough_equity = Column(Numeric(precision=20, scale=8), nullable=False)
    current_equity = Column(Numeric(precision=20, scale=8), nullable=False)

    # Drawdown metrics
    max_drawdown_percent = Column(Numeric(precision=8, scale=4), nullable=False)
    current_drawdown_percent = Column(Numeric(precision=8, scale=4), nullable=False)

    # Additional metrics
    unrealized_pnl = Column(Numeric(precision=20, scale=8), nullable=True)
    realized_pnl = Column(Numeric(precision=20, scale=8), nullable=True)
    total_pnl = Column(Numeric(precision=20, scale=8), nullable=True)

    # Timestamps
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    risk_limit = relationship("RiskLimit", back_populates="drawdown_records")
    watchlist = relationship("Watchlist", back_populates="drawdown_records")

    __table_args__ = (
        Index("ix_drawdown_records_watchlist_id", "watchlist_id"),
        Index("ix_drawdown_records_recorded_at", "recorded_at"),
    )
