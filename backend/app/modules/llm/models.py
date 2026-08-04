"""LLM usage log (2026-08-04) — every LLM call the system makes is booked here.

Direction v3 is deterministic-quant: the LLM does NOT make trading decisions.
The only live LLM use is the recommendation *explanation layer* (one call per
top-N shortlisted name per signal_cycle), and any future LLM use MUST route
through ``app.modules.llm.client`` so it lands in this table automatically.

One row per call captures who/what/how-much: platform + model, token counts,
the per-1M unit prices SNAPSHOTTED at call time (so a later price-table change
never rewrites history), and the derived USD cost. The dashboard sums these
into total-calls / total-tokens / total-USD tiles.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        nullable=False, index=True)

    # what triggered the call — free-form label + optional subject symbol
    context = Column(String(80), nullable=False)          # e.g. recommendation_explanation
    symbol = Column(String(20), nullable=True, index=True)

    platform = Column(String(30), nullable=False)         # e.g. anthropic
    model = Column(String(60), nullable=False)            # e.g. claude-haiku-4-5-20251001

    # token usage as reported by the provider
    input_tokens = Column(Integer, nullable=False, server_default="0")
    output_tokens = Column(Integer, nullable=False, server_default="0")
    cache_read_tokens = Column(Integer, nullable=False, server_default="0")
    cache_creation_tokens = Column(Integer, nullable=False, server_default="0")

    # per-1M-token unit prices SNAPSHOTTED at call time (USD)
    unit_price_in = Column(Numeric(10, 4), nullable=False, server_default="0")
    unit_price_out = Column(Numeric(10, 4), nullable=False, server_default="0")

    cost_usd = Column(Numeric(14, 8), nullable=False, server_default="0")

    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, server_default="true")
    error = Column(Text, nullable=True)
    request_id = Column(String(80), nullable=True)
