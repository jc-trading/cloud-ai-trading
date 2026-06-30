"""
AI analysis result models.
"""

import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Numeric, Integer, Text, Boolean, ForeignKey, Enum as SAEnum, text, true
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalysisType(str, enum.Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    SIGNAL = "signal"


class TradeAction(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class AssetClass(str, enum.Enum):
    CRYPTO = "crypto"
    EQUITY = "equity"


class Verdict(str, enum.Enum):
    """Go/no-go/watch decision verdict — independent of `action`. HOLD != no-go."""
    GO = "go"
    NO_GO = "no-go"
    WATCH = "watch"


class AIAnalysisResult(Base):
    __tablename__ = "ai_analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    exchange_type: Mapped[str] = mapped_column(String(20), default="binance")

    analysis_type: Mapped[AnalysisType] = mapped_column(
        SAEnum(AnalysisType, values_callable=lambda e: [m.value for m in e], native_enum=False)
    )
    indicators_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    claude_response: Mapped[dict] = mapped_column(JSONB, default=dict)

    action: Mapped[TradeAction] = mapped_column(
        SAEnum(TradeAction, values_callable=lambda e: [m.value for m in e], native_enum=False)
    )
    confidence: Mapped[int] = mapped_column(Integer, default=0)

    entry_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    prompt_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    api_cost: Mapped[float] = mapped_column(Numeric(10, 6), default=0)

    # --- Decision fields (migration 010) ---
    asset_class: Mapped[AssetClass] = mapped_column(
        SAEnum(AssetClass, values_callable=lambda e: [m.value for m in e], native_enum=False),
        server_default="crypto",
        default=AssetClass.CRYPTO,
    )
    verdict: Mapped[Verdict] = mapped_column(
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e], native_enum=False),
        server_default="watch",
        default=Verdict.WATCH,
    )
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_completeness: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    ai_invoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    ai_skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("positions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
