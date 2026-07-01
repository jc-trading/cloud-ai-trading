"""Tests for TASKS-fundamentals-refresh: the Celery cache-refresh tasks.

Standalone — no DB and no network. Pure mappers are checked directly; the async
task cores are driven via asyncio.run against a fake client + fake session, so
the suite is deterministic and never consumes the Finnhub free-tier quota.
"""

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tasks import fundamentals_tasks as ft
from app.tasks.fundamentals_tasks import (
    _map_calendar_row,
    _map_profile_row,
    _refresh_company_profiles,
    _refresh_earnings_calendar,
    _refresh_financials_on_earnings,
)


# ---- fakes ----------------------------------------------------------------


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeSession:
    """Queues scalar results; records every executed statement + commit count."""

    def __init__(self, scalar_results=None):
        self._scalar_results = list(scalar_results or [])
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        if self._scalar_results:
            return _FakeResult(self._scalar_results.pop(0))
        return _FakeResult([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FakeClient:
    def __init__(self, enabled=True, **data):
        self._enabled = enabled
        self._data = data
        self.calls = []

    @property
    def enabled(self):
        return self._enabled

    def index_constituents(self, symbol="^GSPC"):
        self.calls.append(("index", symbol))
        return self._data.get("index", [])

    def company_profile(self, symbol):
        self.calls.append(("profile", symbol))
        return self._data.get("profile")

    def basic_financials(self, symbol, metric="all"):
        self.calls.append(("metric", symbol))
        return self._data.get("metric")

    def earnings_actuals(self, symbol):
        self.calls.append(("actuals", symbol))
        return self._data.get("actuals", [])

    def earnings_calendar(self, from_date, to_date, symbol=None):
        self.calls.append(("calendar", symbol))
        return self._data.get("calendar", [])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Never actually sleep during tests."""
    monkeypatch.setattr(ft.time, "sleep", lambda *_a, **_k: None)


# ---- pure mapper: profile -------------------------------------------------


def test_map_profile_row_expands_millions_and_maps_fields():
    profile = {
        "name": "Apple Inc",
        "finnhubIndustry": "Technology",
        "shareOutstanding": 15000.0,          # millions of shares
        "marketCapitalization": 3000000.0,    # millions USD
    }
    metric = {"metric": {"10DayAverageTradingVolume": 55.5}}  # millions of shares
    actuals = [{"period": "2026-03-31", "actual": 1.5, "estimate": 1.4}]

    values = _map_profile_row("AAPL", profile, metric, actuals, is_sp500=True)

    assert values["symbol"] == "AAPL"
    assert values["name"] == "Apple Inc"
    assert values["industry"] == "Technology"
    assert values["shares_outstanding"] == Decimal("15000000000")
    assert values["market_cap"] == Decimal("3000000000000")
    assert values["avg_volume"] == Decimal("55500000")
    assert values["is_sp500"] is True
    assert values["historical_financials"] == actuals


def test_map_profile_row_omits_missing_fields():
    """A near-empty response must not manufacture NULL columns to overwrite cache."""
    values = _map_profile_row("XYZ", {}, None, [], is_sp500=None)
    assert values == {"symbol": "XYZ"}
    # None-valued / empty fields are simply absent
    for absent in ("name", "market_cap", "avg_volume", "is_sp500", "historical_financials"):
        assert absent not in values


def test_map_profile_row_falls_back_to_3month_volume():
    metric = {"metric": {"3MonthAverageTradingVolume": 10}}
    values = _map_profile_row("AAA", {}, metric, [])
    assert values["avg_volume"] == Decimal("10000000")


# ---- pure mapper: calendar ------------------------------------------------


def test_map_calendar_row_scheduled_when_no_actual():
    row = {
        "symbol": "AAPL",
        "date": "2026-07-31",
        "hour": "amc",
        "epsEstimate": 1.42,
        "revenueEstimate": 90000000000,
    }
    values = _map_calendar_row(row)
    assert values["symbol"] == "AAPL"
    assert values["report_date"] == date(2026, 7, 31)
    assert values["time"] == "amc"
    assert values["eps_estimate"] == Decimal("1.42")
    assert values["rev_estimate"] == Decimal("90000000000")
    assert values["status"] == "scheduled"
    assert "eps_actual" not in values


def test_map_calendar_row_reported_when_actual_present():
    row = {"symbol": "AAPL", "date": "2026-05-01", "epsActual": 1.53}
    values = _map_calendar_row(row)
    assert values["eps_actual"] == Decimal("1.53")
    assert values["status"] == "reported"


def test_map_calendar_row_invalid_returns_none():
    assert _map_calendar_row(None) is None
    assert _map_calendar_row({"date": "2026-01-01"}) is None          # no symbol
    assert _map_calendar_row({"symbol": "AAPL"}) is None              # no date
    assert _map_calendar_row({"symbol": "AAPL", "date": "bad"}) is None


# ---- graceful skip: no key ------------------------------------------------


def test_all_tasks_skip_cleanly_without_key():
    """No key -> return 0, no DB touch, no crash (guardrail)."""
    for core in (
        _refresh_company_profiles,
        _refresh_earnings_calendar,
        _refresh_financials_on_earnings,
    ):
        session = _FakeSession()
        client = _FakeClient(enabled=False)
        result = asyncio.run(core(session, client))
        assert result == 0
        assert session.executed == []
        assert session.commits == 0
        assert client.calls == []


# ---- happy paths: cores write via upsert ----------------------------------


def test_refresh_company_profiles_upserts_and_commits():
    # First scalar result = the watchlist stock symbols query.
    session = _FakeSession(scalar_results=[["AAPL"]])
    client = _FakeClient(
        enabled=True,
        index=["AAPL", "MSFT"],
        profile={"name": "Apple Inc", "finnhubIndustry": "Tech", "shareOutstanding": 100},
        metric={"metric": {"10DayAverageTradingVolume": 5}},
        actuals=[{"period": "2026-03-31", "actual": 1.5}],
    )
    count = asyncio.run(_refresh_company_profiles(session, client))
    assert count == 1
    assert session.commits == 1
    # symbol query + one upsert = 2 executes
    assert len(session.executed) == 2


def test_refresh_earnings_calendar_upserts_each_row():
    session = _FakeSession(scalar_results=[["AAPL"]])
    client = _FakeClient(
        enabled=True,
        calendar=[
            {"symbol": "AAPL", "date": "2026-07-31", "hour": "amc", "epsEstimate": 1.4},
            {"symbol": "AAPL", "date": "2026-10-31", "epsEstimate": 1.6},
            {"garbage": True},  # skipped
        ],
    )
    count = asyncio.run(_refresh_earnings_calendar(session, client))
    assert count == 2
    assert session.commits == 1


def test_refresh_financials_skips_symbol_with_no_recent_row():
    """No recent calendar row for the symbol -> zero API calls for it (scoping)."""
    # symbol query -> ["AAPL"]; recent-rows query -> [] (nothing reported)
    session = _FakeSession(scalar_results=[["AAPL"], []])
    client = _FakeClient(enabled=True, calendar=[], actuals=[])
    count = asyncio.run(_refresh_financials_on_earnings(session, client))
    assert count == 0
    # only the two SELECTs ran, no calendar/actuals API calls
    assert client.calls == []


def test_refresh_financials_fills_actuals_when_recent_row_exists():
    # symbol query -> ["AAPL"]; recent-rows query -> [some id]
    session = _FakeSession(scalar_results=[["AAPL"], ["row-id"]])
    client = _FakeClient(
        enabled=True,
        calendar=[{"symbol": "AAPL", "date": "2026-06-29", "epsActual": 1.55}],
        actuals=[{"period": "2026-06-29", "actual": 1.55}],
    )
    count = asyncio.run(_refresh_financials_on_earnings(session, client))
    assert count == 1
    assert ("calendar", "AAPL") in client.calls
    assert session.commits == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
