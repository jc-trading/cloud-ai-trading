"""Unit tests for the per-night IC / Fama-MacBeth computation layer.

Covers the regression this module exists to prevent (pooled Spearman flipping
sign against the true within-night skill), the night-eligibility gate, the FM
and Newey-West estimators, era assignment on trade_date, and the JSON payload
contract. DB-touching paths are exercised by the manual run, not here.
"""

from datetime import date

import json

import pandas as pd
import pytest

from app.modules.simledger.outcomes import spearman_ic
from app.modules.simledger.outcomes_ic import (
    ERA_CUTS, analyse, assign_era, build_payload, effective_lag, fama_macbeth,
    newey_west_se, per_night_ic,
)


def _night(trade_date, confs, rets):
    """One trade_date's recs as a joined-frame slice."""
    return pd.DataFrame({
        "trade_date": [date.fromisoformat(trade_date)] * len(confs),
        "symbol": [f"S{i}" for i in range(len(confs))],
        "direction": ["up"] * len(confs),
        "confidence": [float(c) for c in confs],
        "shortlist_rank": [None] * len(confs),
        "ret_1d": [float(r) for r in rets],
        "ret_3d": [float(r) for r in rets],
        "ret_5d": [float(r) for r in rets],
    })


# Two nights, each perfectly ranked (within-night IC = +1). Night 2 carries the
# HIGHER confidences but the LOWER return level — exactly the shape that makes
# pooling lie.
NIGHT_1 = _night("2026-08-03", [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
                 [0.100 + 0.001 * k for k in range(10)])
NIGHT_2 = _night("2026-08-04", [110, 120, 130, 140, 150, 160, 170, 180, 190, 200],
                 [-0.100 + 0.001 * k for k in range(10)])
TWO_NIGHTS = pd.concat([NIGHT_1, NIGHT_2], ignore_index=True)


class TestPooledVsPerNight:
    def test_pooled_flips_sign_but_fama_macbeth_does_not(self):
        pooled = spearman_ic(TWO_NIGHTS["confidence"], TWO_NIGHTS["ret_1d"])
        assert pooled < 0                       # the metric this module replaces

        nights = per_night_ic(TWO_NIGHTS, 1)
        assert [r["ic"] for r in nights] == [pytest.approx(1.0)] * 2
        fm = fama_macbeth([r["ic"] for r in nights])
        assert fm["mean_ic"] == pytest.approx(1.0)
        assert fm["n_nights"] == 2


class TestPerNightIC:
    def test_min_n_gate(self):
        thin = _night("2026-08-05", [10, 20, 30], [0.01, 0.02, 0.03])
        assert per_night_ic(thin, 1)[0]["ic"] is None
        assert per_night_ic(thin, 1, min_n=3)[0]["ic"] == pytest.approx(1.0)

    def test_constant_confidence_night_dropped(self):
        flat = _night("2026-08-05", [50] * 10, [0.001 * k for k in range(10)])
        row = per_night_ic(flat, 1)[0]
        assert row["n"] == 10 and row["ic"] is None

    def test_missing_returns_shrink_n(self):
        df = NIGHT_1.copy()
        df.loc[df.index[:6], "ret_5d"] = float("nan")
        assert per_night_ic(df, 5)[0] == {"trade_date": date(2026, 8, 3),
                                          "n": 4, "ic": None}

    def test_empty_frame(self):
        assert per_night_ic(TWO_NIGHTS.iloc[0:0], 1) == []


class TestFamaMacbeth:
    def test_hand_computed(self):
        fm = fama_macbeth([0.2, 0.0, -0.2, 0.4])
        assert fm["n_nights"] == 4
        assert fm["mean_ic"] == pytest.approx(0.1)
        assert fm["se"] == pytest.approx(0.12909944, rel=1e-6)
        assert fm["t"] == pytest.approx(0.77459667, rel=1e-6)

    def test_zero_variance_gives_no_t(self):
        fm = fama_macbeth([1.0, 1.0, 1.0])
        assert fm["mean_ic"] == pytest.approx(1.0)
        assert fm["se"] == 0.0
        assert fm["t"] is None                  # never inf — must stay JSON-safe

    def test_degenerate_inputs(self):
        assert fama_macbeth([]) == {"mean_ic": None, "se": None, "t": None,
                                    "n_nights": 0}
        assert fama_macbeth([None, None])["n_nights"] == 0
        one = fama_macbeth([0.3, None])
        assert one["n_nights"] == 1 and one["se"] is None and one["t"] is None


class TestNeweyWest:
    def test_lag_zero_equals_naive(self):
        ics = [0.2, 0.0, -0.2, 0.4]
        assert newey_west_se(ics, lag=0) == pytest.approx(fama_macbeth(ics)["se"])

    def test_positive_autocorrelation_inflates_se(self):
        ics = [0.3, 0.28, 0.26, 0.24, 0.22, 0.20, 0.18, 0.16]
        assert newey_west_se(ics, lag=2) > newey_west_se(ics, lag=0)

    def test_negative_autocorrelation_shrinks_se(self):
        # sign-alternating ICs: the Bartlett cross-terms are negative, so NW
        # lands BELOW the naive SE — real behaviour, not a floor at zero
        ics = [0.3, -0.3, 0.3, -0.3, 0.3, -0.3, 0.3, -0.3]
        assert 0.0 <= newey_west_se(ics, lag=2) < newey_west_se(ics, lag=0)

    def test_refuses_under_identified_estimates(self):
        # 3 nights cannot support a lag-4 (nor a lag-2) Bartlett window
        assert newey_west_se([0.1, 0.2, 0.3], lag=4) is None
        assert newey_west_se([0.1, 0.2, 0.3], lag=2) is None
        assert newey_west_se([0.1, 0.2, 0.3], lag=0) is not None

    def test_lag_clamped_and_degenerate(self):
        ics = [0.1, 0.2, 0.3, 0.2, 0.1, 0.2, 0.3, 0.2]
        # clamped to n-1, which is then under-identified — refused, not crashed
        assert newey_west_se(ics, lag=99) is None
        assert newey_west_se(ics, lag=len(ics) - 1) is None
        assert newey_west_se([0.1], lag=0) is None
        assert newey_west_se([], lag=0) is None

    def test_effective_lag(self):
        assert effective_lag(3, 4) == 2
        assert effective_lag(10, 4) == 4
        assert effective_lag(1, 4) == 0
        assert effective_lag(6, -3) == 0


class TestAssignEra:
    @pytest.mark.parametrize("d,era", [
        ("2026-08-31", "pre"),
        ("2026-09-08", "pre"),
        ("2026-09-09", "phase_a"),
        ("2026-09-10", "phase_a"),
        ("2026-09-11", "phase_c"),
        ("2026-09-30", "phase_c"),
    ])
    def test_boundaries(self, d, era):
        assert assign_era(date.fromisoformat(d)) == era
        assert assign_era(d) == era             # ISO strings accepted too

    def test_cuts_overridable(self):
        assert assign_era("2026-09-09", cuts=("2026-09-15", "2026-09-20")) == "pre"


class TestAnalyse:
    def _mixed(self):
        """Eight pre-era nights, ICs alternating +1/-1 (five up, three down)."""
        dates = ['2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12']
        signs = [1, -1, 1, 1, -1, 1, -1, 1]
        return pd.concat(
            [_night(d, range(10, 110, 10),
                    [s * 0.001 * k for k in range(10)])
             for d, s in zip(dates, signs)], ignore_index=True)

    def _result(self):
        df = pd.concat([
            TWO_NIGHTS,                                     # pre
            _night("2026-09-09", range(10, 110, 10),
                   [0.001 * k for k in range(10)]),         # phase_a
            _night("2026-09-11", range(10, 110, 10),
                   [-0.001 * k for k in range(10)]),        # phase_c
        ], ignore_index=True)
        return df, analyse(df, total_recs=len(df) + 7)

    def test_era_counts_sum_to_global(self):
        _, res = self._result()
        assert sum(res["eras"][n]["n"] for n in ("pre", "phase_a", "phase_c")) \
            == res["global"]["n"] == res["analysed"]
        assert res["eras"]["pre"]["nights"] == 2
        assert res["eras"]["phase_c"]["horizons"]["1"]["mean_ic"] == \
            pytest.approx(-1.0)

    def test_pooled_and_fama_macbeth_disagree_end_to_end(self):
        res = analyse(TWO_NIGHTS, total_recs=len(TWO_NIGHTS))
        assert res["pooled_ic"]["1"]["ic"] < 0 < \
            res["global"]["horizons"]["1"]["mean_ic"]

    def test_newey_west_reported_only_when_identified(self):
        df = self._mixed()
        h3 = analyse(df, total_recs=len(df))["global"]["horizons"]["3"]
        assert h3["n_nights"] == 8 and h3["nw_lag"] == 2
        assert h3["mean_ic"] == pytest.approx(0.25)
        assert h3["nw_se"] != pytest.approx(h3["se"])
        assert h3["t_nw"] == pytest.approx(h3["mean_ic"] / h3["nw_se"])

        # only two nights — a lag-2 window is not identified
        thin = analyse(TWO_NIGHTS, total_recs=len(TWO_NIGHTS))
        thin3 = thin["global"]["horizons"]["3"]
        assert thin3["n_nights"] == 2
        assert thin3["nw_lag"] == 1
        assert thin3["nw_se"] is None and thin3["t_nw"] is None
        assert thin3["se"] is not None          # the naive SE still exists

    def test_pending_and_dropped_are_separate(self):
        df = self._mixed()
        df.loc[df["trade_date"] == date(2026, 8, 4), "ret_5d"] = float("nan")
        df.loc[df["trade_date"] == date(2026, 8, 5), "confidence"] = 50.0
        h5 = analyse(df, total_recs=len(df))["global"]["horizons"]["5"]
        assert h5["nights_pending"] == 1        # 08-04: no return stored yet
        assert h5["nights_dropped"] == 1        # 08-05: confidence has no spread
        assert h5["n_nights"] == 6

    def test_custom_cuts_move_nights_between_eras(self):
        df = self._mixed()
        default = analyse(df, total_recs=len(df))
        assert default["eras"]["pre"]["nights"] == 8
        assert default["eras"]["phase_a"]["n"] == 0

        shifted = analyse(df, total_recs=len(df),
                          cuts=("2026-08-06", "2026-08-11"))
        assert shifted["era_cuts"] == ["2026-08-06", "2026-08-11"]
        assert [shifted["eras"][n]["nights"]
                for n in ("pre", "phase_a", "phase_c")] == [3, 3, 2]
        assert sum(shifted["eras"][n]["n"]
                   for n in ("pre", "phase_a", "phase_c")) == \
            shifted["global"]["n"]

    def test_payload_is_json_safe(self):
        _, res = self._result()
        # non-finite leaves must never reach the file
        res["global"]["horizons"]["1"]["_probe_nan"] = float("nan")
        res["global"]["horizons"]["1"]["_probe_inf"] = float("inf")
        res["global"]["coverage"][0]["_probe_neg_inf"] = float("-inf")

        payload = build_payload(res)
        assert payload["global"]["horizons"]["1"]["_probe_nan"] is None
        assert payload["global"]["horizons"]["1"]["_probe_inf"] is None
        assert payload["global"]["coverage"][0]["_probe_neg_inf"] is None

        raw = json.dumps(payload, allow_nan=False)
        assert "NaN" not in raw and "Infinity" not in raw
        loaded = json.loads(raw)
        assert loaded["era_cuts"] == list(ERA_CUTS)
        night = loaded["eras"]["pre"]["horizons"]["1"]["per_night"][0]
        assert night["trade_date"] == "2026-08-03"
        assert loaded["global"]["coverage"][0]["trade_date"] == "2026-08-03"

    def test_empty_frame_still_produces_every_era(self):
        empty = TWO_NIGHTS.iloc[0:0]
        res = analyse(empty, total_recs=0)
        assert set(res["eras"]) == {"pre", "phase_a", "phase_c"}
        assert all(res["eras"][n]["n"] == 0 for n in res["eras"])
        json.dumps(build_payload(res), allow_nan=False)
