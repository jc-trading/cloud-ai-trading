"""Equity research agent: candidate symbol -> one auditable Decision.

This is Phase 3 Step 3 of the CAT merge. It turns a candidate equity ``symbol``
into exactly ONE unified Decision row on ``ai_analysis_results`` (asset_class=
equity), the same transparent payload the crypto pipeline writes, so both AIs
show up side by side on the dashboard's Decision feed.

Pipeline (deliberately conservative + cheap on the free API tiers):

  1. **Read structured FA from the Step-1 cache** (``company_fundamentals`` +
     ``earnings_calendar``) — the latest *reported* earnings gives EPS/revenue
     actual vs. estimate, from which we derive the beat %; the profile gives
     name/sector/S&P membership for context. No Finnhub call on this path.
  2. **Pull the earnings-day price reaction %** live via market_data (a small,
     best-effort, None-safe fetch — reaction is a non-critical signal).
  3. **Score** with the pure EQUITY-scoring module (``score_catalyst``). The
     scorer owns the SPEC bands/gates: critical data (EPS/revenue) missing ->
     ``scorable=False`` -> auto no-go; stale earnings -> recency gate vetoes to
     no-go; composite maps to go/watch/no-go (>=65 / 50-64 / <50).
  4. **Call Claude for the qualitative layer ONLY when it can matter** — i.e.
     the structured pass is scorable AND already cleared the recency gate AND
     scored at least watch-band (verdict != no-go). Claude reads the qualitative
     picture (guidance raised/cut, news) — we feed it the Finnhub company_news
     headlines (cheaper + deterministic than the web-search tool; the tool is a
     drop-in alternative). If Claude returns a concrete guidance direction we
     RE-SCORE with it so the qualitative read actually moves the number.
  5. **Persist one Decision**: verdict / verdict_reason / data_completeness
     (JSONB, each datum present-or-missing) / indicators_snapshot (the pulled FA
     + score breakdown) / claude_response (the reasoning) / ai_invoked /
     confidence = composite.

Guardrails baked in:
  * **Critical data (EPS/revenue) missing -> automatic no-go, Claude is NOT
    called and nothing is guessed** ("default NO").
  * **None-safe**: if Claude is unavailable / errors / returns None we STILL
    write a Decision (score-based verdict) instead of crashing.
  * **Frugal**: no Finnhub call on the read path (cache only); at most one live
    reaction fetch + at most one Claude call, and Claude only past the gate.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.analysis.models import (
    AIAnalysisResult,
    AnalysisType,
    AssetClass,
    TradeAction,
    Verdict,
)
from app.modules.equity.scoring import (
    VERDICT_NO_GO,
    ScoreResult,
    score_catalyst,
)
from app.modules.fundamentals.models import CompanyFundamentals, EarningsCalendar

logger = logging.getLogger("cloud_ai_trading.equity_research")
settings = get_settings()

# Equity Decisions are Alpaca-sourced (US stocks), mirroring the crypto path's
# exchange_type tag so the dashboard can tell the two feeds apart.
EQUITY_EXCHANGE_TYPE = "alpaca"

# How many recent daily bars to pull for the reaction calculation (small — a
# couple of weeks is plenty to bracket a report date).
REACTION_CANDLE_LIMIT = 15

# Injectable async fetcher type: (symbol, report_date, report_time) -> reaction %.
ReactionFetcher = Callable[[str, date, Optional[str]], Awaitable[Optional[float]]]
# Injectable async Claude analyzer type: keyword-only call -> dict | None.
ClaudeAnalyzer = Callable[..., Awaitable[Optional[dict]]]


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested directly)                                          #
# --------------------------------------------------------------------------- #
def _to_float(value) -> Optional[float]:
    """Best-effort float (handles Decimal / str / None). Never raises."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def beat_pct(actual, estimate) -> Optional[float]:
    """Surprise % = (actual - estimate) / |estimate| * 100.

    Returns None when either side is missing OR the estimate is zero (a
    percentage beat is undefined against a zero base — we do NOT fabricate one).
    """
    a = _to_float(actual)
    e = _to_float(estimate)
    if a is None or e is None or e == 0:
        return None
    return (a - e) / abs(e) * 100.0


def business_days_ago(report_date: Optional[date], today: Optional[date] = None) -> Optional[int]:
    """Approx trading days between ``report_date`` and ``today`` (weekdays only).

    Holidays are ignored (conservative: a real holiday makes this a slight
    over-count, which can only *tighten* the recency gate, never loosen it).
    Negative when the report date is in the future.
    """
    if report_date is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    if report_date == today:
        return 0
    step = 1 if report_date < today else -1
    lo, hi = (report_date, today) if step == 1 else (today, report_date)
    days = 0
    cursor = lo
    while cursor < hi:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Mon-Fri
            days += 1
    return days * step


