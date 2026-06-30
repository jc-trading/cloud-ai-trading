"""
Quantitative strategy models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Numeric, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuantStrategy(Base):
    __tablename__ = "quant_strategies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Strategy configuration
    symbols: Mapped[dict] = mapped_column(JSONB, default=lambda: [])  # ["BTC/USDT", "ETH/USDT"]
    timeframe: Mapped[str] = mapped_column(String(10), default="1h")  # 1m, 5m, 15m, 1h, 4h, 1d

    indicators_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    entry_conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    exit_conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    position_sizing: Mapped[dict] = mapped_column(JSONB, default=dict)

    stop_loss_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=3.0)
    take_profit_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=8.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=5)
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=24)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    backtest_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
