"""Tests for the auto-execution Celery task (EXEC-auto-task).

Standalone — no DB, no network, no Celery broker. We drive the task CORE
(``_auto_execute_equity``) directly with a fresh event loop, a no-op fake
session, an injected mock adapter, and the module-level helpers monkeypatched so
the test exercises the SCAN + BUDGET + loop logic without touching Postgres.

Asserts the acceptance criteria + money-path guardrails:
  * only equity + go + not-yet-executed Decisions are executed, in the weekly (<=3)
    and concurrency (<=5) budget — the loop STOPS once the budget is spent;
  * a spent budget skips the run WITHOUT ever resolving an adapter;
  * an Alpaca / execution error for one name is logged and skipped — the run
    keeps going and never raises;
  * a non-trading day is a clean skip;
  * a missing paper adapter is a clean skip (no orders).
"""

import asyncio
from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.modules.analysis.models import AssetClass, TradeAction, Verdict
from app.modules.equity.risk_config import EquityRiskLimit
from app.modules.execution.service import STATUS_EXECUTED, STATUS_REJECTED, ExecutionResult
from app.tasks import execution_tasks

# A real weekday that is NOT a US market holiday (Wed 2026-07-01).
TRADING_DAY = date(2026, 7, 1)


class _FakeSession:
    """No-op session: commit/rollback just count; execute is never reached because
    every DB helper the core calls is monkeypatched."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _MockAdapter:
    def __init__(self):
        self.orders = []

    async def place_order(self, order):  # pragma: no cover - execute_decision is patched
        self.orders.append(order)
        return SimpleNamespace(success=True, order_id="x")


def _decision(**overrides):
    base = dict(
        id=uuid4(),
        user_id=uuid4(),
        symbol="AAPL",
        exchange_type="alpaca",
        asset_class=AssetClass.EQUITY,
        verdict=Verdict.GO,
        action=TradeAction.BUY,
        position_id=None,
        confidence=80,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch(monkeypatch, *, user_id=None, open_positions=1, weekly_buys=0,
           candidates=None, execute_impl=None):
    user_id = user_id or uuid4()

    async def _user(_session):
        return user_id

    async def _open(_session, _uid):
        return open_positions

    async def _week(_session, _uid):
        return weekly_buys

    async def _pending(_session, _uid, **_kw):
        return list(candidates or [])

    monkeypatch.setattr(execution_tasks, "_resolve_target_user", _user)
    monkeypatch.setattr(execution_tasks, "_count_open_equity_positions", _open)
    monkeypatch.setattr(execution_tasks, "_count_weekly_equity_buys", _week)
    monkeypatch.setattr(execution_tasks, "_pending_go_equity_decisions", _pending)
    if execute_impl is not None:
        monkeypatch.setattr(execution_tasks, "execute_decision", execute_impl)
    return user_id


def _run(**kwargs):
    session = _FakeSession()
    result = asyncio.run(
        execution_tasks._auto_execute_equity(
            session, today=TRADING_DAY, adapter=_MockAdapter(), **kwargs
        )
    )
    return result, session


# --------------------------------------------------------------------------- #


def test_executes_go_equity_within_budget(monkeypatch):
    calls = []

    async def _exec(_session, decision, **_kw):
        calls.append(decision.symbol)
        return ExecutionResult(STATUS_EXECUTED, "paper_buy_filled",
                               decision_id=decision.id, position_id=uuid4())

    _patch(
        monkeypatch,
        open_positions=1,
        weekly_buys=0,
        candidates=[_decision(symbol="AAPL"), _decision(symbol="MSFT")],
        execute_impl=_exec,
    )
    result, session = _run()
    assert result["executed"] == 2
    assert result["rejected"] == 0
    assert session.commits == 2  # one commit per executed Position
    assert calls == ["AAPL", "MSFT"]


def test_weekly_budget_caps_the_loop(monkeypatch):
    """Weekly BUYs already at 2 -> only ONE more entry allowed; loop stops after it."""
    executed = []

    async def _exec(_session, decision, **_kw):
        executed.append(decision.symbol)
        return ExecutionResult(STATUS_EXECUTED, "ok", decision_id=decision.id,
                               position_id=uuid4())

    _patch(
        monkeypatch,
        open_positions=1,
        weekly_buys=2,  # remaining weekly budget = 3 - 2 = 1
        candidates=[_decision(symbol="A"), _decision(symbol="B"), _decision(symbol="C")],
        execute_impl=_exec,
    )
    result, _ = _run()
    assert result["executed"] == 1
    assert result["budget_left"] == 0
    assert executed == ["A"]  # stopped after the single allowed entry


def test_budget_spent_skips_without_adapter(monkeypatch):
    """Concurrency cap reached -> skip BEFORE resolving any adapter (guardrail)."""
    resolved = {"called": False}

    async def _boom(_session, _uid):  # must NOT be called
        resolved["called"] = True
        return _MockAdapter(), None

    _patch(monkeypatch, open_positions=5, weekly_buys=0, candidates=[_decision()])
    monkeypatch.setattr(execution_tasks, "_resolve_alpaca_paper_adapter", _boom)

    session = _FakeSession()
    result = asyncio.run(
        execution_tasks._auto_execute_equity(session, today=TRADING_DAY)  # adapter=None
    )
    assert result["skipped"] == "budget_spent"
    assert resolved["called"] is False


def test_execution_error_is_skipped_gracefully(monkeypatch):
    """An Alpaca/DB error on one name is logged + skipped; the run keeps going."""
    seen = []

    async def _exec(_session, decision, **_kw):
        seen.append(decision.symbol)
        if decision.symbol == "BAD":
            raise RuntimeError("alpaca down")
        return ExecutionResult(STATUS_EXECUTED, "ok", decision_id=decision.id,
                               position_id=uuid4())

    _patch(
        monkeypatch,
        candidates=[_decision(symbol="BAD"), _decision(symbol="GOOD")],
        execute_impl=_exec,
    )
    result, session = _run()
    assert result["errors"] == 1
    assert result["executed"] == 1
    assert seen == ["BAD", "GOOD"]  # one bad name did not abort the run
    assert session.rollbacks >= 1


def test_business_rejection_counts_and_rolls_back(monkeypatch):
    async def _exec(_session, decision, **_kw):
        return ExecutionResult(STATUS_REJECTED, "risk_gate: weekly cap",
                               decision_id=decision.id)

    _patch(monkeypatch, candidates=[_decision()], execute_impl=_exec)
    result, session = _run()
    assert result["executed"] == 0
    assert result["rejected"] == 1
    assert session.commits == 0


def test_non_trading_day_is_a_clean_skip(monkeypatch):
    _patch(monkeypatch, candidates=[_decision()])
    session = _FakeSession()
    # 2026-07-04 (Independence Day, observed Fri 07-03) — use a Saturday to be safe.
    result = asyncio.run(
        execution_tasks._auto_execute_equity(session, today=date(2026, 7, 4))
    )
    assert result["skipped"] == "non_trading_day"


def test_missing_adapter_is_a_clean_skip(monkeypatch):
    _patch(monkeypatch, candidates=[_decision()])

    async def _none(_session, _uid):
        return None, "no_active_alpaca_connection"

    monkeypatch.setattr(execution_tasks, "_resolve_alpaca_paper_adapter", _none)
    session = _FakeSession()
    result = asyncio.run(
        execution_tasks._auto_execute_equity(session, today=TRADING_DAY)  # adapter=None
    )
    assert result["executed"] == 0
    assert result["skipped"] == "no_active_alpaca_connection"