def normalize_guidance(raw) -> Optional[str]:
    """Map a free-form Claude guidance signal onto the scorer's three states.

    Anything we don't confidently recognize -> None (treated as "unknown /
    neutral" by the scorer, never invented into a raise or a cut).
    """
    if not isinstance(raw, str):
        return None
    g = raw.strip().lower()
    if g in ("raised", "raise", "up", "upgrade", "hiked"):
        return "raised"
    if g in ("maintained", "reaffirmed", "inline", "in-line", "unchanged", "flat"):
        return "maintained"
    if g in ("cut", "lowered", "down", "reduced", "slashed", "guidance cut"):
        return "cut"
    return None


def build_completeness(
    *,
    eps_beat: Optional[float],
    rev_beat: Optional[float],
    reaction: Optional[float],
    report_date: Optional[date],
    guidance: Optional[str],
    ai_invoked: bool,
) -> dict:
    """Present-or-missing flag per datum, for the dashboard's audit column."""
    return {
        "eps_beat_pct": eps_beat is not None,
        "revenue_beat_pct": rev_beat is not None,
        "earnings_reaction_pct": reaction is not None,
        "earnings_date": report_date is not None,
        "guidance": guidance is not None,
        "ai_qualitative": bool(ai_invoked),
    }


def _clean_reasons(result: ScoreResult, extra: Optional[list[str]] = None) -> str:
    parts = list(result.reasons)
    if extra:
        parts.extend(extra)
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# Cache reads (DB) + live reaction fetch                                        #
# --------------------------------------------------------------------------- #
async def _load_fa(
    db: AsyncSession, symbol: str
) -> tuple[Optional[EarningsCalendar], Optional[CompanyFundamentals]]:
    """Read the latest REPORTED earnings row + the company profile from cache."""
    earnings_stmt = (
        select(EarningsCalendar)
        .where(
            EarningsCalendar.symbol == symbol,
            or_(
                EarningsCalendar.eps_actual.isnot(None),
                EarningsCalendar.rev_actual.isnot(None),
            ),
        )
        .order_by(desc(EarningsCalendar.report_date))
        .limit(1)
    )
    earnings = (await db.execute(earnings_stmt)).scalars().first()

    fundamentals_stmt = (
        select(CompanyFundamentals)
        .where(CompanyFundamentals.symbol == symbol)
        .limit(1)
    )
    fundamentals = (await db.execute(fundamentals_stmt)).scalars().first()
    return earnings, fundamentals


