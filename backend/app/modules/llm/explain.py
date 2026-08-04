"""Recommendation explanation layer — the ONLY live LLM use in v3.

After signal_cycle publishes the daily recommendation feed, this writes a
one-or-two-sentence plain read of WHY each of the top-N names is a candidate,
for the transparency dashboard. It is explanation-ONLY (拍板 A): the
deterministic score is authoritative and this text never changes it. Every call
is booked into llm_calls via app.modules.llm.client.call_llm, so the cost is
always visible.

Wording follows the R0-9/§3.2 rule: the phase label is DESCRIPTIVE, not a
prediction — 'down' describes the current trend, it does not mean the price will
fall. The system prompt enforces that so the LLM read stays honest.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.client import call_llm
from app.modules.simledger.models import Recommendation

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You explain a deterministic quantitative stock signal for a dashboard, in "
    "one or two plain sentences. Describe only what the signal is currently "
    "reading — trend phase, momentum, volatility. You are NOT giving financial "
    "advice, NOT predicting the price, and NOT recommending buy or sell. A "
    "'down' phase is a description of the current trend; it does not mean the "
    "price will fall. Be concrete and factual. No preamble, no disclaimer — just "
    "the read."
)


def build_prompt(rec: Recommendation) -> str:
    f = rec.features or {}
    parts = [
        f"Symbol: {rec.symbol}",
        "Trend phase: " + rec.phase
        + (f" ({rec.phase_reason})" if rec.phase_reason else ""),
        f"Signal direction: {rec.direction}",
        f"Confidence score: {float(rec.confidence):.1f} / 100",
        (f"Buy-shortlist rank: {rec.shortlist_rank}" if rec.shortlist_rank
         else "Not in the buy shortlist (watch-only)"),
    ]
    if f.get("sector"):
        parts.append(f"Sector: {f['sector']}")
    if f.get("atr_pct") is not None:
        parts.append(f"ATR volatility: {float(f['atr_pct']) * 100:.1f}% of price")
    if f.get("above_rising_ma20") is not None:
        parts.append(f"Above a rising 20-day MA: {bool(f['above_rising_ma20'])}")
    if f.get("expected_move") is not None:
        parts.append(f"Expected ATR move band: {float(f['expected_move']):.2f}")
    return "Explain this signal in one or two sentences:\n" + "\n".join(parts)


async def explain_recommendations(
    db: AsyncSession,
    trade_date: date,
    *,
    top_n: int = 10,
    context: str = "recommendation_explanation",
) -> int:
    """Write an LLM explanation onto the top-N (by confidence) recommendations
    for ``trade_date``. Rows/calls are added to ``db``; the caller commits.
    Returns how many got an explanation. call_llm never raises, so a provider
    outage just leaves ``llm_explanation`` null (and books failed rows)."""
    rows = (await db.execute(
        select(Recommendation)
        .where(Recommendation.trade_date == trade_date)
        .order_by(Recommendation.confidence.desc())
        .limit(top_n)
    )).scalars().all()

    explained = 0
    for r in rows:
        result = await call_llm(
            db, context=context, prompt=build_prompt(r),
            system=_SYSTEM, symbol=r.symbol, max_tokens=160,
        )
        if result.text:
            r.llm_explanation = result.text.strip()
            explained += 1
    return explained
