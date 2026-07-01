"""Tests for the execution service (go-Decision -> risk -> Alpaca paper -> Position).

Standalone — no real DB and no network. The adapter is a mock that records
``place_order`` calls (so we can assert an order was / was NOT sent), risk inputs
are injected as an ``EquityRiskSnapshot``, and the session is a tiny queued fake
that hands back the default-watchlist id and assigns a Position id on refresh.

Asserts the acceptance criteria + the money-path guardrails:
  * only equity + verdict=go is executed; crypto / non-go are refused (no order);
  * the risk gate runs BEFORE the order — weekly cap / concurrency / daily loss
    each reject and record a reason, with no order sent;
  * a successful run places a PAPER market BUY, creates a Position, and links
    position_id back onto the Decision;
  * idempotency — a Decision already carrying position_id is skipped (no order);
  * the Alpaca adapter is PAPER-forced and a LIVE connection is refused;
  * position sizing respects the 5% single-name cap.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Register the full ORM model graph so SQLAlchemy can configure mappers when we
# instantiate a Position (its relationships reference Watchlist / PositionMetric).
import app.models_registry  # noqa: F401,E402

from app.modules.analysis.models import AssetClass, TradeAction, Verdict
from app.modules.exchange.adapters.alpaca import AlpacaAdapter
from app.modules.exchange.adapters.base import OrderResult
from app.modules.exchange.models import TradingMode
from app.modules.execution import service
from app.modules.execution.service import (
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_SKIPPED,
    EquityRiskSnapshot,
    compute_share_quantity,
    execute_decision,
)


# ---- fakes ----------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value


class _FakeSession:
    """Queues scalar results for successive execute() calls; assigns Position ids."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []
        self.flushes = 0

    async def execute(self, stmt):
        value = self._results.pop(0) if self._results else None
        return _FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()


class _MockAdapter:
    """Records place_order calls; returns a canned successful fill."""

    def __init__(self, result=None, balance=None, ticker=None):
        self._result = result or OrderResult(
            success=True, order_id="ord-1", filled_price=150.0, filled_quantity=33.0,
            fee=0.0, message="ok",
        )
        self._balance = balance
        self._ticker = ticker
        self.orders = []

    async def place_order(self, order):
        self.orders.append(order)
        return self._result

    async def get_balance(self):
        return self._balance or {"equity": 100000.0}

    async def get_ticker(self, symbol):
        return self._ticker or {"last": 150.0}


def _decision(**overrides):
    base = dict(
        id=uuid4(),
        user_id=uuid4(),
        symbol="AAPL",
        exchange_type="alpaca",
        asset_class=AssetClass.EQUITY,
        verdict=Verdict.GO,
        verdict_reason="Strong catalyst; earnings beat + guidance raise.",
        action=TradeAction.BUY,
        position_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(**overrides):
    base = dict(
        account_equity=Decimal("100000"),
        reference_price=Decimal("150"),
        open_positions=1,
        trades_this_week=0,
        day_realized_pnl=Decimal("0"),
    )
    base.update(overrides)
    return EquityRiskSnapshot(**base)


def _run(decision, adapter, snapshot, session=None):
    session = session or _FakeSession([uuid4()])  # watchlist id
    return asyncio.run(
        execute_decision(session, decision, adapter=adapter, risk_snapshot=snapshot)
    ), session


# ---- pure sizing ----------------------------------------------------------


def test_compute_share_quantity_respects_5pct_cap():
    # 5% of 100k = 5000; 5000 // 150 = 33 whole shares (33*150=4950 <= cap).
    assert compute_share_quantity(100000, 150) == 33
    assert Decimal("33") * Decimal("150") <= Decimal("100000") * Decimal("0.05")


def test_compute_share_quantity_none_safe_and_zero():
    assert compute_share_quantity(None, 150) == 0
    assert compute_share_quantity(100000, None) == 0
    assert compute_share_quantity(100000, 0) == 0
    assert compute_share_quantity(1000, 5000) == 0  # price > cap -> 0 shares


# ---- happy path -----------------------------------------------------------


def test_equity_go_executes_paper_buy_and_links_position():
    decision = _decision()
    adapter = _MockAdapter()
    result, session = _run(decision, adapter, _snapshot())

    assert result.status == STATUS_EXECUTED
    assert result.executed is True
    # An order WAS placed — market BUY, sized to the 5% cap (33 shares).
    assert len(adapter.orders) == 1
    order = adapter.orders[0]
    assert order.side == "buy"
    assert order.order_type == "market"
    assert order.quantity == 33.0
    # A Position was created and linked back onto the Decision (idempotency marker).
    assert len(session.added) == 1
    position = session.added[0]
    assert position.status == "open"
    assert position.position_type == "LONG"
    assert decision.position_id == position.id
    assert result.position_id == position.id
    assert result.order_id == "ord-1"


# ---- asset-class / verdict / action gates (refusals) ----------------------


def test_crypto_decision_is_refused_no_order():
    decision = _decision(asset_class=AssetClass.CRYPTO, exchange_type="binance", symbol="BTCUSDT")
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot())

    assert result.status == STATUS_REJECTED
    assert "not_equity" in result.reason
    assert adapter.orders == []  # NEVER touched the adapter for crypto
    assert decision.position_id is None


