"""Tests for EQUITY-schedule: the equity Celery Beat task cores.

Standalone — no real DB, no network, no Claude. The trading-calendar helpers are
pure and checked directly; the three async task cores are driven via asyncio.run
against a fake session, with ``select_daily_candidates`` / ``research_equity`` /
``_resolve_target_user`` monkeypatched so the suite never touches Postgres,
Finnhub, or Anthropic.

Asserts the acceptance criteria:
  * pre_market researches the day's candidates into equity Decisions (one commit
    per candidate) and honours the MAX_CANDIDATES cap;
  * every task skips cleanly on a non-trading day (weekend / holiday) and when no
    target user exists — never calling the agent;
  * market_open stamps an intent marker on GO Decisions ONLY, is idempotent, and
    places no order (executed=False);
  * eod summarizes verdict counts / go names / ai invocations without writing.
"""

import asyncio
from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.analysis.models import AssetClass, Verdict
from app.tasks import equity_tasks as et
from app.tasks.equity_tasks import (
    good_friday,
    is_us_market_holiday,
    is_us_trading_day,
    us_market_holidays,
    _eod,
    _market_open,
    _pre_market,
)


# --------------------------------------------------------------------------- #
# fakes                                                                         #
# --------------------------------------------------------------------------- #
class _FakeScalars:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeSession:
    """Queues per-execute scalar result lists; records commits/rollbacks."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        values = self._results.pop(0) if self._results else []
        return _FakeResult(values)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FakeDecision:
    def __init__(self, symbol, verdict, confidence=70, ai_invoked=True,
                 snapshot=None):
        self.symbol = symbol
        self.verdict = verdict
        self.confidence = confidence
        self.ai_invoked = ai_invoked
        self.indicators_snapshot = snapshot if snapshot is not None else {}


class _FakeCandidates:
    def __init__(self, symbols):
        self.symbols = list(symbols)


TRADING_DAY = date(2026, 6, 30)      # Tuesday, not a holiday
WEEKEND = date(2026, 6, 28)          # Sunday
HOLIDAY = date(2026, 7, 3)           # Independence Day observed (Sat Jul 4 -> Fri)


@pytest.fixture(autouse=True)
def _stub_target_user(monkeypatch):
    """Every core resolves the same fake account unless a test overrides it."""
    uid = uuid4()

    async def _fake_user(session):
        return uid

    monkeypatch.setattr(et, "_resolve_target_user", _fake_user)
    return uid


# --------------------------------------------------------------------------- #
# pure calendar helpers                                                         #
# --------------------------------------------------------------------------- #
def test_good_friday_2026():
    assert good_friday(2026) == date(2026, 4, 3)


def test_holiday_set_covers_the_majors():
    hols = us_market_holidays(2026)
    assert date(2026, 1, 1) in hols     # New Year's Day (Thu)
    assert date(2026, 1, 19) in hols    # MLK (3rd Mon Jan)
    assert date(2026, 5, 25) in hols    # Memorial (last Mon May)
    assert date(2026, 6, 19) in hols    # Juneteenth
    assert date(2026, 11, 26) in hols   # Thanksgiving (4th Thu Nov)
    assert date(2026, 12, 25) in hols   # Christmas (Fri)


def test_independence_day_observed_on_friday():
    # Jul 4 2026 is a Saturday -> observed Friday Jul 3.
    assert is_us_market_holiday(date(2026, 7, 3)) is True
    assert is_us_trading_day(date(2026, 7, 3)) is False


def test_trading_day_and_weekend():
    assert is_us_trading_day(TRADING_DAY) is True
    assert is_us_trading_day(WEEKEND) is False       # Sunday
    assert is_us_trading_day(date(2026, 12, 25)) is False  # holiday


# --------------------------------------------------------------------------- #
# pre_market                                                                    #
# --------------------------------------------------------------------------- #
def test_pre_market_researches_each_candidate(monkeypatch):
    calls = []

    async def _fake_candidates(session, *, today=None):
        return _FakeCandidates(["AAPL", "MSFT"])

    async def _fake_research(session, user_id, symbol, *, analysis_type=None):
        calls.append(symbol)
        return _FakeDecision(symbol, Verdict.GO)

    monkeypatch.setattr(et, "select_daily_candidates", _fake_candidates)
    monkeypatch.setattr(et, "research_equity", _fake_research)

    session = _FakeSession()
    out = asyncio.run(_pre_market(session, today=TRADING_DAY))

    assert calls == ["AAPL", "MSFT"]
    assert out["candidates"] == 2
    assert out["written"] == 2
    assert session.commits == 2  # one commit per researched candidate


def test_pre_market_caps_candidates(monkeypatch):
    async def _many(session, *, today=None):
        return _FakeCandidates([f"S{i}" for i in range(100)])

    researched = []

    async def _fake_research(session, user_id, symbol, *, analysis_type=None):
        researched.append(symbol)
        return _FakeDecision(symbol, Verdict.WATCH)

    monkeypatch.setattr(et, "select_daily_candidates", _many)
    monkeypatch.setattr(et, "research_equity", _fake_research)

    out = asyncio.run(_pre_market(_FakeSession(), today=TRADING_DAY))
    assert out["candidates"] == et.MAX_CANDIDATES
    assert len(researched) == et.MAX_CANDIDATES


def test_pre_market_one_bad_symbol_does_not_abort(monkeypatch):
    async def _fake_candidates(session, *, today=None):
        return _FakeCandidates(["AAPL", "BOOM", "MSFT"])

    async def _fake_research(session, user_id, symbol, *, analysis_type=None):
        if symbol == "BOOM":
            raise RuntimeError("simulated research failure")
        return _FakeDecision(symbol, Verdict.GO)

    monkeypatch.setattr(et, "select_daily_candidates", _fake_candidates)
    monkeypatch.setattr(et, "research_equity", _fake_research)

    session = _FakeSession()
    out = asyncio.run(_pre_market(session, today=TRADING_DAY))
    assert out["written"] == 2          # AAPL + MSFT survived
    assert session.rollbacks == 1       # BOOM rolled back, run continued


def test_pre_market_skips_non_trading_day(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("candidate selection must not run off-day")

    monkeypatch.setattr(et, "select_daily_candidates", _boom)
    out = asyncio.run(_pre_market(_FakeSession(), today=WEEKEND))
    assert out["skipped"] == "non_trading_day"


def test_pre_market_skips_holiday(monkeypatch):
    monkeypatch.setattr(
        et, "select_daily_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran on holiday")),
    )
    out = asyncio.run(_pre_market(_FakeSession(), today=HOLIDAY))
    assert out["skipped"] == "non_trading_day"


def test_pre_market_skips_when_no_user(monkeypatch):
    async def _no_user(session):
        return None

    monkeypatch.setattr(et, "_resolve_target_user", _no_user)
    monkeypatch.setattr(
        et, "select_daily_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran without a user")),
    )
    out = asyncio.run(_pre_market(_FakeSession(), today=TRADING_DAY))
    assert out["skipped"] == "no_user"


# --------------------------------------------------------------------------- #
# market_open                                                                   #
# --------------------------------------------------------------------------- #
def test_market_open_stamps_intent_on_go_only():
    go = _FakeDecision("AAPL", Verdict.GO, confidence=72)
    session = _FakeSession(results=[[go]])  # one execute -> the GO query

    out = asyncio.run(_market_open(session, today=TRADING_DAY))

    assert out["go"] == 1
    assert out["stamped"] == 1
    intent = go.indicators_snapshot["order_intent"]
    assert intent["executed"] is False          # intent only, no execution
    assert intent["status"] == "planned"
    assert intent["intent"] == "buy"
    assert session.commits == 1


def test_market_open_is_idempotent():
    already = _FakeDecision(
        "AAPL", Verdict.GO, snapshot={"order_intent": {"executed": False}}
    )
    session = _FakeSession(results=[[already]])
    out = asyncio.run(_market_open(session, today=TRADING_DAY))
    assert out["go"] == 1
    assert out["stamped"] == 0                   # already marked -> untouched


def test_market_open_skips_non_trading_day():
    session = _FakeSession(results=[[_FakeDecision("AAPL", Verdict.GO)]])
    out = asyncio.run(_market_open(session, today=WEEKEND))
    assert out["skipped"] == "non_trading_day"
    assert session.commits == 0


# --------------------------------------------------------------------------- #
# eod                                                                           #
# --------------------------------------------------------------------------- #
def test_eod_summarizes_verdicts():
    decisions = [
        _FakeDecision("AAPL", Verdict.GO, ai_invoked=True,
                      snapshot={"order_intent": {"executed": False}}),
        _FakeDecision("MSFT", Verdict.WATCH, ai_invoked=True),
        _FakeDecision("XOM", Verdict.NO_GO, ai_invoked=False),
    ]
    session = _FakeSession(results=[decisions])

    out = asyncio.run(_eod(session, today=TRADING_DAY))
    assert out["total"] == 3
    assert out["counts"] == {"go": 1, "watch": 1, "no-go": 1}
    assert out["go_names"] == ["AAPL"]
    assert out["intent_names"] == ["AAPL"]
    assert out["ai_invocations"] == 2
    assert session.commits == 0                  # read-only


def test_eod_skips_non_trading_day():
    out = asyncio.run(_eod(_FakeSession(), today=HOLIDAY))
    assert out["skipped"] == "non_trading_day"
