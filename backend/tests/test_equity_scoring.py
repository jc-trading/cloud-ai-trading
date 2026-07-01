"""Unit tests for the equity entry scoring module (pure functions).

Covers the SPEC boundary cases explicitly:
  * EPS exactly 5% (full) vs 2% boundary (partial) vs 1.99% (zero)
  * Revenue 3% / 1% / 0.99% boundaries
  * Reaction curve at -1% / +1% / +3% / +8% / +12% / +13% (chasing decay)
  * Guidance three-state (raised / maintained / cut) + missing
  * Critical-data-missing (EPS/revenue None) -> auto no-go, scorable=False
  * Recency gate (fresh vs stale earnings)
  * Exhaustion correction (pre-earnings run-up)
  * Momentum path (qualified / disqualified / missing)
  * Hard vetoes override
  * The acceptance spot-check (EPS+10/rev+4/raised/reaction+6 -> ~full)

These are pure functions — no DB, no network — so nothing is mocked.
"""

import pytest

from app.modules.equity.scoring import (
    GO_THRESHOLD,
    WATCH_THRESHOLD,
    W_EPS,
    W_REVENUE,
    W_GUIDANCE,
    W_REACTION,
    VERDICT_GO,
    VERDICT_WATCH,
    VERDICT_NO_GO,
    PATH_CATALYST,
    PATH_MOMENTUM,
    HardVetoInputs,
    ScoreResult,
    score_catalyst,
    score_momentum,
    score_equity,
    apply_hard_vetoes,
    verdict_for,
)


def _comp(result: ScoreResult, name: str):
    return next(c for c in result.components if c.name == name)


# --------------------------------------------------------------------------- #
# Weight sanity                                                                #
# --------------------------------------------------------------------------- #
def test_catalyst_weights_sum_to_100():
    assert W_EPS + W_REVENUE + W_GUIDANCE + W_REACTION == 100.0


def test_verdict_bands():
    assert verdict_for(65.0) == VERDICT_GO
    assert verdict_for(64.9) == VERDICT_WATCH
    assert verdict_for(50.0) == VERDICT_WATCH
    assert verdict_for(49.9) == VERDICT_NO_GO
    assert verdict_for(0.0) == VERDICT_NO_GO
    assert GO_THRESHOLD == 65.0 and WATCH_THRESHOLD == 50.0


# --------------------------------------------------------------------------- #
# EPS band boundaries                                                          #
# --------------------------------------------------------------------------- #
def test_eps_exactly_5pct_is_full():
    r = score_catalyst(eps_beat_pct=5.0, rev_beat_pct=0.0, earnings_days_ago=1)
    assert _comp(r, "eps_beat").points == pytest.approx(W_EPS)


def test_eps_2pct_boundary_is_partial_not_zero():
    r = score_catalyst(eps_beat_pct=2.0, rev_beat_pct=0.0, earnings_days_ago=1)
    pts = _comp(r, "eps_beat").points
    assert 0.0 < pts < W_EPS
    # partial band starts at 40% of weight
    assert pts == pytest.approx(0.40 * W_EPS)


def test_eps_just_below_2pct_is_zero():
    r = score_catalyst(eps_beat_pct=1.99, rev_beat_pct=0.0, earnings_days_ago=1)
    assert _comp(r, "eps_beat").points == 0.0


def test_eps_miss_is_zero():
    r = score_catalyst(eps_beat_pct=-4.0, rev_beat_pct=0.0, earnings_days_ago=1)
    assert _comp(r, "eps_beat").points == 0.0


def test_eps_partial_is_monotonic():
    lo = _comp(score_catalyst(eps_beat_pct=3.0, rev_beat_pct=0.0, earnings_days_ago=1), "eps_beat").points
    hi = _comp(score_catalyst(eps_beat_pct=4.0, rev_beat_pct=0.0, earnings_days_ago=1), "eps_beat").points
    assert lo < hi < W_EPS


# --------------------------------------------------------------------------- #
# Revenue band boundaries                                                      #
# --------------------------------------------------------------------------- #
def test_revenue_exactly_3pct_is_full():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=3.0, earnings_days_ago=1)
    assert _comp(r, "revenue_beat").points == pytest.approx(W_REVENUE)