def test_equity_but_binance_tag_is_refused():
    # Defense in depth: even asset_class=equity is refused if the venue tag is crypto.
    decision = _decision(exchange_type="binance")
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot())
    assert result.status == STATUS_REJECTED
    assert adapter.orders == []


def test_non_go_verdict_is_refused_no_order():
    for v in (Verdict.WATCH, Verdict.NO_GO):
        decision = _decision(verdict=v)
        adapter = _MockAdapter()
        result, _ = _run(decision, adapter, _snapshot())
        assert result.status == STATUS_REJECTED
        assert "verdict_not_go" in result.reason
        assert adapter.orders == []


def test_non_buy_action_is_refused():
    decision = _decision(action=TradeAction.SELL)
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot())
    assert result.status == STATUS_REJECTED
    assert "unsupported_action" in result.reason
    assert adapter.orders == []


def test_null_decision_rejected():
    session = _FakeSession()
    result = asyncio.run(execute_decision(session, None, adapter=_MockAdapter(), risk_snapshot=_snapshot()))
    assert result.status == STATUS_REJECTED
    assert result.reason == "null_decision"


# ---- idempotency ----------------------------------------------------------


def test_already_executed_decision_is_skipped_no_order():
    existing_pos = uuid4()
    decision = _decision(position_id=existing_pos)
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot())

    assert result.status == STATUS_SKIPPED
    assert result.reason == "already_executed"
    assert result.position_id == existing_pos
    assert adapter.orders == []  # no double-place


# ---- risk gate (pre-order rejections) -------------------------------------


def test_weekly_cap_blocks_before_order():
    decision = _decision()
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot(trades_this_week=3))
    assert result.status == STATUS_REJECTED
    assert "risk_gate" in result.reason
    assert adapter.orders == []  # gate is PRE-order


def test_concurrency_cap_blocks_before_order():
    decision = _decision()
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot(open_positions=5))
    assert result.status == STATUS_REJECTED
    assert "risk_gate" in result.reason
    assert adapter.orders == []


def test_daily_loss_stop_blocks_before_order():
    decision = _decision()
    adapter = _MockAdapter()
    # -2% of 100k = -2000; a -2500 realized day trips the stop.
    result, _ = _run(decision, adapter, _snapshot(day_realized_pnl=Decimal("-2500")))
    assert result.status == STATUS_REJECTED
    assert "daily_loss_stop" in result.reason
    assert adapter.orders == []


def test_no_account_equity_rejected():
    decision = _decision()
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot(account_equity=None))
    assert result.status == STATUS_REJECTED
    assert result.reason == "no_account_equity"
    assert adapter.orders == []


def test_zero_size_rejected_no_order():
    decision = _decision()
    adapter = _MockAdapter()
    # price above the whole 5% cap -> 0 shares -> refuse, no order.
    result, _ = _run(decision, adapter, _snapshot(reference_price=Decimal("999999")))
    assert result.status == STATUS_REJECTED
    assert result.reason == "position_size_zero"
    assert adapter.orders == []


def test_no_watchlist_rejected_before_order():
    decision = _decision()
    adapter = _MockAdapter()
    session = _FakeSession([None])  # watchlist lookup -> None
    result = asyncio.run(
        execute_decision(session, decision, adapter=adapter, risk_snapshot=_snapshot())
    )
    assert result.status == STATUS_REJECTED
    assert result.reason == "no_watchlist_for_user"
    assert adapter.orders == []


# ---- order failure --------------------------------------------------------


def test_failed_order_creates_no_position():
    decision = _decision()
    adapter = _MockAdapter(result=OrderResult(success=False, message="rejected by broker"))
    result, session = _run(decision, adapter, _snapshot())
    assert result.status == STATUS_REJECTED
    assert "order_failed" in result.reason
    assert session.added == []          # no Position on a failed fill
    assert decision.position_id is None


# ---- adapter resolution: PAPER forced, LIVE refused -----------------------


