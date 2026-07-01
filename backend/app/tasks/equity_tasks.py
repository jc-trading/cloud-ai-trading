"""EQUITY-schedule: Celery Beat tasks that drive the equity research agent.

Three weekday, US-market-hours tasks (registered in ``tasks/celery_app.py``):

  equity.pre_market   (12:00 UTC ~ 07:00 EST / 08:00 EDT, before the 09:30 open)
      Select the day's candidates (earnings-driven ∪ standing watchlist) and run
      the equity_research agent on each -> one auditable ``asset_class=equity``
      Decision per name, in the same unified feed the crypto pipeline writes.
  equity.market_open  (14:30 UTC ~ 09:30 EST / 10:30 EDT, at/after the ET open)
      Read today's GO equity Decisions and stamp an "order intent" marker on each
      (the "打算下单" record). This does NOT place any order — real execution is
      Phase 4. Idempotent: an already-stamped Decision is skipped.
  equity.eod          (21:30 UTC ~ 16:30 EST / 17:30 EDT, after the 16:00 close)
      Summarize the day: Decision counts by verdict + the GO / intent names +
      how many times Claude was actually invoked. Read-only aggregation + a
      structured log line; it writes nothing new.

Guardrails (see task spec):
  * **Trading-day only.** The crontab restricts to Mon-Fri; each task ALSO skips
    US market holidays via a pure NYSE full-closure calendar (``is_us_trading_day``),
    so it only fires on real trading days.
  * **Frugal on the free tiers.** Candidate selection is cache-only (ZERO Finnhub /
    Claude). Research runs ONCE per day for a SINGLE target account (not per user),
    and the research agent itself only calls Claude for a candidate that clears its
    recency + score gate — so Claude fires only for the handful of names that just
    reported earnings, never the whole watchlist. A hard ``MAX_CANDIDATES`` cap
    backs this up.
  * **None-safe.** A missing target user, an empty candidate pool, or any per-symbol
    error is logged and skipped; one bad symbol never aborts the run, and nothing
    here ever raises out of the Celery task.

ASSUMPTIONS (documented, not guessed):
  * Equity Decisions attach to ONE canonical account — the first active
    super-admin (else the earliest active user). Rationale: the candidate universe
    is global (S&P 500), so it is not a per-user watchlist; researching it once per
    account keeps us inside the Claude free tier instead of multiplying calls by the
    user count. If a multi-tenant equity feed is ever needed, research once and fan
    the row out per user — do NOT re-run Claude per user.
  * ``pre_market`` passes no ``news`` to the research agent, so Claude reads the
    structured signals only (still None-safe). Fetching per-name Finnhub news would
    add calls against the free tier; wire it in later if the quota allows.
  * UTC schedule times are chosen to hold year-round across EST/EDT (the app runs
    UTC and a fixed crontab cannot follow DST). See ``tasks/celery_app.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.analysis.models import (
    AIAnalysisResult,
    AnalysisType,
    AssetClass,
    Verdict,
)
from app.modules.equity.research import research_equity
from app.modules.equity.universe import select_daily_candidates

logger = logging.getLogger("cloud_ai_trading.tasks.equity")

# US market timezone (handles EST/EDT automatically for the trading-day check).
ET = ZoneInfo("America/New_York")

# Hard safety cap on how many candidates we research in one run. The pool is
# already small (standing watchlist ∪ names that just reported), but this bounds
# the worst-case Claude spend even if the watchlist grows.
MAX_CANDIDATES = 30


# --------------------------------------------------------------------------- #
# Pure NYSE trading-calendar helpers (unit-tested directly)                    #
# --------------------------------------------------------------------------- #
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0..Sun=6) of ``month``. ``n=-1`` -> last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    # last occurrence: walk back from the last day of the month
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def good_friday(year: int) -> date:
    """Good Friday = the Friday two days before Easter Sunday."""
    return _easter(year) - timedelta(days=2)


def _observed(d: date) -> date:
    """NYSE observed date: Saturday holiday -> Friday, Sunday holiday -> Monday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def us_market_holidays(year: int) -> set[date]:
    """Full-day NYSE closures for ``year`` (regular sessions; half-days excluded)."""
    holidays = {
        _observed(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, 0, 3),           # MLK Jr. Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),           # Washington's Birthday (3rd Mon Feb)
        good_friday(year),                     # Good Friday
        _nth_weekday(year, 5, 0, -1),          # Memorial Day (last Mon May)
        _observed(date(year, 7, 4)),           # Independence Day
        _nth_weekday(year, 9, 0, 1),           # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),          # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),         # Christmas Day
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth (from 2022)
    return holidays


def is_us_market_holiday(d: date) -> bool:
    """Is ``d`` a full-day NYSE closure? (Adjacent years unioned for observed
    dates that spill across a year boundary, e.g. a Jan-1-observed on Dec 31.)"""
    return (
        d in us_market_holidays(d.year)
        or d in us_market_holidays(d.year - 1)
        or d in us_market_holidays(d.year + 1)
    )


def is_us_trading_day(d: date) -> bool:
    """A weekday that is not a US market holiday."""
    return d.weekday() < 5 and not is_us_market_holiday(d)


def et_today() -> date:
    """Today's date in US-Eastern (the market's calendar day)."""
    return datetime.now(ET).date()


def _et_day_start_utc(d: date) -> datetime:
    """UTC instant of ET-midnight for ``d`` — the lower bound for 'created today'."""
    return datetime.combine(d, time.min, tzinfo=ET).astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Target-account resolution                                                    #
# --------------------------------------------------------------------------- #
async def _resolve_target_user(session) -> Optional[UUID]:
    """The single account equity Decisions attach to: first active super-admin,
    else the earliest active user. ``None`` when there are no users."""
    from app.modules.auth.models import User, UserRole

    stmt = (
        select(User.id)
        .where(User.is_active.is_(True))
        .order_by(
            (User.role == UserRole.SUPER_ADMIN).desc(),  # super-admins first
            User.created_at.asc(),                        # then earliest account
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _today_equity_decisions(
    session, user_id: UUID, day: date, *, verdict: Optional[Verdict] = None
) -> list[AIAnalysisResult]:
    """Equity Decisions this account produced on ``day`` (optionally verdict-filtered)."""
    stmt = select(AIAnalysisResult).where(
        AIAnalysisResult.user_id == user_id,
        AIAnalysisResult.asset_class == AssetClass.EQUITY,
        AIAnalysisResult.created_at >= _et_day_start_utc(day),
    )
    if verdict is not None:
        stmt = stmt.where(AIAnalysisResult.verdict == verdict)
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# Task cores (async, take a session + optional injectable ``today``)           #
# --------------------------------------------------------------------------- #
async def _pre_market(session, *, today: Optional[date] = None) -> dict[str, Any]:
    """Select today's candidates and research each -> one equity Decision apiece."""
    today = today or et_today()
    if not is_us_trading_day(today):
        logger.info("equity.pre_market: %s is not a US trading day; skipping", today)
        return {"skipped": "non_trading_day", "as_of": today.isoformat()}

    user_id = await _resolve_target_user(session)
    if user_id is None:
        logger.warning("equity.pre_market: no target user; skipping")
        return {"skipped": "no_user"}

    daily = await select_daily_candidates(session, today=today)
    symbols = daily.symbols[:MAX_CANDIDATES]
    if not symbols:
        logger.info("equity.pre_market: no candidates for %s", today)
        return {"as_of": today.isoformat(), "candidates": 0, "written": 0}

    written = 0
    for symbol in symbols:
        try:
            decision = await research_equity(
                session, user_id, symbol, analysis_type=AnalysisType.SCHEDULED
            )
            await session.commit()
            written += 1
            logger.info(
                "equity.pre_market: %s -> %s (confidence %s, ai_invoked=%s)",
                symbol,
                decision.verdict.value,
                decision.confidence,
                decision.ai_invoked,
            )
        except Exception as exc:  # one bad symbol must not abort the run
            logger.error("equity.pre_market: research failed for %s: %s", symbol, exc)
            await session.rollback()

    logger.info(
        "equity.pre_market: %s candidate(s), %s Decision(s) written for %s",
        len(symbols),
        written,
        today,
    )
    return {"as_of": today.isoformat(), "candidates": len(symbols), "written": written}


async def _market_open(session, *, today: Optional[date] = None) -> dict[str, Any]:
    """Stamp an 'order intent' marker on today's GO equity Decisions.

    Intent ONLY — no order is placed here (real execution is Phase 4). Idempotent:
    a Decision already carrying an ``order_intent`` is left untouched.
    """
    today = today or et_today()
    if not is_us_trading_day(today):
        logger.info("equity.market_open: %s is not a US trading day; skipping", today)
        return {"skipped": "non_trading_day", "as_of": today.isoformat()}

    user_id = await _resolve_target_user(session)
    if user_id is None:
        logger.warning("equity.market_open: no target user; skipping")
        return {"skipped": "no_user"}

    go_decisions = await _today_equity_decisions(
        session, user_id, today, verdict=Verdict.GO
    )

    stamped = 0
    for decision in go_decisions:
        snapshot = dict(decision.indicators_snapshot or {})
        if snapshot.get("order_intent"):  # idempotent — already marked
            continue
        snapshot["order_intent"] = {
            "intent": "buy",
            "status": "planned",          # planned, NOT executed
            "executed": False,
            "phase": "phase3_intent_only",
            "note": "Marked at market open; real execution is Phase 4.",
            "marked_at": datetime.now(timezone.utc).isoformat(),
            "confidence": decision.confidence,
        }
        decision.indicators_snapshot = snapshot  # reassign so JSONB is flagged dirty
        stamped += 1
        logger.info(
            "equity.market_open: order intent stamped for %s (confidence %s)",
            decision.symbol,
            decision.confidence,
        )

    await session.commit()
    logger.info(
        "equity.market_open: %s GO Decision(s), %s new intent(s) stamped for %s",
        len(go_decisions),
        stamped,
        today,
    )
    return {"as_of": today.isoformat(), "go": len(go_decisions), "stamped": stamped}


async def _eod(session, *, today: Optional[date] = None) -> dict[str, Any]:
    """Summarize the day's equity Decisions (read-only; writes nothing new)."""
    today = today or et_today()
    if not is_us_trading_day(today):
        logger.info("equity.eod: %s is not a US trading day; skipping", today)
        return {"skipped": "non_trading_day", "as_of": today.isoformat()}

    user_id = await _resolve_target_user(session)
    if user_id is None:
        logger.warning("equity.eod: no target user; skipping")
        return {"skipped": "no_user"}

    decisions = await _today_equity_decisions(session, user_id, today)

    counts = {"go": 0, "watch": 0, "no-go": 0}
    go_names: list[str] = []
    intent_names: list[str] = []
    ai_invocations = 0
    for d in decisions:
        verdict = d.verdict.value if d.verdict is not None else "no-go"
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict == "go":
            go_names.append(d.symbol)
        if d.ai_invoked:
            ai_invocations += 1
        if (d.indicators_snapshot or {}).get("order_intent"):
            intent_names.append(d.symbol)

    summary = {
        "as_of": today.isoformat(),
        "total": len(decisions),
        "counts": counts,
        "go_names": go_names,
        "intent_names": intent_names,
        "ai_invocations": ai_invocations,
    }
    logger.info("equity.eod summary: %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Celery task wrappers — never let a task crash                                 #
# --------------------------------------------------------------------------- #
def _run(core) -> dict[str, Any]:
    """Drive a task core with a fresh session, swallowing any last-resort error."""

    async def _inner() -> dict[str, Any]:
        async with CeleryAsyncSessionLocal() as session:
            try:
                return await core(session)
            except Exception as exc:  # last-resort guardrail
                logger.error("%s failed: %s", core.__name__, exc)
                await session.rollback()
                return {"error": str(exc)}

    return asyncio.run(_inner())


@shared_task(name="equity.pre_market")
def pre_market() -> dict[str, Any]:
    """Pre-market: research the day's equity candidates into unified Decisions."""
    return _run(_pre_market)


@shared_task(name="equity.market_open")
def market_open() -> dict[str, Any]:
    """Market open: stamp 'order intent' on today's GO equity Decisions (no execution)."""
    return _run(_market_open)


@shared_task(name="equity.eod")
def eod() -> dict[str, Any]:
    """EOD: summarize the day's equity Decisions."""
    return _run(_eod)
