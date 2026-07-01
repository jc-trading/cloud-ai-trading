"""Execution service — go-Decision -> risk gate -> Alpaca **paper** BUY -> Position.

This is the money path. It takes ONE approved equity Decision
(``ai_analysis_results`` row, ``asset_class=equity`` + ``verdict=go``) and, only
if every gate passes, places a **market BUY on Alpaca in PAPER mode** sized by the
equity risk SPEC, then records a ``Position`` and links it back to the Decision.

Hard safety guardrails (non-negotiable — enforced in code, not just docs):

  * **Paper only, live fully off.** The Alpaca adapter is always built with
    ``paper=True`` (paper-api endpoint). If the resolved connection is in LIVE
    trading mode we REFUSE — we never downgrade a live connection into a trade.
    No live order path exists here.
  * **Alpaca equities only.** ``asset_class`` must be ``equity``. Anything else —
    crypto / Binance — is refused BEFORE any adapter is touched, and a Binance
    adapter is never imported or constructed. (Binance has no testnet isolation,
    so even a "simulated" Binance order hits the real exchange — a real-money
    trap.)
  * **Risk gate is mandatory and pre-order.** Before any order: weekly BUY budget
    (<= 3/week), single-position cap (<= 5% equity), concurrency cap (<= 5 open),
    and the daily-loss stop (-2% equity). Any failing gate rejects the trade and
    records the reason — no order is sent.
  * **Idempotent.** A Decision already carrying a ``position_id`` was executed
    before and is skipped — we never double-place.

Everything is None-safe: a null/blank input, a missing account, a missing price,
or a failed order resolves to a structured rejection, never an exception.

NOTE on reuse (documented conflict, resolved faithfully): the task text says
"reuse ``risk/engine.py``". That engine is the **crypto** risk model (per-watchlist
``RiskLimit`` DB row, ``min_signal_strength``, concentration bands). The equity
SPEC gates (weekly <= 3, 5% single, 3-5 concurrency, daily -2%) live in
``equity/risk_config.py``, whose own docstring declares it the single source of
truth for equities and "entirely separate from the crypto RiskLimit". So the
equity path reuses ``equity/risk_config.py`` — using the crypto engine here would
apply the wrong limits. Flagged rather than silently followed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_api_key
from app.modules.analysis.models import AIAnalysisResult, AssetClass, TradeAction, Verdict
from app.modules.equity.risk_config import (
    DEFAULT_EQUITY_RISK_LIMIT,
    EquityRiskLimit,
    can_open_new_position,
    daily_loss_breached,
)
from app.modules.exchange.adapters.alpaca import AlpacaAdapter
from app.modules.exchange.adapters.base import ExchangeAdapter, OrderRequest
from app.modules.exchange.models import ExchangeConnection, ExchangeType, TradingMode
from app.modules.trading.models import Position
from app.modules.watchlist.models import Watchlist

logger = logging.getLogger("cloud_ai_trading.execution")


# --------------------------------------------------------------------------- #
# Result / status vocabulary                                                    #
# --------------------------------------------------------------------------- #
STATUS_EXECUTED = "executed"
STATUS_REJECTED = "rejected"
STATUS_SKIPPED = "skipped"


@dataclass
class ExecutionResult:
    """Structured outcome. Never raises for a business rejection — callers read
    ``executed`` / ``status`` / ``reason``."""

    status: str
    reason: str
    decision_id: Optional[UUID] = None
    position_id: Optional[UUID] = None
    order_id: Optional[str] = None
    quantity: Optional[float] = None
    entry_price: Optional[float] = None

    @property
    def executed(self) -> bool:
        return self.status == STATUS_EXECUTED

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "executed": self.executed,
            "decision_id": str(self.decision_id) if self.decision_id else None,
            "position_id": str(self.position_id) if self.position_id else None,
            "order_id": self.order_id,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
        }


@dataclass
class EquityRiskSnapshot:
    """Everything the equity gates need, gathered once. Injectable for tests so a
    unit test drives the gate logic without mocking every DB read."""

    account_equity: Optional[Decimal]
    reference_price: Optional[Decimal]
    open_positions: int
    trades_this_week: int
    day_realized_pnl: Decimal


# --------------------------------------------------------------------------- #
# Pure helpers                                                                  #
# --------------------------------------------------------------------------- #
def _to_decimal(value) -> Optional[Decimal]:
    """Best-effort Decimal (handles float / str / Decimal / None). Never raises."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def compute_share_quantity(
    account_equity,
    reference_price,
    limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> int:
    """Whole-share size for a new name = floor((5% of equity) / price).

    Floors to whole shares so the notional can never *exceed* the 5% single-name
    cap. Returns 0 when equity/price are missing or non-positive (caller rejects).
    """
    eq = _to_decimal(account_equity)
    px = _to_decimal(reference_price)
    if eq is None or px is None or eq <= 0 or px <= 0:
        return 0
    cap = eq * Decimal(str(limit.max_position_size_pct)) / Decimal("100")
    return int(cap // px)


def _is_equity_decision(decision: AIAnalysisResult) -> bool:
    """True only for a genuine equity Decision. Defense in depth: also reject if the
    exchange tag smells like a crypto venue (Binance), regardless of asset_class."""
    if decision.asset_class != AssetClass.EQUITY:
        return False
    exchange_tag = str(getattr(decision, "exchange_type", "") or "").lower()
    if "binance" in exchange_tag or "crypto" in exchange_tag:
        return False
    return True


def _week_start_utc(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())  # back to Monday 00:00


def _day_start_utc(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# DB / adapter resolution                                                       #
# --------------------------------------------------------------------------- #
async def _resolve_alpaca_paper_adapter(
    db: AsyncSession, user_id: UUID
) -> tuple[Optional[ExchangeAdapter], Optional[str]]:
    """Build the user's Alpaca adapter — **always paper**. Returns (adapter, None)
    on success or (None, reason). A LIVE-mode connection is refused outright: we
    never build a live adapter here."""
    stmt = (
        select(ExchangeConnection)
        .where(
            ExchangeConnection.user_id == user_id,
            ExchangeConnection.exchange_type == ExchangeType.ALPACA,
            ExchangeConnection.is_active.is_(True),
        )
        .order_by(ExchangeConnection.created_at.desc())
        .limit(1)
    )
    conn = (await db.execute(stmt)).scalar_one_or_none()
    if conn is None:
        return None, "no_active_alpaca_connection"
    if conn.trading_mode == TradingMode.LIVE:
        # Guardrail: live is fully off. Refuse rather than downgrade.
        return None, "live_mode_refused"

    api_key = decrypt_api_key(conn.api_key_encrypted)
    api_secret = decrypt_api_key(conn.api_secret_encrypted)
    # paper=True is FORCED — the only order path this service ever opens.
    return AlpacaAdapter(api_key, api_secret, paper=True), None


async def _resolve_watchlist_id(db: AsyncSession, user_id: UUID) -> Optional[UUID]:
    """The user's default (earliest) watchlist — Positions require a watchlist FK."""
    stmt = (
        select(Watchlist.id)
        .where(Watchlist.user_id == user_id)
        .order_by(Watchlist.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _count_open_equity_positions(db: AsyncSession, user_id: UUID) -> int:
    stmt = (
        select(func.count(Position.id))
        .select_from(Position)
        .join(AIAnalysisResult, AIAnalysisResult.position_id == Position.id)
        .where(
            AIAnalysisResult.user_id == user_id,
            AIAnalysisResult.asset_class == AssetClass.EQUITY,
            Position.status == "open",
        )
    )
    return (await db.execute(stmt)).scalar() or 0


async def _count_weekly_equity_buys(db: AsyncSession, user_id: UUID) -> int:
    week_start = _week_start_utc()
    stmt = (
        select(func.count(Position.id))
        .select_from(Position)
        .join(AIAnalysisResult, AIAnalysisResult.position_id == Position.id)
        .where(
            AIAnalysisResult.user_id == user_id,
            AIAnalysisResult.asset_class == AssetClass.EQUITY,
            Position.entry_date >= week_start,
        )
    )
    return (await db.execute(stmt)).scalar() or 0


async def _equity_day_realized_pnl(db: AsyncSession, user_id: UUID) -> Decimal:
    day_start = _day_start_utc()
    stmt = (
        select(func.sum((Position.exit_price - Position.entry_price) * Position.quantity))
        .select_from(Position)
        .join(AIAnalysisResult, AIAnalysisResult.position_id == Position.id)
        .where(
            AIAnalysisResult.user_id == user_id,
            AIAnalysisResult.asset_class == AssetClass.EQUITY,
            Position.status == "closed",
            Position.exit_date >= day_start,
        )
    )
    pnl = (await db.execute(stmt)).scalar()
    return _to_decimal(pnl) or Decimal("0")


async def gather_risk_snapshot(
    db: AsyncSession,
    user_id: UUID,
    symbol: str,
    adapter: ExchangeAdapter,
) -> EquityRiskSnapshot:
    """Collect account equity + a reference price (from the paper adapter) and the
    three equity tallies (open, weekly, today's realized P&L). None-safe: any
    adapter/data hiccup leaves that field None/0 and the gates decide."""
    account_equity: Optional[Decimal] = None
    reference_price: Optional[Decimal] = None
    try:
        balance = await adapter.get_balance()
        account_equity = _to_decimal((balance or {}).get("equity"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("execution: get_balance failed: %s", exc)
    try:
        ticker = await adapter.get_ticker(symbol)
        reference_price = _to_decimal((ticker or {}).get("last"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("execution: get_ticker failed for %s: %s", symbol, exc)

    open_positions = await _count_open_equity_positions(db, user_id)
    trades_this_week = await _count_weekly_equity_buys(db, user_id)
    day_realized_pnl = await _equity_day_realized_pnl(db, user_id)

    return EquityRiskSnapshot(
        account_equity=account_equity,
        reference_price=reference_price,
        open_positions=open_positions,
        trades_this_week=trades_this_week,
        day_realized_pnl=day_realized_pnl,
    )


# --------------------------------------------------------------------------- #
# Main entry                                                                    #
# --------------------------------------------------------------------------- #
async def execute_decision(
    db: AsyncSession,
    decision: Optional[AIAnalysisResult],
    *,
    adapter: Optional[ExchangeAdapter] = None,
    risk_snapshot: Optional[EquityRiskSnapshot] = None,
    risk_limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> ExecutionResult:
    """Execute ONE approved equity Decision on Alpaca paper, gated by equity risk.

    Args:
        db: async session (caller owns the transaction / commit).
        decision: the ``AIAnalysisResult`` Decision row to execute.
        adapter: injectable exchange adapter (tests pass a mock). When omitted, a
            **paper** Alpaca adapter is resolved from the user's connection.
        risk_snapshot: injectable risk inputs (tests pass explicit numbers). When
            omitted, gathered from the adapter + DB.
        risk_limit: equity SPEC limits (override only in tests).

    Returns an ``ExecutionResult`` — ``executed`` / ``rejected`` / ``skipped`` with
    a reason. Never raises for a business rejection.
    """
    # 1) None-safe input.
    if decision is None:
        return ExecutionResult(STATUS_REJECTED, "null_decision")

    decision_id = getattr(decision, "id", None)

    # 2) Idempotency: already linked to a Position -> never re-place.
    if getattr(decision, "position_id", None) is not None:
        logger.info("execution: decision %s already executed -> skip", decision_id)
        return ExecutionResult(
            STATUS_SKIPPED,
            "already_executed",
            decision_id=decision_id,
            position_id=decision.position_id,
        )

    # 3) Asset-class gate: equities only. Crypto/Binance refused before any adapter.
    if not _is_equity_decision(decision):
        reason = f"not_equity_refused:{getattr(decision, 'asset_class', None)}"
        logger.warning("execution: refusing non-equity decision %s (%s)", decision_id, reason)
        return ExecutionResult(STATUS_REJECTED, reason, decision_id=decision_id)

    # 4) Verdict gate: only an explicit go is actionable.
    if decision.verdict != Verdict.GO:
        return ExecutionResult(
            STATUS_REJECTED, f"verdict_not_go:{decision.verdict}", decision_id=decision_id
        )

    # 5) Action gate: this path only opens long entries (market BUY).
    if decision.action != TradeAction.BUY:
        return ExecutionResult(
            STATUS_REJECTED, f"unsupported_action:{decision.action}", decision_id=decision_id
        )

    user_id = decision.user_id
    symbol = (decision.symbol or "").upper()
    if not symbol:
        return ExecutionResult(STATUS_REJECTED, "missing_symbol", decision_id=decision_id)

    # 6) Positions need a watchlist FK — resolve BEFORE ordering (fail fast, no orphan order).
    watchlist_id = await _resolve_watchlist_id(db, user_id)
    if watchlist_id is None:
        return ExecutionResult(STATUS_REJECTED, "no_watchlist_for_user", decision_id=decision_id)

    # 7) Adapter — PAPER Alpaca only. Injected in tests; live connections refused.
    if adapter is None:
        adapter, err = await _resolve_alpaca_paper_adapter(db, user_id)
        if adapter is None:
            return ExecutionResult(STATUS_REJECTED, err or "no_adapter", decision_id=decision_id)

    # 8) Risk snapshot (equity account + tallies).
    if risk_snapshot is None:
        risk_snapshot = await gather_risk_snapshot(db, user_id, symbol, adapter)

    equity = risk_snapshot.account_equity
    if equity is None or equity <= 0:
        return ExecutionResult(STATUS_REJECTED, "no_account_equity", decision_id=decision_id)

    # 9) Daily-loss stop (-2% equity). Pre-order.
    dl = daily_loss_breached(
        day_pnl=float(risk_snapshot.day_realized_pnl),
        account_equity=float(equity),
        limit=risk_limit,
    )
    if not dl.allowed:
        return ExecutionResult(
            STATUS_REJECTED, "daily_loss_stop: " + "; ".join(dl.reasons), decision_id=decision_id
        )

    # 10) Concurrency (<=5 open) + weekly budget (<=3/week). Pre-order.
    cap_gate = can_open_new_position(
        open_positions=risk_snapshot.open_positions,
        trades_this_week=risk_snapshot.trades_this_week,
        limit=risk_limit,
    )
    if not cap_gate.allowed:
        return ExecutionResult(
            STATUS_REJECTED, "risk_gate: " + "; ".join(cap_gate.reasons), decision_id=decision_id
        )

    # 11) Size to <=5% single-name cap (whole shares).
    ref_price = risk_snapshot.reference_price
    if ref_price is None or ref_price <= 0:
        return ExecutionResult(STATUS_REJECTED, "no_reference_price", decision_id=decision_id)
    quantity = compute_share_quantity(equity, ref_price, risk_limit)
    if quantity <= 0:
        return ExecutionResult(STATUS_REJECTED, "position_size_zero", decision_id=decision_id)

    # 12) Place the PAPER market BUY.
    order = OrderRequest(
        symbol=symbol,
        side="buy",
        order_type="market",
        quantity=float(quantity),
    )
    order_result = await adapter.place_order(order)
    if order_result is None or not order_result.success:
        msg = getattr(order_result, "message", "unknown") if order_result else "no_result"
        return ExecutionResult(
            STATUS_REJECTED, f"order_failed:{msg}", decision_id=decision_id
        )

    # 13) Record the Position from the fill (fall back to reference price/size if the
    #     paper fill fields come back empty — None-safe).
    entry_price = _to_decimal(order_result.filled_price) or ref_price
    filled_qty = _to_decimal(order_result.filled_quantity) or Decimal(quantity)

    position = Position(
        watchlist_id=watchlist_id,
        symbol=symbol,
        entry_price=entry_price,
        quantity=filled_qty,
        entry_date=datetime.now(timezone.utc),
        status="open",
        position_type="LONG",
        notes=f"paper equity BUY via decision {decision_id}",
    )
    db.add(position)
    await db.flush()
    await db.refresh(position)

    # 14) Link the Position back onto the Decision (idempotency marker).
    decision.position_id = position.id
    await db.flush()

    logger.info(
        "execution: decision %s -> paper BUY %s x%s @ %s -> position %s",
        decision_id, symbol, filled_qty, entry_price, position.id,
    )
    return ExecutionResult(
        STATUS_EXECUTED,
        "paper_buy_filled",
        decision_id=decision_id,
        position_id=position.id,
        order_id=order_result.order_id,
        quantity=float(filled_qty),
        entry_price=float(entry_price),
    )
