"""EXEC-auto-task: Celery task that auto-executes approved equity Decisions.

One weekday task, scheduled just AFTER the equity market-open run (which stamps
"order intent" on today's GO Decisions):

  execution.auto_execute_equity  (14:35 UTC ~ 09:35 EST / 10:35 EDT)
      Scan for ``asset_class=equity`` + ``verdict=go`` + not-yet-executed
      (``position_id IS NULL``) Decisions and, strictly inside the equity risk
      BUDGET (weekly BUYs < 3, open positions < the concurrency cap), hand each
      one to ``execution.service.execute_decision`` — which places a **paper**
      Alpaca BUY and links a ``Position`` back onto the Decision.

Hard money-path guardrails (enforced in code here AND again in the service):

  * **Paper only, live fully off.** This task never selects a trading mode and
    never builds a live adapter. ``execute_decision`` forces ``paper=True`` and
    REFUSES a LIVE-mode connection; we only ever open the paper order path.
  * **Alpaca equities only — crypto/Binance never touched.** The scan query
    filters ``asset_class == equity``; a crypto/Binance Decision can never enter
    the loop, and ``execute_decision`` refuses it again before any adapter is
    constructed. A Binance adapter is never imported here. (Binance has no testnet
    isolation, so even a "simulated" Binance order hits the real exchange — a
    real-money trap; this task must never scan its way into one.)
  * **Risk budget is pre-order and mandatory.** Before any order we compute the
    remaining weekly/concurrency budget from the DB; if it is spent we skip the
    run WITHOUT building an adapter. Inside the loop each execution also passes
    the full equity risk gate (weekly <= 3, single <= 5%, concurrency <= 5, daily
    -2% stop) in ``execute_decision``; the loop stops once the budget is used up.
  * **Idempotent.** Only ``position_id IS NULL`` Decisions are scanned, and
    ``execute_decision`` skips any Decision already linked to a Position — no
    double placement.
  * **Graceful on failure.** An Alpaca hiccup / limit / any per-Decision error is
    logged and skipped; one bad name never aborts the run, and nothing here ever
    raises out of the Celery task.

  * **Don't over-scan.** GO Decisions are intentionally rare, and a hard
    ``MAX_SCAN`` cap plus the weekly budget bound the loop regardless.

Target account: the same single account equity Decisions attach to (first active
super-admin, else earliest active user) — reused from ``equity_tasks`` so the
scan reads exactly the feed the equity research/market-open tasks wrote.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.analysis.models import AIAnalysisResult, AssetClass, Verdict
from app.modules.equity.risk_config import DEFAULT_EQUITY_RISK_LIMIT, EquityRiskLimit
from app.modules.execution.service import (
    STATUS_EXECUTED,
    _count_open_equity_positions,
    _count_weekly_equity_buys,
    _resolve_alpaca_paper_adapter,
    execute_decision,
)
from app.tasks.equity_tasks import _resolve_target_user, et_today, is_us_trading_day

logger = logging.getLogger("cloud_ai_trading.tasks.execution")

# Hard cap on how many pending GO Decisions we even look at in one run. GO names
# are already scarce; this bounds the worst case independent of the risk budget.
MAX_SCAN = 20


async def _pending_go_equity_decisions(
    session, user_id, *, limit_rows: int = MAX_SCAN
) -> list[AIAnalysisResult]:
    """Equity + verdict=go + not-yet-executed (position_id IS NULL) Decisions for
    this account, highest confidence first. This is the ONLY scan — asset_class is
    pinned to EQUITY so crypto/Binance Decisions can never enter the loop."""
    stmt = (
        select(AIAnalysisResult)
        .where(
            AIAnalysisResult.user_id == user_id,
            AIAnalysisResult.asset_class == AssetClass.EQUITY,
            AIAnalysisResult.verdict == Verdict.GO,
            AIAnalysisResult.position_id.is_(None),
        )
        .order_by(
            AIAnalysisResult.confidence.desc(),
            AIAnalysisResult.created_at.desc(),
        )
        .limit(limit_rows)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _auto_execute_equity(
    session,
    *,
    today: Optional[date] = None,
    adapter=None,
    limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> dict[str, Any]:
    """Core: scan pending GO equity Decisions and execute within the risk budget."""
    today = today or et_today()
    if not is_us_trading_day(today):
        logger.info(
            "execution.auto_execute_equity: %s is not a US trading day; skipping", today
        )
        return {"skipped": "non_trading_day", "as_of": today.isoformat()}

    user_id = await _resolve_target_user(session)
    if user_id is None:
        logger.warning("execution.auto_execute_equity: no target user; skipping")
        return {"skipped": "no_user"}

    # Pre-order BUDGET: how many new entries may we still open this week / at once?
    open_positions = await _count_open_equity_positions(session, user_id)
    weekly_buys = await _count_weekly_equity_buys(session, user_id)
    remaining = min(
        limit.max_trades_per_week - weekly_buys,
        limit.max_open_positions - open_positions,
    )
    if remaining <= 0:
        logger.info(
            "execution.auto_execute_equity: budget spent (open=%s/%s, week=%s/%s); skipping",
            open_positions,
            limit.max_open_positions,
            weekly_buys,
            limit.max_trades_per_week,
        )
        return {
            "as_of": today.isoformat(),
            "skipped": "budget_spent",
            "open_positions": open_positions,
            "weekly_buys": weekly_buys,
        }

    candidates = await _pending_go_equity_decisions(session, user_id)
    if not candidates:
        logger.info("execution.auto_execute_equity: no pending GO equity Decisions for %s", today)
        return {"as_of": today.isoformat(), "candidates": 0, "executed": 0}

    # Resolve the PAPER Alpaca adapter ONCE (never build a live/Binance adapter).
    # Only reached when there is real work AND budget — no wasted connection.
    if adapter is None:
        adapter, err = await _resolve_alpaca_paper_adapter(session, user_id)
        if adapter is None:
            logger.warning(
                "execution.auto_execute_equity: no paper adapter (%s); skipping", err
            )
            return {
                "as_of": today.isoformat(),
                "candidates": len(candidates),
                "executed": 0,
                "skipped": err or "no_adapter",
            }

    executed = 0
    rejected = 0
    errors = 0
    for decision in candidates:
        if remaining <= 0:
            logger.info("execution.auto_execute_equity: weekly/concurrency budget reached; stop")
            break
        try:
            result = await execute_decision(
                session, decision, adapter=adapter, risk_limit=limit
            )
        except Exception as exc:  # Alpaca / DB hiccup — skip this name, keep going
            errors += 1
            logger.error(
                "execution.auto_execute_equity: execution error for %s: %s",
                getattr(decision, "symbol", "?"),
                exc,
            )
            await session.rollback()
            continue

        if result.status == STATUS_EXECUTED:
            await session.commit()  # persist Position + position_id link
            executed += 1
            remaining -= 1
            logger.info(
                "execution.auto_execute_equity: executed %s -> position %s (%s left)",
                result.decision_id,
                result.position_id,
                remaining,
            )
        else:
            # Business rejection/skip (non-go slipped in, risk gate, no price, ...).
            # execute_decision made no committed change; roll back to stay clean.
            rejected += 1
            await session.rollback()
            logger.info(
                "execution.auto_execute_equity: %s for %s -> %s",
                result.status,
                getattr(decision, "symbol", "?"),
                result.reason,
            )

    summary = {
        "as_of": today.isoformat(),
        "candidates": len(candidates),
        "executed": executed,
        "rejected": rejected,
        "errors": errors,
        "budget_left": remaining,
    }
    logger.info("execution.auto_execute_equity summary: %s", summary)
    return summary


def _run(core) -> dict[str, Any]:
    """Drive the task core with a fresh session, swallowing any last-resort error."""

    async def _inner() -> dict[str, Any]:
        async with CeleryAsyncSessionLocal() as session:
            try:
                return await core(session)
            except Exception as exc:  # last-resort guardrail — never crash the task
                logger.error("auto_execute_equity failed: %s", exc)
                await session.rollback()
                return {"error": str(exc)}

    return asyncio.run(_inner())


@shared_task(name="execution.auto_execute_equity")
def auto_execute_equity() -> dict[str, Any]:
    """Auto-execute today's approved equity Decisions on Alpaca paper, in budget."""
    return _run(_auto_execute_equity)