async def _default_reaction_fetcher(
    symbol: str, report_date: date, report_time: Optional[str]
) -> Optional[float]:
    """Earnings-day price reaction % from daily bars — best-effort, None-safe.

    The reaction lands on the report day for a before-market-open (bmo) report,
    otherwise on the next trading day (after-market-close / unknown session).
    Reaction % = (reaction-day close - prior close) / prior close * 100. Any
    missing data / API hiccup resolves to None (a non-critical signal), never an
    exception.
    """
    try:
        # Imported lazily so the pure/scoring path never pulls in market/httpx.
        from app.modules.market.service import MarketService

        candles = await MarketService.get_stock_candles(
            symbol, "1d", REACTION_CANDLE_LIMIT
        )
        if not candles or len(candles) < 2:
            return None

        # Map each candle to its calendar date (candles are ascending by time).
        dated = []
        for c in candles:
            ts = c.get("timestamp")
            close = c.get("close")
            if ts is None or close is None:
                continue
            d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
            dated.append((d, float(close)))
        if len(dated) < 2:
            return None

        session = (report_time or "").strip().lower()
        want_same_day = session in ("bmo", "before market open")

        # Find the index of the reaction day.
        idx = None
        for i, (d, _close) in enumerate(dated):
            if want_same_day and d >= report_date:
                idx = i
                break
            if not want_same_day and d > report_date:
                idx = i
                break
        if idx is None or idx == 0:
            return None

        prior_close = dated[idx - 1][1]
        reaction_close = dated[idx][1]
        if prior_close == 0:
            return None
        return (reaction_close - prior_close) / prior_close * 100.0
    except Exception as exc:  # pragma: no cover - defensive; reaction is optional
        logger.warning("reaction fetch failed for %s: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# Claude qualitative layer (mirrors analysis/claude.py) — None-safe             #
# --------------------------------------------------------------------------- #
QUALITATIVE_SYSTEM_PROMPT = (
    "You are a conservative equity research analyst. You read recent company "
    "news and summarize the QUALITATIVE picture behind an earnings report: the "
    "forward guidance direction and any material news. You never invent facts. "
    "Respond in valid JSON only, with the exact structure requested. If the "
    "guidance direction is not clearly stated in the material provided, return "
    "\"unknown\" — do not guess."
)


def build_qualitative_prompt(symbol: str, context: dict, news: list[dict]) -> str:
    """Prompt Claude to extract guidance direction + news read from headlines."""
    headlines = []
    for item in (news or [])[:15]:
        if not isinstance(item, dict):
            continue
        head = item.get("headline") or item.get("summary") or ""
        if head:
            headlines.append(f"- {head}")
    news_block = "\n".join(headlines) if headlines else "- (no recent headlines provided)"

    return f"""Assess the qualitative picture for **{symbol}** after its latest earnings report.

## Structured signals already computed
- EPS beat: {context.get('eps_beat_pct', 'N/A')}%
- Revenue beat: {context.get('rev_beat_pct', 'N/A')}%
- Earnings-day price reaction: {context.get('reaction_pct', 'N/A')}%
- Trading days since report: {context.get('earnings_days_ago', 'N/A')}
- Structured composite score (0-100): {context.get('composite', 'N/A')}

## Recent company news headlines
{news_block}

## Required Output (JSON only, no markdown)
{{
  "guidance": "raised" | "maintained" | "cut" | "unknown",
  "news_sentiment": "positive" | "neutral" | "negative",
  "reasoning": "2-3 sentence qualitative read grounded ONLY in the material above",
  "key_factors": ["factor1", "factor2"]
}}"""


async def research_qualitative_with_claude(
    *, symbol: str, context: dict, news: list[dict]
) -> Optional[dict]:
    """Call Claude for the qualitative read. Returns dict on success, else None.

    Mirrors ``analysis/claude.py``: missing key / API error / parse failure all
    resolve to None so the caller stays None-safe.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured; skipping Claude qualitative read")
        return None

    prompt = build_qualitative_prompt(symbol, context, news)
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=15.0)
        message = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=512,
            system=QUALITATIVE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text.strip()
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
            else:
                logger.error("Failed to parse Claude qualitative response: %s", response_text[:200])
                return None

        usage = message.usage
        result["tokens_used"] = usage.input_tokens + usage.output_tokens
        result["api_cost"] = round(
            usage.input_tokens * 0.80 / 1_000_000 + usage.output_tokens * 4 / 1_000_000, 6
        )
        result["provider"] = "claude"
        return result
    except Exception as exc:
        logger.error("Claude qualitative API error for %s: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# Main entry: research one candidate -> persist one Decision                    #
# --------------------------------------------------------------------------- #
async def research_equity(
    db: AsyncSession,
    user_id: UUID,
    symbol: str,
    *,
    analysis_type: AnalysisType = AnalysisType.SCHEDULED,
    reaction_fetcher: Optional[ReactionFetcher] = None,
    claude_analyzer: Optional[ClaudeAnalyzer] = None,
    news: Optional[list[dict]] = None,
) -> AIAnalysisResult:
    """Research one equity candidate and persist exactly one equity Decision.

    ``reaction_fetcher`` and ``claude_analyzer`` are injectable for testing (so a
    unit test drives the whole flow with mock FA + mock Claude and never touches
    the network). ``news`` (optional) is the qualitative material fed to Claude;
    when omitted, an empty list is used (Claude then reads the structured signals
    only — still None-safe).
    """
    symbol = symbol.upper()

    # --- Step 1: structured FA from cache ------------------------------------
    earnings, fundamentals = await _load_fa(db, symbol)

    report_date = earnings.report_date if earnings is not None else None
    report_time = earnings.time if earnings is not None else None
    eps_beat = beat_pct(
        getattr(earnings, "eps_actual", None), getattr(earnings, "eps_estimate", None)
    ) if earnings is not None else None
    rev_beat = beat_pct(
        getattr(earnings, "rev_actual", None), getattr(earnings, "rev_estimate", None)
    ) if earnings is not None else None
    earnings_days_ago = business_days_ago(report_date)

    # --- Step 2: earnings-day reaction (non-critical, live, None-safe) --------
    reaction_pct: Optional[float] = None
    if report_date is not None and (eps_beat is not None or rev_beat is not None):
        fetch = reaction_fetcher or _default_reaction_fetcher
        try:
            reaction_pct = await fetch(symbol, report_date, report_time)
        except Exception as exc:  # pragma: no cover - fetcher already guards
            logger.warning("reaction fetch error for %s: %s", symbol, exc)
            reaction_pct = None

    # --- Step 3: structured score (guidance unknown at this stage) -----------
    result = score_catalyst(
        eps_beat_pct=eps_beat,
        rev_beat_pct=rev_beat,
        guidance=None,
        reaction_pct=reaction_pct,
        earnings_days_ago=earnings_days_ago,
    )

    # --- Step 4: Claude qualitative — only past the gate, None-safe -----------
    claude_response: dict = {}
    ai_invoked = False
    ai_skip_reason: Optional[str] = None
    guidance: Optional[str] = None
    extra_reasons: list[str] = []

    if not result.scorable:
        # Critical EPS/revenue missing -> auto no-go, do NOT call Claude.
        ai_skip_reason = "critical_data_missing"
    elif result.verdict == VERDICT_NO_GO:
        # Below watch-band OR recency gate failed (stale earnings) -> not worth a
        # qualitative read; the score already decided no-go.
        ai_skip_reason = "score_or_recency_gate_not_passed"
    else:
        # Passed recency gate AND scored >= watch -> qualitative read can matter.
        analyzer = claude_analyzer or research_qualitative_with_claude
        context = {
            "eps_beat_pct": None if eps_beat is None else round(eps_beat, 2),
            "rev_beat_pct": None if rev_beat is None else round(rev_beat, 2),
            "reaction_pct": None if reaction_pct is None else round(reaction_pct, 2),
            "earnings_days_ago": earnings_days_ago,
            "composite": result.composite,
        }
        claude_out = await analyzer(symbol=symbol, context=context, news=news or [])
        if claude_out is None:
            # None-safe: Claude unavailable/error -> keep the score-based verdict.
            ai_skip_reason = "ai_unavailable"
            extra_reasons.append("Qualitative read unavailable (Claude None) -> score-only verdict.")
        else:
            ai_invoked = True
            claude_response = claude_out
            guidance = normalize_guidance(claude_out.get("guidance"))
            if guidance is not None:
                # Re-score with the qualitative guidance so Claude actually moves
                # the number (raise lifts, cut can pull down hard).
                result = score_catalyst(
                    eps_beat_pct=eps_beat,
                    rev_beat_pct=rev_beat,
                    guidance=guidance,
                    reaction_pct=reaction_pct,
                    earnings_days_ago=earnings_days_ago,
                )
                extra_reasons.append(f"Claude guidance '{guidance}' applied -> re-scored.")
            else:
                extra_reasons.append("Claude qualitative read added (no confident guidance signal).")
            reasoning = claude_out.get("reasoning")
            if reasoning:
                extra_reasons.append(f"Qualitative: {reasoning}")

    # --- Step 5: assemble + persist the Decision -----------------------------
    verdict = Verdict(result.verdict)
    confidence = int(round(result.composite))
    action = TradeAction.BUY if verdict == Verdict.GO else TradeAction.HOLD

    indicators_snapshot = {
        "asset_class": "equity",
        "symbol": symbol,
        "name": getattr(fundamentals, "name", None),
        "sector": getattr(fundamentals, "sector", None),
        "is_sp500": bool(getattr(fundamentals, "is_sp500", False)) if fundamentals else None,
        "eps_actual": _to_float(getattr(earnings, "eps_actual", None)),
        "eps_estimate": _to_float(getattr(earnings, "eps_estimate", None)),
        "eps_beat_pct": None if eps_beat is None else round(eps_beat, 3),
        "rev_actual": _to_float(getattr(earnings, "rev_actual", None)),
        "rev_estimate": _to_float(getattr(earnings, "rev_estimate", None)),
        "rev_beat_pct": None if rev_beat is None else round(rev_beat, 3),
        "reaction_pct": None if reaction_pct is None else round(reaction_pct, 3),
        "report_date": report_date.isoformat() if report_date else None,
        "report_time": report_time,
        "earnings_days_ago": earnings_days_ago,
        "guidance_applied": guidance,
        "score": result.to_dict(),
    }

    data_completeness = build_completeness(
        eps_beat=eps_beat,
        rev_beat=rev_beat,
        reaction=reaction_pct,
        report_date=report_date,
        guidance=guidance,
        ai_invoked=ai_invoked,
    )

    verdict_reason = _clean_reasons(result, extra_reasons)

    analysis = AIAnalysisResult(
        user_id=user_id,
        symbol=symbol,
        exchange_type=EQUITY_EXCHANGE_TYPE,
        analysis_type=analysis_type,
        indicators_snapshot=indicators_snapshot,
        claude_response=claude_response,
        action=action,
        confidence=confidence,
        prompt_used=None,
        tokens_used=claude_response.get("tokens_used", 0) if claude_response else 0,
        api_cost=claude_response.get("api_cost", 0) if claude_response else 0,
        asset_class=AssetClass.EQUITY,
        verdict=verdict,
        verdict_reason=verdict_reason,
        data_completeness=data_completeness,
        ai_invoked=ai_invoked,
        ai_skip_reason=ai_skip_reason,
    )
    db.add(analysis)
    await db.flush()
    await db.refresh(analysis)
    return analysis
