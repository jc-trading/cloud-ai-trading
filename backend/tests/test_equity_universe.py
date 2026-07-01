"""Tests for equity universe + daily candidate selection.

Pure helpers are tested directly; the DB-touching ``select_daily_candidates`` is
driven with a fake session that queues ``.scalars().all()`` results (recent
earnings rows, then a batched fundamentals load) and an injectable price lookup —
no real DB and no network. Asserts acceptance criterion (1):

  * universe = S&P 500 ∩ liquidity (price >= $10, avg_volume >= 1M)
  * daily candidates = recent (1-3 trading-day) reporters ∪ standing watchlist
  * conservative defaults: an unvetted earnings name with missing data is dropped
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.equity.universe import (
    DEFAULT_EQUITY_WATCHLIST,
    LIQUIDITY_MIN_AVG_VOLUME,
    LIQUIDITY_MIN_PRICE,
    is_recent_report,
    liquidity_check,
    select_daily_candidates,
)


# --------------------------------------------------------------------------- #
# fakes                                                                        #
# --------------------------------------------------------------------------- #
class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """Queues list-results for successive execute() calls."""

    def __init__(self, result_lists):
        self._results = list(result_lists)

    async def execute(self, stmt):
        rows = self._results.pop(0) if self._results else []
        return _FakeResult(rows)


class _Earnings:
    def __init__(self, symbol, report_date, *, eps_actual=Decimal("1"), rev_actual=None):
        self.symbol = symbol
        self.report_date = report_date
        self.eps_actual = eps_actual
        self.rev_actual = rev_actual


class _Fund:
    def __init__(self, symbol, *, is_sp500=True, avg_volume=5_000_000):
        self.symbol = symbol
        self.is_sp500 = is_sp500
        self.avg_volume = None if avg_volume is None else Decimal(str(avg_volume))


def _recent_business_day(days_back=1):
    d = date.today()
    while days_back > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days_back -= 1
    return d


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# liquidity_check (pure)                                                        #
# --------------------------------------------------------------------------- #
def test_liquidity_passes_when_both_ok():
    lc = liquidity_check(50.0, 3_000_000)
    assert lc.passed is True
    assert lc.confirmed_fail is False
    assert lc.price_verified is True


def test_liquidity_fails_low_volume():
    lc = liquidity_check(50.0, 500_000)
    assert lc.passed is False
    assert lc.confirmed_fail is True


def test_liquidity_fails_low_price():
    lc = liquidity_check(4.0, 3_000_000)
    assert lc.passed is False
    assert lc.confirmed_fail is True


def test_liquidity_boundaries_are_inclusive():
    lc = liquidity_check(LIQUIDITY_MIN_PRICE, LIQUIDITY_MIN_AVG_VOLUME)
    assert lc.passed is True
    assert lc.volume_ok is True and lc.price_ok is True


def test_liquidity_unknown_price_passes_on_volume_but_unverified():
    lc = liquidity_check(None, 3_000_000)
    assert lc.passed is True            # leans on confirmed volume
    assert lc.price_verified is False   # but price never fabricated
    assert lc.confirmed_fail is False


def test_liquidity_unknown_volume_never_passes():
    lc = liquidity_check(50.0, None)
    assert lc.passed is False
    assert lc.confirmed_fail is False   # unknown != confirmed-fail


# --------------------------------------------------------------------------- #
# is_recent_report (pure)                                                       #
# --------------------------------------------------------------------------- #
def test_is_recent_report_within_window():
    today = date(2026, 6, 30)  # Tuesday
    ok, days = is_recent_report(date(2026, 6, 29), today=today)  # Monday
    assert ok is True and days == 1


def test_is_recent_report_today_is_zero_days():
    today = date(2026, 6, 30)
    ok, days = is_recent_report(today, today=today)
    assert ok is True and days == 0


def test_is_recent_report_too_old():
    today = date(2026, 6, 30)
    ok, days = is_recent_report(date(2026, 6, 20), today=today)
    assert ok is False and days > 3


def test_is_recent_report_future_not_recent():
    today = date(2026, 6, 30)
    ok, days = is_recent_report(date(2026, 7, 3), today=today)
    assert ok is False and days is not None and days < 0


def test_is_recent_report_none_date():
    ok, days = is_recent_report(None, today=date(2026, 6, 30))
    assert ok is False and days is None


# --------------------------------------------------------------------------- #
# select_daily_candidates                                                       #
# --------------------------------------------------------------------------- #
def test_watchlist_selected_even_without_earnings_or_fundamentals():
    """A quiet day: no reporters, no fundamentals cached -> the standing
    watchlist is still the candidate pool (curated names trusted)."""
    session = _FakeSession([[], []])  # no earnings rows, no fundamentals
    out = _run(select_daily_candidates(session))
    assert set(out.symbols) == set(DEFAULT_EQUITY_WATCHLIST)
    for c in out.selected:
        assert "watchlist" in c.sources
        assert c.in_universe is True


def test_earnings_name_in_universe_is_added():
    today = _recent_business_day(0) if date.today().weekday() < 5 else _recent_business_day(0)
    report_day = _recent_business_day(1)
    earnings = [_Earnings("SNOW", report_day, eps_actual=Decimal("0.5"))]
    funds = [_Fund("SNOW", is_sp500=True, avg_volume=8_000_000)]
    # Only the watchlist + SNOW get fundamentals; watchlist has none here.
    session = _FakeSession([earnings, funds])
    out = _run(select_daily_candidates(session, today=date.today()))
    snow = next((c for c in out.selected if c.symbol == "SNOW"), None)
    assert snow is not None
    assert "earnings" in snow.sources
    assert snow.earnings_days_ago is not None and 0 <= snow.earnings_days_ago <= 3
    assert snow.in_universe is True


def test_earnings_name_not_in_sp500_is_rejected():
    report_day = _recent_business_day(1)
    earnings = [_Earnings("PENNY", report_day)]
    funds = [_Fund("PENNY", is_sp500=False, avg_volume=8_000_000)]
    session = _FakeSession([earnings, funds])
    out = _run(select_daily_candidates(session, today=date.today()))
    assert "PENNY" not in out.symbols
    rej = next(c for c in out.rejected if c.symbol == "PENNY")
    assert rej.in_universe is False


def test_earnings_name_missing_fundamentals_is_rejected_conservatively():
    """Unvetted dynamic name with NO cached fundamentals -> default NO."""
    report_day = _recent_business_day(1)
    earnings = [_Earnings("XYZ", report_day)]
    session = _FakeSession([earnings, []])  # no fundamentals for anyone
    out = _run(select_daily_candidates(session, today=date.today()))
    assert "XYZ" not in out.symbols
    rej = next(c for c in out.rejected if c.symbol == "XYZ")
    assert rej.in_universe is False


def test_earnings_name_low_volume_rejected():
    report_day = _recent_business_day(1)
    earnings = [_Earnings("THIN", report_day)]
    funds = [_Fund("THIN", is_sp500=True, avg_volume=200_000)]  # < 1M
    session = _FakeSession([earnings, funds])
    out = _run(select_daily_candidates(session, today=date.today()))
    assert "THIN" not in out.symbols


def test_watchlist_dropped_only_on_confirmed_breach():
    """A watchlist name with a KNOWN sub-1M volume is dropped; missing data isn't."""
    aapl = DEFAULT_EQUITY_WATCHLIST[0]
    funds = [_Fund(aapl, is_sp500=True, avg_volume=100_000)]  # confirmed thin
    session = _FakeSession([[], funds])
    out = _run(select_daily_candidates(session))
    assert aapl not in out.symbols
    rej = next(c for c in out.rejected if c.symbol == aapl)
    assert rej.liquidity.confirmed_fail is True


def test_price_lookup_enforces_price_floor():
    """When a price source IS provided, a sub-$10 watchlist name is dropped."""
    aapl = DEFAULT_EQUITY_WATCHLIST[0]

    def price_lookup(symbol):
        return 3.0 if symbol == aapl else 100.0

    session = _FakeSession([[], []])
    out = _run(select_daily_candidates(session, price_lookup=price_lookup))
    assert aapl not in out.symbols


def test_async_price_lookup_supported():
    aapl = DEFAULT_EQUITY_WATCHLIST[0]

    async def price_lookup(symbol):
        return 250.0

    session = _FakeSession([[], []])
    out = _run(select_daily_candidates(session, price_lookup=price_lookup))
    aapl_c = next(c for c in out.selected if c.symbol == aapl)
    assert aapl_c.price == pytest.approx(250.0)
    assert aapl_c.liquidity.price_verified is True


def test_custom_watchlist_and_serialization():
    session = _FakeSession([[], []])
    out = _run(select_daily_candidates(session, watchlist=["nvda", "msft"]))
    assert set(out.symbols) == {"NVDA", "MSFT"}
    import json
    json.dumps(out.to_dict())  # must be serializable
