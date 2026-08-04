"""Central LLM call wrapper — the ONE entry point every LLM call routes through.

Any code that wants to call an LLM MUST go through ``call_llm`` so the call is
booked into ``llm_calls`` automatically (platform, model, token usage, the
per-1M unit prices SNAPSHOTTED at call time, and the derived USD cost). This is
the mechanism that makes "何时用 LLM / 用什么 model / 花了多少" fully visible on
the dashboard — a new LLM use added later needs no separate accounting.

Direction v3 keeps the LLM out of the trading decision: today the only caller is
the recommendation explanation layer (see signal_cycle). The call is bounded by
a timeout (07-04 incident rule) and NEVER raises out — a provider failure is
logged as a failed row and returned as ``LLMResult(success=False)``.

Prices are per 1,000,000 tokens, USD (source: claude-api skill, 2026-06 cache):
  haiku-4.5  $1 / $5     sonnet-5  $3 / $15     opus-4.8  $5 / $25
Update LLM_PRICES when Anthropic changes pricing; historical rows are unaffected
because each row snapshots the price it was charged at.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.llm.models import LLMCall

logger = logging.getLogger(__name__)

PLATFORM = "anthropic"

# (platform, model) -> (input_$/1M, output_$/1M)
LLM_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5"): (1.00, 5.00),
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
    ("anthropic", "claude-sonnet-5"): (3.00, 15.00),
    ("anthropic", "claude-opus-4-8"): (5.00, 25.00),
}


def price_for(platform: str, model: str) -> tuple[Decimal, Decimal]:
    """Per-1M (input, output) USD prices; (0, 0) with a warning if unknown so an
    unpriced model still logs (cost 0) rather than crashing the caller."""
    prices = LLM_PRICES.get((platform, model))
    if prices is None:
        logger.warning("no price for %s/%s — logging call at $0", platform, model)
        return Decimal(0), Decimal(0)
    return Decimal(str(prices[0])), Decimal(str(prices[1]))


@dataclass
class LLMResult:
    text: str | None
    call_id: UUID | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    success: bool
    error: str | None = None

    @property
    def skipped(self) -> bool:
        return self.call_id is None


async def call_llm(
    db: AsyncSession,
    *,
    context: str,
    prompt: str,
    system: str | None = None,
    symbol: str | None = None,
    model: str | None = None,
    max_tokens: int = 160,
    timeout: float = 30.0,
) -> LLMResult:
    """Make one LLM call and book it into ``llm_calls`` (added to ``db``; the
    caller commits). Returns the text plus usage/cost. Never raises: on any
    failure a ``success=False`` row is written and returned.

    If ``ANTHROPIC_API_KEY`` is unset the call is SKIPPED and nothing is logged
    (no call was made) — ``LLMResult.skipped`` is True.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("call_llm skipped (%s): ANTHROPIC_API_KEY not set", context)
        return LLMResult(text=None, call_id=None, input_tokens=0,
                         output_tokens=0, cost_usd=Decimal(0),
                         success=False, error="ANTHROPIC_API_KEY not set")

    resolved_model = model or settings.ANTHROPIC_MODEL
    price_in, price_out = price_for(PLATFORM, resolved_model)
    row = LLMCall(id=uuid4(), context=context, symbol=symbol,
                  platform=PLATFORM, model=resolved_model,
                  unit_price_in=price_in, unit_price_out=price_out)
    started = time.perf_counter()
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=timeout,
            max_retries=1,
        )
        kwargs: dict = {"model": resolved_model, "max_tokens": max_tokens,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        message = await client.messages.create(**kwargs)

        usage = message.usage
        row.input_tokens = usage.input_tokens or 0
        row.output_tokens = usage.output_tokens or 0
        row.cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        row.cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
        row.cost_usd = (Decimal(row.input_tokens) / Decimal(1_000_000) * price_in
                        + Decimal(row.output_tokens) / Decimal(1_000_000) * price_out)
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        row.success = True
        row.request_id = getattr(message, "_request_id", None)

        text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
        db.add(row)
        return LLMResult(text=text or None, call_id=row.id,
                         input_tokens=row.input_tokens,
                         output_tokens=row.output_tokens,
                         cost_usd=row.cost_usd, success=True)
    except Exception as exc:  # never raise out of the wrapper
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        row.success = False
        row.error = f"{type(exc).__name__}: {exc}"[:2000]
        db.add(row)
        logger.warning("call_llm failed (%s): %s", context, exc)
        return LLMResult(text=None, call_id=row.id, input_tokens=0,
                         output_tokens=0, cost_usd=Decimal(0),
                         success=False, error=row.error)
