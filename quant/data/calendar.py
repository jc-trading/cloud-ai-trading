"""XNYS trading calendar + business-day-aware date arithmetic (design §8.2, §14).

Wraps exchange_calendars (the NYSE calendar covers full-day closes, early closes
and one-off halts like Sandy 2012-10-29, which a naive weekday check would miss).
``advance()`` is the single place date arithmetic happens, with an explicit
BusinessDayConvention — never scatter ``+ timedelta(days=1)`` around the code.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

import exchange_calendars as xcals
import pandas as pd

_CAL = xcals.get_calendar("XNYS")
_SESSIONS = _CAL.sessions  # DatetimeIndex of all trading days (tz-naive, normalized)


class BusinessDayConvention(str, Enum):
    FOLLOWING = "following"
    MODIFIED_FOLLOWING = "modified_following"
    PRECEDING = "preceding"
    MODIFIED_PRECEDING = "modified_preceding"
    UNADJUSTED = "unadjusted"
    NEAREST = "nearest"


def _ts(d: date | str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def is_trading_day(d: date | str | pd.Timestamp) -> bool:
    return bool(_CAL.is_session(_ts(d)))


def next_session(d: date | str | pd.Timestamp) -> date:
    """First trading day STRICTLY after d (d need not itself be a session)."""
    ts = _ts(d)
    if _CAL.is_session(ts):
        return _CAL.next_session(ts).date()
    return _CAL.date_to_session(ts, direction="next").date()


def previous_session(d: date | str | pd.Timestamp) -> date:
    """First trading day STRICTLY before d (d need not itself be a session)."""
    ts = _ts(d)
    if _CAL.is_session(ts):
        return _CAL.previous_session(ts).date()
    return _CAL.date_to_session(ts, direction="previous").date()


def sessions_in_range(start, end) -> list[date]:
    idx = _CAL.sessions_in_range(_ts(start), _ts(end))
    return [t.date() for t in idx]


def adjust_to_business_day(d: date | str | pd.Timestamp,
                           convention: BusinessDayConvention = BusinessDayConvention.FOLLOWING) -> date:
    """Roll a (possibly non-trading) date onto a trading day per convention."""
    ts = _ts(d)
    if is_trading_day(ts):
        return ts.date()
    c = convention
    if c is BusinessDayConvention.UNADJUSTED:
        return ts.date()
    if c is BusinessDayConvention.FOLLOWING:
        return next_session(ts)
    if c is BusinessDayConvention.PRECEDING:
        return previous_session(ts)
    if c is BusinessDayConvention.MODIFIED_FOLLOWING:
        nxt = next_session(ts)
        return nxt if nxt.month == ts.month else previous_session(ts)
    if c is BusinessDayConvention.MODIFIED_PRECEDING:
        prev = previous_session(ts)
        return prev if prev.month == ts.month else next_session(ts)
    if c is BusinessDayConvention.NEAREST:
        nxt, prev = next_session(ts), previous_session(ts)
        # tie -> forward (QuantLib convention)
        return nxt if (nxt - ts.date()) <= (ts.date() - prev) else prev
    raise ValueError(c)


def advance(d: date | str | pd.Timestamp, n: int, *, unit: str = "sessions",
            convention: BusinessDayConvention = BusinessDayConvention.FOLLOWING) -> date:
    """Advance a date by n units.

    unit='sessions' : n trading sessions forward/back (the useful one — holding
        periods, T+1/T+2 settlement). d need not itself be a trading day.
    unit='days'     : n calendar days, then roll onto a trading day per convention.
    """
    ts = _ts(d)
    if unit == "days":
        return adjust_to_business_day(ts + pd.Timedelta(days=n), convention)
    if unit == "sessions":
        pos = _SESSIONS.searchsorted(ts)  # first index with _SESSIONS[pos] >= ts
        is_session = pos < len(_SESSIONS) and _SESSIONS[pos] == ts
        if is_session:
            idx = pos + n
        elif n == 0:
            return adjust_to_business_day(ts, convention)
        elif n > 0:
            idx = pos + n - 1          # _SESSIONS[pos] is the 1st session after ts
        else:
            idx = pos + n              # _SESSIONS[pos-1] is the 1st session before ts
        if idx < 0 or idx >= len(_SESSIONS):
            raise IndexError(f"advance {n} sessions from {ts.date()} out of calendar range")
        return _SESSIONS[idx].date()
    raise ValueError(f"unit must be 'sessions' or 'days', got {unit!r}")
