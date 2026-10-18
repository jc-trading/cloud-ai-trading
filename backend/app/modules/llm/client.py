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
``deepseek`` | ``claude_cli``). DeepSeek serves an Anthropic-compatible
endpoint, so both API providers run on the same ``anthropic`` SDK — only the
key, base URL and default model differ. ``claude_cli`` runs the pinned Claude
Code binary (``claude -p``) on the Max subscription: its rows are marked as
subscription usage and their cost is the CLI's own estimate, capped by
``CLAUDE_CLI_DAILY_CAP`` calls per UTC day. ``llm_calls.platform`` records which
one actually served the call.

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

import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
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
    billing: str = "api"


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
    if provider == "claude_cli":
        return Provider("claude-cli", settings.CLAUDE_CODE_OAUTH_TOKEN, None,
                        settings.CLAUDE_CLI_MODEL, "CLAUDE_CODE_OAUTH_TOKEN",
                        billing="subscription")
    raise ValueError(
        f"unsupported AI_PROVIDER {settings.AI_PROVIDER!r} — use 'claude', "
        "'deepseek' or 'claude_cli'"
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
    if provider.platform == "claude-cli":
        return await _call_claude_cli(db, provider, _skip, context=context, prompt=prompt,
                                      system=system, symbol=symbol, model=model)

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


# Everything a bare `claude -p` would load (tools, MCP servers, user/project
# settings, slash commands, session files) is switched off: it costs ~28k input
# tokens per call otherwise. The empty strings must stay separate argv items.
CLAUDE_CLI_SLIM_FLAGS = (
    "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
)
CLAUDE_CLI_DEFAULT_SYSTEM = "Answer concisely."
CLAUDE_CLI_BREAKER_SECONDS = 900
# Plan quota wording ("usage limit reached", "5-hour limit reached", "hit your
# limit") stops the CLI for the rest of the UTC day; a transient API rate limit
# ("rate limit reached", 429) only trips the circuit breaker.
_QUOTA_PATTERN = re.compile(
    r"usage limit|(?<!rate[ _-])limit reached|hit your limit|quota|out of (extra )?usage",
    re.I)
_RATE_LIMIT_PATTERN = re.compile(r"rate.?limit|too many requests|\b429\b", re.I)
_cli_breaker_until = 0.0


def build_cli_argv(model: str, system: str | None) -> list[str]:
    return [settings.CLAUDE_CLI_PATH, "-p", "--output-format", "json", "--model", model,
            "--system-prompt", system or CLAUDE_CLI_DEFAULT_SYSTEM, *CLAUDE_CLI_SLIM_FLAGS]


def cli_env(token: str) -> dict[str, str]:
    """Allow-list environment for the CLI child: anything else (notably
    ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN) would switch it to pay-per-token API use."""
    env = {"LANG": "C.UTF-8", "CLAUDE_CODE_OAUTH_TOKEN": token, "DISABLE_AUTOUPDATER": "1"}
    for key in ("PATH", "HOME"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


async def _cli_usage_today(db: AsyncSession) -> tuple[int, bool]:
    """Today's (UTC) claude-cli calls and whether one hit the quota: rows in the
    database plus rows still pending in the caller's session, read without
    autoflushing the caller's unit of work."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    pending = [o for o in db.new if isinstance(o, LLMCall) and o.platform == "claude-cli"]
    with db.no_autoflush:
        count, quota = (await db.execute(
            select(func.count(),
                   func.count().filter(LLMCall.error.like("quota_exhausted%")))
            .where(LLMCall.platform == "claude-cli", LLMCall.created_at >= start)
        )).one()
    pending_quota = any((o.error or "").startswith("quota_exhausted") for o in pending)
    return int(count) + len(pending), bool(quota) or pending_quota


def _open_breaker() -> None:
    global _cli_breaker_until
    _cli_breaker_until = time.monotonic() + CLAUDE_CLI_BREAKER_SECONDS


async def _notify_quota(context: str) -> None:
    try:
        from app.modules.notifications.service import notify

        await notify("llm_quota_exhausted", "warning", {
            "text": "⚠️ Claude CLI 额度用完 — 今天（UTC）停止 LLM 调用，00:00 UTC 后重试。"
                    f"context={context}"})
    except Exception:
        logger.warning("claude-cli quota notification failed", exc_info=True)


async def _call_claude_cli(db: AsyncSession, provider: Provider, skip, *, context: str,
                           prompt: str, system: str | None, symbol: str | None,
                           model: str | None) -> LLMResult:
    """One `claude -p` call on the subscription, booked like an API call.

    Guarded three ways so the CLI cannot stall or overspend the nightly cycle:
    a daily call cap plus a sticky per-day stop once the CLI reports its quota
    is used up (announced once), a hard timeout that kills the whole process
    group, and a 15-minute circuit breaker after a crash, timeout or unreadable
    output. Never raises."""
    if time.monotonic() < _cli_breaker_until:
        return skip("claude-cli circuit breaker open")
    try:
        used, quota_hit = await _cli_usage_today(db)
    except Exception as exc:
        return skip(f"claude-cli usage check failed: {type(exc).__name__}: {exc}")
    if quota_hit:
        return skip("claude-cli quota exhausted today — retry after 00:00 UTC")
    cap = settings.CLAUDE_CLI_DAILY_CAP
    if used >= cap:
        return skip(f"claude-cli daily cap reached ({used}/{cap})")

    resolved_model = model or provider.default_model
    row = LLMCall(id=uuid4(), context=context, symbol=symbol,
                  platform=provider.platform, model=resolved_model[:60])
    row.billing = "subscription"
    timeout = float(settings.CLAUDE_CLI_TIMEOUT_SECONDS)
    started = time.perf_counter()

    def _book(success: bool) -> None:
        row.unit_price_in, row.unit_price_out = price_for("anthropic", row.model)
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        row.success = success
        db.add(row)

    def _fail(error: str) -> LLMResult:
        row.error = error[:2000]
        _book(False)
        logger.warning("call_llm claude-cli failed (%s): %s", context, row.error)
        return LLMResult(text=None, call_id=row.id, input_tokens=0, output_tokens=0,
                         cost_usd=Decimal(0), success=False, error=row.error)

    try:
        proc = await asyncio.create_subprocess_exec(
            *build_cli_argv(resolved_model, system),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=cli_env(provider.api_key),
            start_new_session=True)
    except OSError as exc:
        _open_breaker()
        return _fail(f"{type(exc).__name__}: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        _open_breaker()
        return _fail(f"timeout after {timeout:g}s")

    err_text = stderr.decode("utf-8", "replace").strip()[-500:]
    try:
        data = json.loads(stdout.decode("utf-8", "replace"))
        if not isinstance(data, dict):
            raise ValueError("CLI output is not a JSON object")
    except ValueError:
        detail = err_text or stdout.decode("utf-8", "replace").strip()[-500:]
        if _QUOTA_PATTERN.search(detail):
            return await _quota_fail(db, context, _fail, detail)
        _open_breaker()
        return _fail(f"claude-cli exit {proc.returncode}, unreadable output: {detail}")

    usage = data.get("usage") or {}
    row.input_tokens = int(usage.get("input_tokens") or 0)
    row.output_tokens = int(usage.get("output_tokens") or 0)
    row.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
    row.cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
    row.cost_usd = Decimal(str(data.get("total_cost_usd") or 0))
    model_usage = data.get("modelUsage") or {}
    if model_usage:
        row.model = max(model_usage,
                        key=lambda k: (model_usage[k] or {}).get("costUSD") or 0)[:60]
    session_id = data.get("session_id")
    row.request_id = str(session_id)[:80] if session_id else None

    text = data.get("result")
    if proc.returncode != 0 or data.get("is_error"):
        detail = str(text or err_text or f"exit {proc.returncode}")
        if _QUOTA_PATTERN.search(detail):
            return await _quota_fail(db, context, _fail, detail)
        if _RATE_LIMIT_PATTERN.search(detail):
            _open_breaker()
        return _fail(f"claude-cli error: {detail}")

    _book(True)
    return LLMResult(text=str(text) if text else None, call_id=row.id,
                     input_tokens=row.input_tokens, output_tokens=row.output_tokens,
                     cost_usd=row.cost_usd, success=True)


async def _quota_fail(db: AsyncSession, context: str, fail, detail: str) -> LLMResult:
    try:
        _, already = await _cli_usage_today(db)
    except Exception:
        already = True
    if not already:
        await _notify_quota(context)
    return fail(f"quota_exhausted: {detail}")
