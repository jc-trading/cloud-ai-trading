"""Sim-ledger service unit tests (R1-3): booking primitives with a queued fake
session — idempotency, cash accounting, lot open/pyramid/close, equity marks.
The same CostModel as the backtest prices every fill (8bps/side defaults)."""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.simledger.models import SimAccount, SimFill, SimOrder, SimPosition
from app.modules.simledger.service import (
    InsufficientCash, SimLedgerService, _dec,
)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        outer = self

        class _S:
            def all(self):
                return outer._value or []
        return _S()


class _FakeSession:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []

    async def execute(self, stmt):
        value = self._results.pop(0) if self._results else None
        return _FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


def _account(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default",
                      is_system=True, starting_capital=_dec(2000),
                      cash=_dec(cash))


def _open_pos(symbol="AAPL", shares=10.0, avg=100.0, stop=95.0):
    return SimPosition(id=uuid4(), account_id=uuid4(), symbol=symbol, status="open",
                       shares=_dec(shares), avg_cost=_dec(avg), stop=_dec(stop),
                       r_unit=_dec(avg - stop), high_water=_dec(avg),
                       entry_date=date(2026, 7, 1), adds_done=0,
                       reversal_count=0, bars_held=0)


def test_open_new_lot_books_order_fill_and_cash():
    acct = _account(cash=2000.0)
    # execute #1: idempotency lookup -> None; #2: open-position lookup -> None
    db = _FakeSession([None, None])
    order = asyncio.run(SimLedgerService.open_or_add(
        db, acct, symbol="AAPL", qty=5.0, raw_price=100.0, stop=95.0,
        reason="entry", idempotency_key="k1", trade_date=date(2026, 7, 30)))
    assert order is not None and order.side == "buy" and order.status == "filled"
    pos = next(o for o in db.added if isinstance(o, SimPosition))
    fill = next(o for o in db.added if isinstance(o, SimFill))
    assert float(fill.price) == pytest.approx(100.08)       # entry pays UP 8bps
    assert float(pos.avg_cost) == pytest.approx(100.08)
    assert float(acct.cash) == pytest.approx(2000 - 5 * 100.08)


def test_idempotent_rerun_books_nothing():
    acct = _account()
    db = _FakeSession([uuid4()])          # idempotency lookup HITS
    order = asyncio.run(SimLedgerService.open_or_add(
        db, acct, symbol="AAPL", qty=5.0, raw_price=100.0, stop=95.0,
        reason="entry", idempotency_key="k1", trade_date=date(2026, 7, 30)))
    assert order is None
    assert db.added == []
    assert float(acct.cash) == pytest.approx(2000.0)


def test_insufficient_cash_refuses():
    acct = _account(cash=100.0)
    db = _FakeSession([None, None])
    with pytest.raises(InsufficientCash):
        asyncio.run(SimLedgerService.open_or_add(
            db, acct, symbol="AAPL", qty=5.0, raw_price=100.0, stop=95.0,
            reason="entry", idempotency_key="k2", trade_date=date(2026, 7, 30)))


def test_pyramid_blends_cost_never_lowers_stop():
    acct = _account(cash=2000.0)
    pos = _open_pos(shares=10, avg=100.0, stop=98.0)
    db = _FakeSession([None, pos])        # idem miss, open lot found
    order = asyncio.run(SimLedgerService.open_or_add(
        db, acct, symbol="AAPL", qty=10.0, raw_price=110.0, stop=90.0,  # lower stop offered
        reason="pyramid", idempotency_key="k3", trade_date=date(2026, 7, 30)))
    assert order is not None
    assert float(pos.shares) == pytest.approx(20.0)
    assert float(pos.avg_cost) == pytest.approx((10 * 100.0 + 10 * 110.088) / 20)
    assert float(pos.stop) == pytest.approx(98.0)           # never lowered
    assert pos.adds_done == 1


def test_close_position_credits_cash_and_closes_lot():
    acct = _account(cash=0.0)
    pos = _open_pos(shares=10, avg=100.0)
    db = _FakeSession([None])             # idem miss
    order = asyncio.run(SimLedgerService.close_position(
        db, acct, pos, raw_price=120.0, reason="trailing", idempotency_key="k4"))
    assert order is not None and order.side == "sell"
    assert pos.status == "closed" and pos.close_reason == "trailing"
    assert float(acct.cash) == pytest.approx(10 * 120.0 * (1 - 0.0008))


def test_close_is_idempotent_too():
    acct = _account(cash=0.0)
    pos = _open_pos()
    db = _FakeSession([uuid4()])          # idem HIT
    order = asyncio.run(SimLedgerService.close_position(
        db, acct, pos, raw_price=120.0, reason="trailing", idempotency_key="k4"))
    assert order is None
    assert pos.status == "open"
    assert float(acct.cash) == 0.0


def test_equity_marks_at_quotes_with_cost_fallback():
    acct = _account(cash=500.0)
    p1 = _open_pos("AAPL", shares=10, avg=100.0)
    p2 = _open_pos("MSFT", shares=2, avg=300.0)
    eq = SimLedgerService.equity(acct, [p1, p2], {"AAPL": 110.0})  # MSFT quote missing
    assert eq == pytest.approx(500 + 10 * 110.0 + 2 * 300.0)
