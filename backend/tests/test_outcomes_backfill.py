"""Unit tests for the recommendation-outcome pure computation layer.

Covers forward_outcome (anchor at trade_date open, session counting via the
frame itself, pending/partial horizons) and spearman_ic degenerate cases.
DB-touching backfill/report paths are exercised by the manual run, not here.
"""

from datetime import date

import pandas as pd
import pytest

from app.modules.simledger.outcomes import forward_outcome, spearman_ic


def _daily(rows):
    """rows: list of (iso_date, open, close) → get_bars-shaped frame.

    ts mirrors the store convention: midnight ET expressed in UTC (04:00Z).
    """
    return pd.DataFrame({
        "ts": [pd.Timestamp(f"{d}T04:00:00Z") for d, _, _ in rows],
        "open": [o for _, o, _ in rows],
        "high": [max(o, c) for _, o, c in rows],
        "low": [min(o, c) for _, o, c in rows],
        "close": [c for _, _, c in rows],
        "volume": [1_000] * len(rows),
    })


# Mon 2026-08-03 .. Mon 2026-08-10 with the 08-08/09 weekend gap — session
# counting must follow rows, not calendar days.
BARS = _daily([
    ("2026-08-03", 100.0, 101.0),
    ("2026-08-04", 101.0, 102.0),
    ("2026-08-05", 102.0, 104.0),
    ("2026-08-06", 104.0, 103.0),
    ("2026-08-07", 103.0, 106.0),
    ("2026-08-10", 106.0, 110.0),
])


class TestForwardOutcome:
    def test_full_horizons(self):
        out = forward_outcome(BARS, date(2026, 8, 4))
        assert out["base_open"] == 101.0
        assert out["close_d0"] == 102.0
        assert out["evaluated_through"] == date(2026, 8, 10)
        # anchored at 08-04 OPEN; +1/+3 sessions = 08-05 / 08-07 closes
        assert out["ret_1d"] == pytest.approx(104.0 / 101.0 - 1)
        assert out["ret_3d"] == pytest.approx(106.0 / 101.0 - 1)
        # +5 sessions would be 08-11 — not stored yet
        assert out["ret_5d"] is None

    def test_weekend_gap_counts_sessions_not_days(self):
        out = forward_outcome(BARS, date(2026, 8, 7))
        # +1 session after Friday is Monday 08-10
        assert out["ret_1d"] == pytest.approx(110.0 / 103.0 - 1)
        assert out["ret_3d"] is None

    def test_pending_when_trade_date_bar_missing(self):
        assert forward_outcome(BARS, date(2026, 8, 11)) is None   # future
        assert forward_outcome(BARS, date(2026, 8, 8)) is None    # weekend

    def test_empty_frame(self):
        assert forward_outcome(pd.DataFrame(), date(2026, 8, 4)) is None
        assert forward_outcome(None, date(2026, 8, 4)) is None

    def test_unsorted_input(self):
        shuffled = BARS.sample(frac=1, random_state=7).reset_index(drop=True)
        assert forward_outcome(shuffled, date(2026, 8, 4)) == \
            forward_outcome(BARS, date(2026, 8, 4))


class TestSpearmanIC:
    def test_perfect_monotonic(self):
        ic = spearman_ic(pd.Series([10, 20, 30, 40]),
                         pd.Series([0.01, 0.02, 0.03, 0.04]))
        assert ic == pytest.approx(1.0)

    def test_inverse(self):
        ic = spearman_ic(pd.Series([10, 20, 30, 40]),
                         pd.Series([0.04, 0.03, 0.02, 0.01]))
        assert ic == pytest.approx(-1.0)

    def test_degenerate(self):
        # too few points / constant series → n/a, never a crash
        assert spearman_ic(pd.Series([1, 2]), pd.Series([0.1, 0.2])) is None
        assert spearman_ic(pd.Series([5, 5, 5]), pd.Series([0.1, 0.2, 0.3])) is None
        assert spearman_ic(pd.Series([1, 2, 3]),
                           pd.Series([None, None, None], dtype=float)) is None