def test_revenue_1pct_boundary_is_partial():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=1.0, earnings_days_ago=1)
    pts = _comp(r, "revenue_beat").points
    assert pts == pytest.approx(0.40 * W_REVENUE)


def test_revenue_just_below_1pct_is_zero():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.99, earnings_days_ago=1)
    assert _comp(r, "revenue_beat").points == 0.0


# --------------------------------------------------------------------------- #
# Reaction curve — the SPEC-named points                                      #
# --------------------------------------------------------------------------- #
def _reaction_points(reaction_pct):
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, reaction_pct=reaction_pct, earnings_days_ago=1)
    return _comp(r, "reaction").points


def test_reaction_negative_gives_nothing():
    assert _reaction_points(-1.0) == 0.0


def test_reaction_peak_band_full():
    assert _reaction_points(3.0) == pytest.approx(W_REACTION)
    assert _reaction_points(6.0) == pytest.approx(W_REACTION)
    assert _reaction_points(8.0) == pytest.approx(W_REACTION)


def test_reaction_plus1_is_positive_but_below_peak():
    p1 = _reaction_points(1.0)
    assert 0.0 < p1 < W_REACTION
    assert p1 == pytest.approx(0.40 * W_REACTION)


def test_reaction_12_faded_below_peak():
    p12 = _reaction_points(12.0)
    assert 0.0 < p12 < W_REACTION
    assert p12 == pytest.approx(0.30 * W_REACTION)


def test_reaction_13_decays_below_12_chasing():
    """>12% must give LESS than +12% (追高 penalty)."""
    assert _reaction_points(13.0) < _reaction_points(12.0)


def test_reaction_curve_peaks_in_middle():
    """Peak 3-8 must beat both the ramp-up (+2) and the fade (+10)."""
    peak = _reaction_points(5.0)
    assert peak > _reaction_points(2.0)
    assert peak > _reaction_points(10.0)


def test_reaction_far_chase_can_hit_zero():
    assert _reaction_points(30.0) == 0.0


# --------------------------------------------------------------------------- #
# Guidance three-state + missing                                              #
# --------------------------------------------------------------------------- #
def test_guidance_raised_full():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, guidance="raised", earnings_days_ago=1)
    assert _comp(r, "guidance").points == pytest.approx(W_GUIDANCE)


def test_guidance_maintained_partial():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, guidance="maintained", earnings_days_ago=1)
    assert _comp(r, "guidance").points == pytest.approx(0.5 * W_GUIDANCE)


def test_guidance_cut_is_strong_negative():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, guidance="cut", earnings_days_ago=1)
    assert _comp(r, "guidance").points == pytest.approx(-W_GUIDANCE)


def test_guidance_case_insensitive():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, guidance="RAISED", earnings_days_ago=1)
    assert _comp(r, "guidance").points == pytest.approx(W_GUIDANCE)


def test_guidance_missing_is_neutral_and_flagged():
    r = score_catalyst(eps_beat_pct=0.0, rev_beat_pct=0.0, guidance=None, earnings_days_ago=1)
    assert _comp(r, "guidance").points == 0.0
    assert r.data_completeness["guidance"] is False
    assert r.scorable is True  # guidance is NOT critical


def test_guidance_cut_can_pull_composite_down():
    """A guidance cut must reduce the composite vs. maintained, all else equal."""
    cut = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="cut",
                         reaction_pct=6.0, earnings_days_ago=1).composite
    maintained = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="maintained",
                                reaction_pct=6.0, earnings_days_ago=1).composite
    assert cut < maintained


# --------------------------------------------------------------------------- #
# Critical data missing -> auto no-go, no guessing                            #
# --------------------------------------------------------------------------- #
def test_missing_eps_is_auto_no_go():
    r = score_catalyst(eps_beat_pct=None, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=1)
    assert r.verdict == VERDICT_NO_GO
    assert r.scorable is False
    assert r.composite == 0.0
    assert r.components == []
    assert r.data_completeness["eps_beat_pct"] is False


def test_missing_revenue_is_auto_no_go():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=None, earnings_days_ago=1)
    assert r.verdict == VERDICT_NO_GO
    assert r.scorable is False


def test_missing_both_critical_is_auto_no_go():
    r = score_catalyst(eps_beat_pct=None, rev_beat_pct=None)
    assert r.verdict == VERDICT_NO_GO
    assert r.scorable is False
    assert any("missing" in x.lower() for x in r.reasons)


