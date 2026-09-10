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

Which provider is used is env-selected (``AI_PROVIDER`` = ``claude`` |
``deepseek``). DeepSeek serves an Anthropic-compatible endpoint, so both run on
the same ``anthropic`` SDK — only the key, base URL and default model differ.
``llm_calls.platform`` records which one actually served the call.

Prices are per 1,000,000 tokens, USD (source: claude-api skill, 2026-06 cache):
  haiku-4.5  $1 / $5     sonnet-5  $3 / $15     opus-4.8  $5 / $25
DeepSeek bills peak/off-peak since 2026-08-16 (peak = Mon-Fri 01:00-04:00 and
06:00-10:00 UTC):
  v4-flash  $0.44 / $1.32 peak, $0.22 / $0.66 off-peak
  v4-pro    $1.32 / $3.96 peak, $0.66 / $1.98 off-peak
Update LLM_PRICES when a provider changes pricing; historical rows are
unaffected because each row snapshots the price it was charged at.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.llm.models import LLMCall

logger = logging.getLogger(__name__)

# (platform, model) -> (input_$/1M, output_$/1M). DeepSeek rows carry the
# OFF-PEAK rate; DEEPSEEK_PEAK_PRICES replaces them inside the peak window.
LLM_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5"): (1.00, 5.00),
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
    ("anthropic", "claude-sonnet-5"): (3.00, 15.00),
    ("anthropic", "claude-opus-4-8"): (5.00, 25.00),
    ("deepseek", "deepseek-v4-flash"): (0.22, 0.66),
    ("deepseek", "deepseek-v4-pro"): (0.66, 1.98),
}

DEEPSEEK_PEAK_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.44, 1.32),
    "deepseek-v4-pro": (1.32, 3.96),
}

# Mon-Fri UTC hour ranges [start, end) billed at the peak rate.
DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))


def _is_deepseek_peak(at: datetime) -> bool:
    utc = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if utc.weekday() >= 5:
        return False
    return any(start <= utc.hour < end for start, end in DEEPSEEK_PEAK_HOURS_UTC)


def price_for(platform: str, model: str,
              at: datetime | None = None) -> tuple[Decimal, Decimal]:
    """Per-1M (input, output) USD prices at time ``at`` (UTC, default now) —
    DeepSeek is billed peak/off-peak, Anthropic is flat. Unknown models return
    (0, 0) with a warning so an unpriced model still logs (cost 0) rather than
    crashing the caller."""
    prices = LLM_PRICES.get((platform, model))
    if prices is None:
        logger.warning("no price for %s/%s — logging call at $0", platform, model)
        return Decimal(0), Decimal(0)
    if platform == "deepseek" and _is_deepseek_peak(at or datetime.now(timezone.utc)):
        prices = DEEPSEEK_PEAK_PRICES.get(model, prices)
    return Decimal(str(prices[0])), Decimal(str(prices[1]))


@dataclass(frozen=True)
class Provider:
    platform: str
    api_key: str
    base_url: str | None
    default_model: str
    key_env: str


def resolve_provider() -> Provider:
    """Map ``settings.AI_PROVIDER`` onto the concrete platform label, credentials
    and default model the SDK is driven with. DeepSeek is served through its
    Anthropic-compatible endpoint, hence the shared client with a base_url."""
    provider = (settings.AI_PROVIDER or "").strip().lower()
    if provider == "claude":
        return Provider("anthropic", settings.ANTHROPIC_API_KEY, None,
                        settings.ANTHROPIC_MODEL, "ANTHROPIC_API_KEY")
    if provider == "deepseek":
        return Provider("deepseek", settings.DEEPSEEK_API_KEY,
                        settings.DEEPSEEK_BASE_URL, settings.DEEPSEEK_MODEL,
                        "DEEPSEEK_API_KEY")
    raise ValueError(
        f"unsupported AI_PROVIDER {settings.AI_PROVIDER!r} — use 'claude' or 'deepseek'"
    )


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

    If the selected provider's API key is unset — or ``AI_PROVIDER`` names an
    unsupported provider — the call is SKIPPED and nothing is logged (no call
    was made) — ``LLMResult.skipped`` is True.
    """
    def _skip(reason: str) -> LLMResult:
        logger.warning("call_llm skipped (%s): %s", context, reason)
        return LLMResult(text=None, call_id=None, input_tokens=0,
                         output_tokens=0, cost_usd=Decimal(0),
                         success=False, error=reason)

    try:
        provider = resolve_provider()
    except ValueError as exc:
        return _skip(str(exc))
    if not provider.api_key:
        return _skip(f"{provider.key_env} not set")

    resolved_model = model or provider.default_model
    price_in, price_out = price_for(provider.platform, resolved_model)
    row = LLMCall(id=uuid4(), context=context, symbol=symbol,
                  platform=provider.platform, model=resolved_model,
                  unit_price_in=price_in, unit_price_out=price_out)
    started = time.perf_counter()
    try:
        import anthropic

        client_kwargs: dict = {"api_key": provider.api_key, "timeout": timeout,
                               "max_retries": 1}
        if provider.base_url:
            client_kwargs["base_url"] = provider.base_url
        client = anthropic.AsyncAnthropic(**client_kwargs)
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
