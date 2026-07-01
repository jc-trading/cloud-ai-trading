"""Tests for FIX-risk-tasks-safe: real equity/price sourcing + 0-position safety.

Standalone (no DB / no pytest-asyncio config needed): async coroutines are driven
via asyncio.run and a minimal fake AsyncSession, so the suite is deterministic and
runs anywhere pytest is available.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.modules.market_data.service import MarketDataService
from app.modules.risk.tracker import PortfolioRiskTracker
from app.tasks.risk_tasks import _starting_capital


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value


class _FakeSession:
    """Returns queued results in order for each .execute() call."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.flushed = 0

    async def execute(self, stmt):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


def test_starting_capital_from_settings_not_hardcoded():
    """Base equity comes from SIMULATE_BALANCE, never the old 100000 placeholder."""
    cap = _starting_capital()
    assert cap == Decimal(str(get_settings().DEFAULT_SIMULATE_BALANCE))
    assert cap > Decimal("0")
    assert cap != Decimal("100000")


def test_get_latest_prices_uses_ohlcv_close_and_omits_missing():
    """Prices come from the latest OHLCV close; a symbol with no candle is dropped
    (never substituted with a placeholder like 100)."""
    session = _FakeSession([_FakeResult(Decimal("42000")), _FakeResult(None)])
    prices = asyncio.run(
        MarketDataService.get_latest_prices(session, "wl-1", ["BTCUSDT", "ETHUSDT"])
    )
    assert prices == {"BTCUSDT": Decimal("42000")}
    assert "ETHUSDT" not in prices


def test_get_latest_prices_empty_symbols_returns_empty():
    """0 open positions -> no symbols -> empty price map -> monitor task skips cleanly."""
    session = _FakeSession([])
    prices = asyncio.run(MarketDataService.get_latest_prices(session, "wl-1", []))
    assert prices == {}


def test_total_return_percent_uses_real_initial_capital():
    """total_return_percent is measured against metrics['initial_capital'] (SIMULATE_
    BALANCE-derived), not a hardcoded 100000. $500 gain on $10000 base = 5%, not 0.5%."""
    session = _FakeSession([_FakeResult(None)])  # no existing PortfolioStats
    metrics = {
        "initial_capital": Decimal("10000"),
        "current_equity": Decimal("10500"),
        "unrealized_pnl": Decimal("500"),
        "realized_pnl": Decimal("0"),
        "total_pnl": Decimal("500"),
        "win_rate": 0.0,
        "max_drawdown_percent": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
    }
    stats = asyncio.run(
        PortfolioRiskTracker.update_portfolio_stats(session, "wl-1", metrics)
    )
    assert stats.total_return_percent == Decimal("5")


def test_total_return_percent_zero_when_no_pnl():
    """Guardrail: flat portfolio -> 0% return, no division blow-up."""
    session = _FakeSession([_FakeResult(None)])
    metrics = {
        "initial_capital": Decimal("10000"),
        "current_equity": Decimal("10000"),
        "unrealized_pnl": Decimal("0"),
        "realized_pnl": Decimal("0"),
        "total_pnl": Decimal("0"),
        "win_rate": 0.0,
        "max_drawdown_percent": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
    }
    stats = asyncio.run(
        PortfolioRiskTracker.update_portfolio_stats(session, "wl-1", metrics)
    )
    assert stats.total_return_percent == Decimal("0")