# --------------------------------------------------------------------------- #
# Recency gate                                                                 #
# --------------------------------------------------------------------------- #
def test_recency_fresh_earnings_can_go():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=1)
    assert r.verdict == VERDICT_GO


def test_recency_stale_earnings_downgraded_to_no_go():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=5)
    assert r.verdict == VERDICT_NO_GO
    # composite itself stays high (transparency) — only the verdict is vetoed
    assert r.composite >= GO_THRESHOLD
    assert any("stale" in x.lower() for x in r.reasons)


def test_recency_unknown_flags_but_does_not_veto():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=None)
    assert r.verdict == VERDICT_GO
    assert r.data_completeness["earnings_days_ago"] is False
    assert any("not confirmed" in x.lower() or "not vetoed" in x.lower() for x in r.reasons)


def test_recency_boundary_3_days_ok():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=3)
    assert r.verdict == VERDICT_GO


def test_recency_negative_days_no_go():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=-1)
    assert r.verdict == VERDICT_NO_GO


# --------------------------------------------------------------------------- #
# Exhaustion correction                                                        #
# --------------------------------------------------------------------------- #
def test_exhaustion_no_penalty_under_threshold():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, ran_up_pre_pct=10.0, earnings_days_ago=1)
    assert _comp(r, "exhaustion").points == 0.0


def test_exhaustion_penalizes_big_runup():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, ran_up_pre_pct=25.0, earnings_days_ago=1)
    assert _comp(r, "exhaustion").points < 0.0


def test_exhaustion_penalty_is_capped():
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, ran_up_pre_pct=200.0, earnings_days_ago=1)
    assert _comp(r, "exhaustion").points == pytest.approx(-15.0)


def test_exhaustion_lowers_composite():
    clean = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                           reaction_pct=6.0, ran_up_pre_pct=0.0, earnings_days_ago=1).composite
    exhausted = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                               reaction_pct=6.0, ran_up_pre_pct=30.0, earnings_days_ago=1).composite
    assert exhausted < clean


# --------------------------------------------------------------------------- #
# Composite clamp                                                              #
# --------------------------------------------------------------------------- #
def test_composite_never_negative():
    r = score_catalyst(eps_beat_pct=-10.0, rev_beat_pct=-10.0, guidance="cut",
                       reaction_pct=-5.0, ran_up_pre_pct=50.0, earnings_days_ago=1)
    assert r.composite == 0.0


def test_composite_never_above_100():
    r = score_catalyst(eps_beat_pct=50.0, rev_beat_pct=50.0, guidance="raised",
                       reaction_pct=5.0, earnings_days_ago=1)
    assert r.composite <= 100.0


# --------------------------------------------------------------------------- #
# Acceptance spot-check                                                        #
# --------------------------------------------------------------------------- #
def test_spotcheck_strong_catalyst_near_full():
    """EPS+10 / rev+4 / raised / reaction+6 -> should be ~full and a GO."""
    r = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                       reaction_pct=6.0, earnings_days_ago=1)
    assert r.composite == pytest.approx(100.0)
    assert r.verdict == VERDICT_GO


def test_spotcheck_reaction_13_decays_composite():
    """Same strong setup but reaction +13% must score BELOW the +6% version."""
    strong = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                            reaction_pct=6.0, earnings_days_ago=1).composite
    chase = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                           reaction_pct=13.0, earnings_days_ago=1).composite
    assert chase < strong


# --------------------------------------------------------------------------- #
# Momentum path                                                                #
# --------------------------------------------------------------------------- #
def test_momentum_fully_qualified_is_go():
    r = score_momentum(golden_cross_days=2, pct_from_52w_high=1.0, sector_week_positive=True)
    assert r.path == PATH_MOMENTUM
    assert r.composite == pytest.approx(100.0)
    assert r.verdict == VERDICT_GO


def test_momentum_stale_cross_disqualified():
    r = score_momentum(golden_cross_days=15, pct_from_52w_high=1.0, sector_week_positive=True)
    assert _comp(r, "golden_cross").points == 0.0
    assert r.verdict != VERDICT_GO


