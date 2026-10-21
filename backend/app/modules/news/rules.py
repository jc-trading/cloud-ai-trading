"""Deterministic news rules (P6b): which session a claim is anchored to, when
it is evaluated, and which action it suggests. Pure functions, no DB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant.data import calendar as qcal

from app.config import settings

_ET = ZoneInfo("America/New_York")
_SCAN_DAYS = 10


def next_open_session(ts_utc: datetime) -> date:
    """The first trading session whose regular open is strictly after ts_utc."""
    if ts_utc.tzinfo is None:
        raise ValueError("ts_utc must be timezone-aware")
    d = ts_utc.astimezone(_ET).date()
    for _ in range(_SCAN_DAYS + 1):
        if qcal.is_trading_day(d) and qcal.rth_bounds(d)[0].to_pydatetime() > ts_utc:
            return d
        d += timedelta(days=1)
    raise ValueError(f"no session opens within {_SCAN_DAYS} days of {ts_utc.isoformat()}")


def evaluate_after(anchor: date, horizon: int) -> date:
    return qcal.advance(anchor, horizon - 1, unit="sessions")


@dataclass(frozen=True)
class SourceStat:
    enabled: bool
    kind: str
    n: int
    lift: float | None


def best_stat(stats: list[SourceStat], s=settings) -> SourceStat | None:
    scored = [x for x in stats if x.enabled and x.n >= s.NEWS_SOURCE_MIN_N and x.lift is not None]
    return max(scored, key=lambda x: x.lift, default=None)


def hit_min_excess(expected: float, s=settings) -> float:
    return max(s.NEWS_HIT_MIN_EXCESS_PCT, expected / 2)


def decide_action(*, claim_type: str, direction: str, probability: int, relevance: str | None,
                  stats: list[SourceStat], now: datetime, anchor: date,
                  s=settings) -> tuple[str, str, datetime | None]:
    """(action, reason, review_at) for one claim. buy/sell needs a verified
    source (enough evaluated claims and lift) and a price claim; review needs a
    held or shortlisted name; a story whose every source is disabled is only
    shadow-scored."""
    if stats and all(not x.enabled for x in stats):
        return "ignore", "source disabled (shadow scoring)", None
    b = best_stat(stats, s)
    if (claim_type == "price" and b and b.lift >= s.NEWS_ACTION_MIN_LIFT
            and probability >= s.NEWS_ACTION_MIN_PROB):
        open_at = qcal.rth_bounds(anchor)[0].to_pydatetime()
        return ("buy" if direction == "up" else "sell",
                f"verified source lift {b.lift:.2f} (n={b.n}), p={probability}", open_at)
    if probability >= s.NEWS_REVIEW_MIN_PROB and relevance in ("held", "shortlist"):
        if b is None:
            reason = f"p={probability}, {relevance}, source unverified"
        else:
            reason = f"p={probability}, {relevance}, source lift {b.lift:.2f} (n={b.n})"
        review_at = now if relevance == "held" else qcal.rth_bounds(anchor)[0].to_pydatetime()
        return "review", reason, review_at
    return "ignore", "below review threshold or not held/shortlisted", None
