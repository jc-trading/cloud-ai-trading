"""P6b deterministic news rules: anchor session, evaluation date, action
thresholds and the hit margin. Pure functions, no DB."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.modules.news import rules
from app.modules.news.rules import SourceStat, decide_action, evaluate_after, next_open_session

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc)
ANCHOR = date(2026, 10, 19)
VERIFIED = SourceStat(enabled=True, kind="author", n=20, lift=1.2)


@pytest.fixture(autouse=True)
def _thresholds(monkeypatch):
    for key, value in (("NEWS_SOURCE_MIN_N", 20), ("NEWS_ACTION_MIN_LIFT", 1.2),
                       ("NEWS_ACTION_MIN_PROB", 70), ("NEWS_REVIEW_MIN_PROB", 60),
                       ("NEWS_HIT_MIN_EXCESS_PCT", 1.0)):
        monkeypatch.setattr(settings, key, value)


def _et(*args) -> datetime:
    return datetime(*args, tzinfo=ET).astimezone(timezone.utc)


def _decide(*, claim_type="price", direction="up", probability=70, relevance="held",
            stats=(VERIFIED,)):
    return decide_action(claim_type=claim_type, direction=direction, probability=probability,
                         relevance=relevance, stats=list(stats), now=NOW, anchor=ANCHOR)


@pytest.mark.parametrize("at, expected", [
    (_et(2026, 10, 16, 15, 0), date(2026, 10, 19)),
    (_et(2026, 10, 19, 9, 29), date(2026, 10, 19)),
    (_et(2026, 10, 19, 9, 30, 0), date(2026, 10, 20)),
    (_et(2026, 10, 19, 9, 31), date(2026, 10, 20)),
    (_et(2026, 10, 17, 11, 0), date(2026, 10, 19)),
    (_et(2026, 11, 25, 17, 0), date(2026, 11, 27)),
])
def test_next_open_session(at, expected):
    assert next_open_session(at) == expected


def test_next_open_session_rejects_naive():
    with pytest.raises(ValueError):
        next_open_session(datetime(2026, 10, 16, 15, 0))


@pytest.mark.parametrize("anchor, horizon, expected", [
    (date(2026, 10, 19), 1, date(2026, 10, 19)),
    (date(2026, 10, 19), 5, date(2026, 10, 23)),
    (date(2026, 11, 25), 2, date(2026, 11, 27)),
])
def test_evaluate_after(anchor, horizon, expected):
    assert evaluate_after(anchor, horizon) == expected


@pytest.mark.parametrize("direction, action", [("up", "buy"), ("down", "sell")])
def test_verified_price_claim_is_traded(direction, action):
    got, reason, review_at = _decide(direction=direction)
    assert got == action
    assert reason == "verified source lift 1.20 (n=20), p=70"
    assert review_at == datetime(2026, 10, 19, 13, 30, tzinfo=timezone.utc)


def test_too_few_evaluations_is_unverified_review():
    got, reason, _ = _decide(relevance="shortlist",
                             stats=[SourceStat(True, "author", 19, 1.5)])
    assert got == "review" and "source unverified" in reason


def test_lift_below_action_min_is_review():
    got, reason, _ = _decide(probability=90, stats=[SourceStat(True, "author", 25, 1.19)])
    assert got == "review" and "lift 1.19" in reason


def test_probability_below_action_min_is_not_traded():
    assert _decide(probability=69)[0] not in ("buy", "sell")


def test_non_price_is_never_traded():
    assert _decide(claim_type="non_price", probability=95)[0] not in ("buy", "sell")


def test_all_sources_disabled_is_shadow_only():
    stats = [SourceStat(False, "platform", 50, 2.0), SourceStat(False, "author", 30, 1.5)]
    assert _decide(probability=99, stats=stats) == (
        "ignore", "source disabled (shadow scoring)", None)


def test_one_enabled_source_is_not_shadow():
    stats = [SourceStat(False, "author", 50, 2.0), SourceStat(True, "platform", 0, None)]
    got, reason, _ = _decide(probability=80, stats=stats)
    assert got == "review" and "source unverified" in reason


def test_no_sources_still_reviews():
    got, reason, _ = _decide(probability=80, stats=[])
    assert got == "review" and "source unverified" in reason


def test_pool_is_ignored():
    assert _decide(probability=90, relevance="pool", stats=[])[0] == "ignore"


def test_below_review_threshold_is_ignored():
    assert _decide(probability=59) == (
        "ignore", "below review threshold or not held/shortlisted", None)


@pytest.mark.parametrize("relevance, expected", [
    ("held", NOW),
    ("shortlist", datetime(2026, 10, 19, 13, 30, tzinfo=timezone.utc)),
])
def test_review_at(relevance, expected):
    got, _, review_at = _decide(probability=65, relevance=relevance)
    assert got == "review"
    assert review_at == expected
    assert type(review_at) is datetime


@pytest.mark.parametrize("expected, margin", [(1, 1), (2, 1), (5, 2.5)])
def test_hit_min_excess(expected, margin):
    assert rules.hit_min_excess(expected) == margin