def test_momentum_too_far_from_high_zero():
    r = score_momentum(golden_cross_days=2, pct_from_52w_high=5.0, sector_week_positive=True)
    assert _comp(r, "near_52w_high").points == 0.0


def test_momentum_boundary_3pct_from_high_counts():
    r = score_momentum(golden_cross_days=2, pct_from_52w_high=3.0, sector_week_positive=True)
    assert _comp(r, "near_52w_high").points > 0.0


def test_momentum_red_sector_loses_points():
    green = score_momentum(golden_cross_days=2, pct_from_52w_high=1.0, sector_week_positive=True).composite
    red = score_momentum(golden_cross_days=2, pct_from_52w_high=1.0, sector_week_positive=False).composite
    assert red < green


def test_momentum_boundary_10_day_cross_counts():
    r = score_momentum(golden_cross_days=10, pct_from_52w_high=1.0, sector_week_positive=True)
    assert _comp(r, "golden_cross").points > 0.0


def test_momentum_missing_data_scored_conservatively():
    r = score_momentum(golden_cross_days=None, pct_from_52w_high=None, sector_week_positive=None)
    assert r.composite == 0.0
    assert r.verdict == VERDICT_NO_GO
    assert r.data_completeness["golden_cross_days"] is False


def test_momentum_is_independent_of_catalyst():
    """Momentum needs no earnings data at all."""
    r = score_momentum(golden_cross_days=1, pct_from_52w_high=0.5, sector_week_positive=True)
    assert r.verdict == VERDICT_GO  # no EPS/rev anywhere


# --------------------------------------------------------------------------- #
# Hard vetoes                                                                   #
# --------------------------------------------------------------------------- #
def test_hard_veto_overrides_go_to_no_go():
    good = score_catalyst(eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised",
                         reaction_pct=6.0, earnings_days_ago=1)
    assert good.verdict == VERDICT_GO
    vetoed = apply_hard_vetoes(good, HardVetoInputs(macro_event_today=True))
    assert vetoed.verdict == VERDICT_NO_GO
    # composite preserved for transparency
    assert vetoed.composite == good.composite
    assert any("HARD VETO" in x for x in vetoed.reasons)


def test_hard_veto_penny_stock():
    good = score_momentum(golden_cross_days=2, pct_from_52w_high=1.0, sector_week_positive=True)
    vetoed = apply_hard_vetoes(good, HardVetoInputs(price_below_5=True))
    assert vetoed.verdict == VERDICT_NO_GO


def test_no_veto_is_passthrough():
    good = score_momentum(golden_cross_days=2, pct_from_52w_high=1.0, sector_week_positive=True)
    same = apply_hard_vetoes(good, HardVetoInputs())
    assert same.verdict == good.verdict
    assert same.composite == good.composite


# --------------------------------------------------------------------------- #
# score_equity convenience                                                      #
# --------------------------------------------------------------------------- #
def test_score_equity_picks_stronger_path():
    out = score_equity(
        eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised", reaction_pct=6.0,
        earnings_days_ago=1,
        golden_cross_days=15, pct_from_52w_high=10.0, sector_week_positive=False,
    )
    assert out["chosen_path"] == PATH_CATALYST
    assert out["verdict"] == VERDICT_GO
    assert "catalyst" in out and "momentum" in out


def test_score_equity_momentum_wins_when_catalyst_missing():
    out = score_equity(
        eps_beat_pct=None, rev_beat_pct=None,
        golden_cross_days=1, pct_from_52w_high=0.5, sector_week_positive=True,
    )
    assert out["chosen_path"] == PATH_MOMENTUM
    assert out["verdict"] == VERDICT_GO


def test_score_equity_applies_vetoes_to_both():
    out = score_equity(
        eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised", reaction_pct=6.0,
        earnings_days_ago=1,
        golden_cross_days=1, pct_from_52w_high=0.5, sector_week_positive=True,
        vetoes=HardVetoInputs(spy_down_gt_1_5pct=True),
    )
    assert out["verdict"] == VERDICT_NO_GO


def test_score_equity_to_dict_serializable():
    out = score_equity(
        eps_beat_pct=10.0, rev_beat_pct=4.0, guidance="raised", reaction_pct=6.0,
        earnings_days_ago=1,
    )
    import json
    json.dumps(out)  # must not raise
