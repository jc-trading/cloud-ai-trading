"""Tests for the equity research agent (candidate -> Decision).

Standalone — no real DB and no network/Claude. The FA cache is faked with a
queued-result session, the earnings-day reaction is a stub, and Claude is a
mock async callable. Asserts the acceptance criteria:

  * a single asset_class=equity Decision is produced with all fields;
  * verdict follows the SPEC bands (>=65 go / 50-64 watch / <50 no-go);
  * critical data (EPS/revenue) missing -> auto no-go AND Claude NOT called;
  * Claude only called past the gate, and None-safe (None -> still a Decision);
  * pure helpers behave on the boundaries.
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.analysis.models import AssetClass, TradeAction, Verdict
from app.modules.equity import research
from app.modules.equity.research import (
    beat_pct,
    business_days_ago,
    normalize_guidance,
    research_equity,
)


# ---- fakes ----------------------------------------------------------------


class _FakeScalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _FakeScalars(self._value)


class _FakeSession:
    """Returns queued scalar objects for successive execute() calls; records adds."""

    def __init__(self, results):
        self._results = list(results)
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
        return None


class _Earnings:
    def __init__(self, *, eps_actual, eps_estimate, rev_actual, rev_estimate,
                 report_date, time="amc"):
        self.eps_actual = eps_actual
        self.eps_estimate = eps_estimate
        self.rev_actual = rev_actual
        self.rev_estimate = rev_estimate
        self.report_date = report_date
        self.time = time


class _Fundamentals:
    def __init__(self, name="Test Co", sector="Technology", is_sp500=True):
        self.name = name
        self.sector = sector
        self.is_sp500 = is_sp500


def _recent_business_day(days_back=1):
    d = date.today()
    while days_back > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days_back -= 1
    return d


async def _stub_reaction(symbol, report_date, report_time):
    return 6.0  # in the +3-8% peak band


def _make_claude(captured, guidance="raised"):
    async def _claude(*, symbol, context, news):
        captured["called"] = True
        captured["context"] = context
        return {
            "guidance": guidance,
            "news_sentiment": "positive",
            "reasoning": "Strong beat and raised guidance.",
            "key_factors": ["beat", "guidance"],
            "tokens_used": 100,
            "api_cost": 0.0001,
            "provider": "claude",
        }

    return _claude


# ---- pure helpers ---------------------------------------------------------


def test_beat_pct_basic():
    assert beat_pct(Decimal("1.10"), Decimal("1.00")) == pytest.approx(10.0)
    assert beat_pct(Decimal("0.90"), Decimal("1.00")) == pytest.approx(-10.0)


def test_beat_pct_missing_or_zero_estimate():
    assert beat_pct(None, Decimal("1.0")) is None
    assert beat_pct(Decimal("1.0"), None) is None
    assert beat_pct(Decimal("1.0"), Decimal("0")) is None  # undefined base


def test_business_days_ago_weekends_and_future():
    monday = date(2026, 6, 29)
    # Thursday before is 3 business days earlier (Fri, Mon, Tue... count weekdays)
    assert business_days_ago(date(2026, 6, 26), monday) == 1  # Fri -> Mon = 1 wd
    assert business_days_ago(monday, monday) == 0
    assert business_days_ago(date(2026, 6, 30), monday) == -1  # future -> negative


def test_normalize_guidance():
    assert normalize_guidance("Raised") == "raised"
    assert normalize_guidance("cut") == "cut"
    assert normalize_guidance("maintained") == "maintained"
    assert normalize_guidance("unknown") is None
    assert normalize_guidance(None) is None


# ---- full pipeline --------------------------------------------------------


def test_go_verdict_calls_claude_and_writes_equity_decision():
    captured = {}
    earnings = _Earnings(
        eps_actual=Decimal("1.10"), eps_estimate=Decimal("1.00"),   # +10% -> full
        rev_actual=Decimal("104"), rev_estimate=Decimal("100"),     # +4% -> full
        report_date=_recent_business_day(1),
    )
    session = _FakeSession([earnings, _Fundamentals()])

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_stub_reaction,
            claude_analyzer=_make_claude(captured, guidance="raised"),
        )
    )

    assert captured.get("called") is True                       # Claude gated-in
    assert result.asset_class == AssetClass.EQUITY
    assert result.verdict == Verdict.GO
    assert result.confidence >= 65
    assert result.ai_invoked is True
    assert result.action == TradeAction.BUY
    # Field completeness (acceptance 1)
    assert result.indicators_snapshot["eps_beat_pct"] == pytest.approx(10.0)
    assert result.indicators_snapshot["score"]["path"] == "catalyst"
    assert result.data_completeness["eps_beat_pct"] is True
    assert result.data_completeness["ai_qualitative"] is True
    assert result.claude_response.get("guidance") == "raised"
    assert result.verdict_reason
    assert session.flushes == 1


def test_critical_data_missing_auto_no_go_no_claude():
    captured = {}
    earnings = _Earnings(
        eps_actual=None, eps_estimate=None,                       # EPS missing
        rev_actual=Decimal("104"), rev_estimate=Decimal("100"),
        report_date=_recent_business_day(1),
    )
    session = _FakeSession([earnings, _Fundamentals()])

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_stub_reaction,
            claude_analyzer=_make_claude(captured),
        )
    )

    assert result.verdict == Verdict.NO_GO                       # auto no-go
    assert result.confidence == 0
    assert result.ai_invoked is False
    assert result.ai_skip_reason == "critical_data_missing"
    assert captured.get("called") is None                        # Claude NOT called
    assert result.data_completeness["eps_beat_pct"] is False


def test_stale_earnings_recency_gate_no_go_no_claude():
    captured = {}
    earnings = _Earnings(
        eps_actual=Decimal("1.10"), eps_estimate=Decimal("1.00"),
        rev_actual=Decimal("104"), rev_estimate=Decimal("100"),
        report_date=_recent_business_day(10),                    # stale (> 3 wd)
    )
    session = _FakeSession([earnings, _Fundamentals()])

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_stub_reaction,
            claude_analyzer=_make_claude(captured),
        )
    )

    assert result.verdict == Verdict.NO_GO
    assert result.ai_invoked is False
    assert result.ai_skip_reason == "score_or_recency_gate_not_passed"
    assert captured.get("called") is None                        # gate blocked Claude


def test_claude_none_is_none_safe_still_writes_decision():
    async def _claude_none(*, symbol, context, news):
        return None

    earnings = _Earnings(
        eps_actual=Decimal("1.10"), eps_estimate=Decimal("1.00"),
        rev_actual=Decimal("104"), rev_estimate=Decimal("100"),
        report_date=_recent_business_day(1),
    )
    session = _FakeSession([earnings, _Fundamentals()])

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_stub_reaction,
            claude_analyzer=_claude_none,
        )
    )

    # Still a full equity Decision, verdict from the score, no crash.
    assert result.asset_class == AssetClass.EQUITY
    assert result.verdict == Verdict.GO
    assert result.ai_invoked is False
    assert result.ai_skip_reason == "ai_unavailable"
    assert result.claude_response == {}


def test_guidance_cut_from_claude_pulls_verdict_down():
    captured = {}
    # Base composite (guidance unknown) must clear the gate so Claude is called:
    # EPS +5% full (30) + rev +3% full (20) + reaction 0 = 50 -> watch. Then the
    # Claude-reported guidance CUT re-scores (-25) -> 25 -> no-go.
    earnings = _Earnings(
        eps_actual=Decimal("1.05"), eps_estimate=Decimal("1.00"),
        rev_actual=Decimal("103"), rev_estimate=Decimal("100"),
        report_date=_recent_business_day(1),
    )
    session = _FakeSession([earnings, _Fundamentals()])

    async def _no_reaction(symbol, report_date, report_time):
        return 0.0

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_no_reaction,
            claude_analyzer=_make_claude(captured, guidance="cut"),
        )
    )

    assert captured.get("called") is True
    assert result.indicators_snapshot["guidance_applied"] == "cut"
    # A guidance cut (-25 weight) drags the modest composite down to no-go.
    assert result.verdict == Verdict.NO_GO
    assert result.ai_invoked is True


def test_watch_band_verdict():
    captured = {}
    # EPS +2% partial (0.40*30=12), rev +1% partial (0.40*20=8), reaction +6 full
    # (25), guidance raised (25) -> ~70 would be go; use weaker to land 50-64.
    # EPS +2% (12), rev +1% (8), reaction +2% (~0.7*25=17.5), no guidance -> ~37.5
    # Instead: EPS +5% full (30) + rev 0 + reaction 0 + guidance none = 30 (no-go).
    # Target watch: EPS +5 full (30) + rev +3 full (20) = 50 exactly -> watch.
    earnings = _Earnings(
        eps_actual=Decimal("1.05"), eps_estimate=Decimal("1.00"),  # +5% full -> 30
        rev_actual=Decimal("103"), rev_estimate=Decimal("100"),    # +3% full -> 20
        report_date=_recent_business_day(1),
    )
    session = _FakeSession([earnings, _Fundamentals()])

    async def _no_reaction(symbol, report_date, report_time):
        return 0.0

    async def _claude_no_guidance(*, symbol, context, news):
        captured["called"] = True
        return {"guidance": "unknown", "news_sentiment": "neutral",
                "reasoning": "Mixed.", "key_factors": []}

    result = asyncio.run(
        research_equity(
            session, uuid4(), "AAPL",
            reaction_fetcher=_no_reaction,
            claude_analyzer=_claude_no_guidance,
        )
    )

    assert result.verdict == Verdict.WATCH
    assert 50 <= result.confidence < 65
    assert captured.get("called") is True  # watch-band still past the gate
