"""R0-3 calendar tests: XNYS sessions (incl. one-off closures) + advance()."""

from datetime import date

import pytest

from quant.data import calendar as cal
from quant.data.calendar import BusinessDayConvention as BDC


def test_one_off_and_holiday_closures():
    assert cal.is_trading_day("2012-10-29") is False   # Hurricane Sandy
    assert cal.is_trading_day("2012-10-30") is False   # Sandy day 2
    assert cal.is_trading_day("2012-10-31") is True
    assert cal.is_trading_day("2026-07-03") is False   # July 4 (Sat) observed Fri
    assert cal.is_trading_day("2026-07-24") is True


def test_advance_sessions():
    # Fri -> next session is Mon (skips weekend)
    assert cal.advance("2026-07-24", 1, unit="sessions") == date(2026, 7, 27)
    assert cal.advance("2026-07-27", -1, unit="sessions") == date(2026, 7, 24)
    # from a non-session date, +1 session = first session after it
    assert cal.advance("2026-07-25", 1, unit="sessions") == date(2026, 7, 27)  # Sat -> Mon
    assert cal.advance("2026-07-25", -1, unit="sessions") == date(2026, 7, 24)  # Sat -> Fri
    # 5 sessions forward across a weekend
    assert cal.advance("2026-07-20", 5, unit="sessions") == date(2026, 7, 27)


def test_advance_days_with_convention():
    # 2026-05-31 is a Sunday; FOLLOWING rolls to Mon 06-01 (next month)
    assert cal.adjust_to_business_day("2026-05-31", BDC.FOLLOWING) == date(2026, 6, 1)
    # MODIFIED_FOLLOWING must not cross the month -> rolls back to Fri 05-29
    assert cal.adjust_to_business_day("2026-05-31", BDC.MODIFIED_FOLLOWING) == date(2026, 5, 29)
    assert cal.adjust_to_business_day("2026-05-31", BDC.PRECEDING) == date(2026, 5, 29)
    assert cal.adjust_to_business_day("2026-05-31", BDC.UNADJUSTED) == date(2026, 5, 31)
    # holiday roll: July 3 2026 (observed) -> FOLLOWING = Mon 07-06, PRECEDING = Thu 07-02
    assert cal.adjust_to_business_day("2026-07-03", BDC.FOLLOWING) == date(2026, 7, 6)
    assert cal.adjust_to_business_day("2026-07-03", BDC.PRECEDING) == date(2026, 7, 2)


def test_sessions_in_range():
    days = cal.sessions_in_range("2026-07-20", "2026-07-24")
    assert days == [date(2026, 7, d) for d in (20, 21, 22, 23, 24)]