def test_resolve_adapter_refuses_live_connection():
    conn = SimpleNamespace(
        trading_mode=TradingMode.LIVE,
        api_key_encrypted="k",
        api_secret_encrypted="s",
    )
    session = _FakeSession([conn])
    adapter, err = asyncio.run(service._resolve_alpaca_paper_adapter(session, uuid4()))
    assert adapter is None
    assert err == "live_mode_refused"  # never build a live adapter


def test_resolve_adapter_missing_connection():
    session = _FakeSession([None])
    adapter, err = asyncio.run(service._resolve_alpaca_paper_adapter(session, uuid4()))
    assert adapter is None
    assert err == "no_active_alpaca_connection"


# ---- post-fill side effects: PAPER Telegram + portfolio stats -------------


class _RecordingNotifier:
    """Fake TelegramNotifier: records send_paper_order_fill kwargs, no network."""

    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def send_paper_order_fill(self, **kwargs):
        _RecordingNotifier.calls.append(kwargs)
        return True


def _patch_notifier(monkeypatch):
    _RecordingNotifier.calls = []
    monkeypatch.setattr(service, "TelegramNotifier", _RecordingNotifier)
    return _RecordingNotifier


def _silence_stats(monkeypatch):
    """Stub the (best-effort) portfolio refresh so it doesn't touch the fake DB."""
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_refresh_portfolio_stats", _noop)


def test_executed_paper_buy_sends_paper_telegram(monkeypatch):
    notifier = _patch_notifier(monkeypatch)
    _silence_stats(monkeypatch)

    decision = _decision(verdict_reason="Breakout above 200D; volume surge.")
    adapter = _MockAdapter()
    result, _ = _run(decision, adapter, _snapshot())

    assert result.status == STATUS_EXECUTED
    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["symbol"] == "AAPL"          # the traded symbol
    assert call["quantity"] is not None      # size passed through
    assert call["entry_price"] is not None   # entry passed through
    assert call["reason"] == "Breakout above 200D; volume surge."  # Decision reasoning


def test_rejected_decision_sends_no_telegram(monkeypatch):
    notifier = _patch_notifier(monkeypatch)
    _silence_stats(monkeypatch)

    # A crypto decision is refused before any order — nothing to notify.
    decision = _decision(asset_class=AssetClass.CRYPTO, exchange_type="binance", symbol="BTCUSDT")
    result, _ = _run(decision, _MockAdapter(), _snapshot())

    assert result.status == STATUS_REJECTED
    assert notifier.calls == []


def test_telegram_failure_does_not_break_execution(monkeypatch):
    _silence_stats(monkeypatch)

    class _BoomNotifier:
        def __init__(self, *a, **k):
            pass

        async def send_paper_order_fill(self, **kwargs):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(service, "TelegramNotifier", _BoomNotifier)

    decision = _decision()
    result, session = _run(decision, _MockAdapter(), _snapshot())

    # Notify blew up, but the paper order still stands: executed + position linked.
    assert result.status == STATUS_EXECUTED
    assert decision.position_id == session.added[0].id


def test_executed_refreshes_portfolio_stats(monkeypatch):
    _patch_notifier(monkeypatch)
    refreshed = []

    async def _record(db, watchlist_id, symbol, entry_price):
        refreshed.append((watchlist_id, symbol, entry_price))

    monkeypatch.setattr(service, "_refresh_portfolio_stats", _record)

    decision = _decision()
    result, _ = _run(decision, _MockAdapter(), _snapshot())

    assert result.status == STATUS_EXECUTED
    assert len(refreshed) == 1
    _, symbol, entry_price = refreshed[0]
    assert symbol == "AAPL"
    assert entry_price is not None


def test_rejected_decision_does_not_refresh_stats(monkeypatch):
    _patch_notifier(monkeypatch)
    refreshed = []

    async def _record(*args, **kwargs):
        refreshed.append(args)

    monkeypatch.setattr(service, "_refresh_portfolio_stats", _record)

    decision = _decision(verdict=Verdict.NO_GO)
    result, _ = _run(decision, _MockAdapter(), _snapshot())

    assert result.status == STATUS_REJECTED
    assert refreshed == []


def test_resolve_adapter_builds_paper_only(monkeypatch):
    monkeypatch.setattr(service, "decrypt_api_key", lambda s: s)
    conn = SimpleNamespace(
        trading_mode=TradingMode.SIMULATE,
        api_key_encrypted="key",
        api_secret_encrypted="secret",
    )
    session = _FakeSession([conn])
    adapter, err = asyncio.run(service._resolve_alpaca_paper_adapter(session, uuid4()))
    assert err is None
    assert isinstance(adapter, AlpacaAdapter)
    assert adapter.paper is True                       # PAPER forced
    assert adapter.trading_url == "https://paper-api.alpaca.markets"
