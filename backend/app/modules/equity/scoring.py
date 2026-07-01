"""Equity entry scoring — deterministic, explainable, and PURE.

This module converts *already-fetched* structured data into a 0-100 composite
score plus a full component breakdown, so the transparency dashboard can show
**why** a symbol scored the way it did. There are two independent scoring paths,
exactly as specified:

  1. Catalyst weighted score  — an earnings-reaction play (EPS/revenue beat,
     guidance, the earnings-day price reaction, and an exhaustion correction),
     gated on the earnings being *recent*.
  2. Momentum path            — a completely separate trend play (fresh golden
     cross + price near its 52-week high + a green sector week).

A symbol can qualify via either path; `score_equity()` runs both and reports the
stronger one (plus both breakdowns) for the dashboard.

Design rules (do not break these):
  * PURE FUNCTIONS ONLY. No DB, no httpx/requests, no Claude, no SQLAlchemy, no
    pandas. Input is plain numbers/strings; output is a dataclass of plain data.
    This is what makes the whole thing unit-testable and auditable.
  * CONSERVATIVE BY DEFAULT. If the *critical* inputs (EPS beat % and revenue
    beat %) cannot be obtained, we DO NOT guess — we return an explicit no-go and
    flag the missing data. "Default NO" beats "make something up".
  * EXPLAINABLE. Every component reports its own points / max / reason so a human
    can reconstruct the composite by hand. The band thresholds live in named
    constants below rather than as magic numbers.

The SPEC gives the *bands* (e.g. "EPS >=5% full, +2-5% partial, <2% zero";
"reaction +1~12% positive, peak +3-8%, >12% decays") but not exact weights. The
weights chosen here sum to 100 for the catalyst path and 100 for the momentum
path; they are documented inline and are the single source of truth. Where the
SPEC was ambiguous the choice is stated as an ASSUMPTION comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Verdict bands (shared by both paths) — SPEC: >=65 go, 50-64 watch, <50 no-go. #
# --------------------------------------------------------------------------- #
GO_THRESHOLD = 65.0
WATCH_THRESHOLD = 50.0

VERDICT_GO = "go"
VERDICT_WATCH = "watch"
VERDICT_NO_GO = "no-go"

# Path identifiers (for the dashboard / Decision record).
PATH_CATALYST = "catalyst"
PATH_MOMENTUM = "momentum"

# --------------------------------------------------------------------------- #
# Catalyst path weights (sum = 100). These are the single source of truth for  #
# how much each earnings signal contributes to the composite.                  #
# --------------------------------------------------------------------------- #
W_EPS = 30.0        # EPS beat is the primary catalyst signal.
W_REVENUE = 20.0    # Revenue beat confirms the beat is real (not one-off).
W_GUIDANCE = 25.0   # Forward guidance — can go strongly NEGATIVE on a cut.
W_REACTION = 25.0   # The market's own earnings-day reaction.

# Exhaustion correction (未透支): a big pre-earnings run-up means the move may be
# priced in / exhausted. Applied as a subtractive penalty, capped.
EXHAUSTION_RUNUP_THRESHOLD = 15.0   # ran up >15% in the 5 days before earnings
EXHAUSTION_MAX_PENALTY = 15.0       # cap the penalty so it corrects, not dominates

# Recency gate (近因 gate): a catalyst is only actionable if the earnings just
# happened. We only *downgrade* when we KNOW it is stale; unknown -> flag, don't
# fabricate a veto (keeps a genuinely-fresh score from being crushed by a missing
# calendar field).
RECENCY_MAX_TRADING_DAYS = 3

# --------------------------------------------------------------------------- #
# Momentum path weights (sum = 100).                                           #
# --------------------------------------------------------------------------- #
M_GOLDEN_CROSS = 40.0   # freshness of the golden cross
M_NEAR_HIGH = 35.0      # proximity to the 52-week high
M_SECTOR = 25.0         # sector green on the week

MOMENTUM_GOLDEN_CROSS_MAX_DAYS = 10   # SPEC: golden cross <=10 days
MOMENTUM_NEAR_HIGH_MAX_PCT = 3.0      # SPEC: within 3% of the 52-week high


# --------------------------------------------------------------------------- #
# Result containers                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Component:
    """One line of the score breakdown, for the dashboard.

    ``points`` may be negative (e.g. a guidance cut). ``max_points`` is the most
    a *positive* contribution can add, so the UI can render a ratio bar.
    """

    name: str
    points: float
    max_points: float
    detail: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "points": round(self.points, 2),
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass
class ScoreResult:
    """A path's full, explainable scoring output."""

    path: str
    composite: float            # 0-100, clamped
    verdict: str                # go / watch / no-go
    components: list[Component] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)          # why this verdict
    data_completeness: dict = field(default_factory=dict)     # field -> present?
    scorable: bool = True       # False => critical data missing, forced no-go

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "composite": round(self.composite, 1),
            "verdict": self.verdict,
            "scorable": self.scorable,
            "components": [c.to_dict() for c in self.components],
            "reasons": list(self.reasons),
            "data_completeness": dict(self.data_completeness),
        }


# --------------------------------------------------------------------------- #
# Small pure helpers                                                           #
# --------------------------------------------------------------------------- #
def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation of x in [x0,x1] onto [y0,y1] (assumes x0 < x1)."""
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def verdict_for(composite: float) -> str:
    """Map a 0-100 composite onto the SPEC entry bands."""
    if composite >= GO_THRESHOLD:
        return VERDICT_GO
    if composite >= WATCH_THRESHOLD:
        return VERDICT_WATCH
    return VERDICT_NO_GO


# --------------------------------------------------------------------------- #
# Catalyst component scorers (each returns fraction 0..1 of its weight, except  #
# guidance which can be negative). Kept tiny + independently testable.          #
# --------------------------------------------------------------------------- #
def _eps_fraction(eps_beat_pct: float) -> tuple[float, str]:
    """EPS beat: >=5% full · +2-5% partial · <2% / miss zero."""
    if eps_beat_pct >= 5.0:
        return 1.0, f"EPS beat {eps_beat_pct:+.1f}% >= 5% -> full"
    if eps_beat_pct >= 2.0:
        # Partial band 2%->5% maps to 40%->100% of the weight (meaningful, not ~0).
        frac = _lerp(eps_beat_pct, 2.0, 5.0, 0.40, 1.00)
        return frac, f"EPS beat {eps_beat_pct:+.1f}% in +2-5% partial band"
    return 0.0, f"EPS beat {eps_beat_pct:+.1f}% < 2% / miss -> zero"


def _revenue_fraction(rev_beat_pct: float) -> tuple[float, str]:
    """Revenue beat: >=3% full · +1-3% partial · <1% zero."""
    if rev_beat_pct >= 3.0:
        return 1.0, f"Revenue beat {rev_beat_pct:+.1f}% >= 3% -> full"
    if rev_beat_pct >= 1.0:
        frac = _lerp(rev_beat_pct, 1.0, 3.0, 0.40, 1.00)
        return frac, f"Revenue beat {rev_beat_pct:+.1f}% in +1-3% partial band"
    return 0.0, f"Revenue beat {rev_beat_pct:+.1f}% < 1% -> zero"


def _guidance_fraction(guidance: Optional[str]) -> tuple[float, str]:
    """Guidance: raised full · maintained partial (0.5) · cut STRONG NEGATIVE.

    Missing guidance is neutral (0) and flagged in data_completeness — it is not
    one of the *critical* fields, so it must not by itself force a no-go.
    """
    if guidance is None:
        return 0.0, "Guidance missing -> neutral (0)"
    g = guidance.strip().lower()
    if g == "raised":
        return 1.0, "Guidance raised -> full"
    if g == "maintained":
        return 0.5, "Guidance maintained -> partial (0.5)"
    if g == "cut":
        # ASSUMPTION: 强负分 == the full weight as a negative (pulls composite down).
        return -1.0, "Guidance cut -> strong negative (-1.0 weight)"
    return 0.0, f"Guidance '{guidance}' unrecognized -> neutral (0)"


def _reaction_fraction(reaction_pct: float) -> tuple[float, str]:
    """Earnings-day reaction curve.

    SPEC: +1~12% gives positive, PEAK +3-8%, >12% decays (chasing), <0 gives
    nothing. Realized as a continuous piecewise curve:

        r <= 0            -> 0                      (no chasing red)
        0 < r < 1         -> 0.0 .. 0.4  (linear)   (weak, sub-band)
        1 <= r < 3        -> 0.4 .. 1.0  (linear)   (ramp into peak)
        3 <= r <= 8       -> 1.0         (PEAK plateau, full)
        8 < r <= 12       -> 1.0 .. 0.3  (linear)   (fading but still positive)
        r > 12            -> 0.3 decaying toward 0  (追高 penalty)
    """
    r = reaction_pct
    if r <= 0:
        return 0.0, f"Reaction {r:+.1f}% <= 0 -> no credit"
    if r < 1:
        return _lerp(r, 0.0, 1.0, 0.0, 0.40), f"Reaction {r:+.1f}% weak (<1%)"
    if r < 3:
        return _lerp(r, 1.0, 3.0, 0.40, 1.00), f"Reaction {r:+.1f}% ramping to peak"
    if r <= 8:
        return 1.0, f"Reaction {r:+.1f}% in +3-8% peak band -> full"
    if r <= 12:
        return _lerp(r, 8.0, 12.0, 1.00, 0.30), f"Reaction {r:+.1f}% fading (8-12%)"
    # >12% : chasing. Continue decaying from 0.3, floor at 0.
    frac = max(0.0, 0.30 - 0.05 * (r - 12.0))
    return frac, f"Reaction {r:+.1f}% > 12% -> chasing, decayed"


def _exhaustion_penalty(ran_up_pre_pct: Optional[float]) -> tuple[float, str]:
    """未透支 correction: pre-earnings run-up > 15% => subtractive penalty (<=0)."""
    if ran_up_pre_pct is None:
        return 0.0, "Pre-earnings run-up unknown -> no correction"
    if ran_up_pre_pct <= EXHAUSTION_RUNUP_THRESHOLD:
        return 0.0, f"Pre-earnings run-up {ran_up_pre_pct:+.1f}% <= 15% -> not exhausted"
    over = ran_up_pre_pct - EXHAUSTION_RUNUP_THRESHOLD
    penalty = -min(EXHAUSTION_MAX_PENALTY, over)
    return penalty, (
        f"Pre-earnings run-up {ran_up_pre_pct:+.1f}% > 15% "
        f"-> exhaustion penalty {penalty:.1f}"
    )


# --------------------------------------------------------------------------- #
# Public scoring functions                                                     #
# --------------------------------------------------------------------------- #
def score_catalyst(
    *,
    eps_beat_pct: Optional[float],
    rev_beat_pct: Optional[float],
    guidance: Optional[str] = None,
    reaction_pct: Optional[float] = None,
    ran_up_pre_pct: Optional[float] = None,
    earnings_days_ago: Optional[int] = None,
) -> ScoreResult:
    """Catalyst weighted score -> 0-100 composite + breakdown + verdict.

    Critical inputs are ``eps_beat_pct`` and ``rev_beat_pct``. If EITHER is None
    we cannot responsibly score the catalyst, so we return an explicit no-go with
    ``scorable=False`` and a data_completeness map — we never guess a number.

    The recency gate only DOWNGRADES to no-go when the earnings are known to be
    stale (``earnings_days_ago`` > 3). If recency is unknown (None) it is flagged
    but does not veto — otherwise a fresh, high-quality score would be crushed by
    a merely-missing calendar field.
    """
    completeness = {
        "eps_beat_pct": eps_beat_pct is not None,
        "rev_beat_pct": rev_beat_pct is not None,
        "guidance": guidance is not None,
        "reaction_pct": reaction_pct is not None,
        "ran_up_pre_pct": ran_up_pre_pct is not None,
        "earnings_days_ago": earnings_days_ago is not None,
    }

    # --- Critical-data gate: EPS + revenue must both be present. -------------
    if eps_beat_pct is None or rev_beat_pct is None:
        missing = [k for k in ("eps_beat_pct", "rev_beat_pct") if not completeness[k]]
        return ScoreResult(
            path=PATH_CATALYST,
            composite=0.0,
            verdict=VERDICT_NO_GO,
            components=[],
            reasons=[
                "Critical earnings data missing (" + ", ".join(missing) + ")"
                " -> auto no-go (default NO, no guessing).",
            ],
            data_completeness=completeness,
            scorable=False,
        )

    components: list[Component] = []

    eps_frac, eps_detail = _eps_fraction(eps_beat_pct)
    components.append(Component("eps_beat", eps_frac * W_EPS, W_EPS, eps_detail))

    rev_frac, rev_detail = _revenue_fraction(rev_beat_pct)
    components.append(Component("revenue_beat", rev_frac * W_REVENUE, W_REVENUE, rev_detail))

    g_frac, g_detail = _guidance_fraction(guidance)
    components.append(Component("guidance", g_frac * W_GUIDANCE, W_GUIDANCE, g_detail))

    # Reaction: missing -> 0 contribution (non-critical), flagged in completeness.
    if reaction_pct is None:
        components.append(Component("reaction", 0.0, W_REACTION, "Reaction missing -> 0"))
    else:
        rx_frac, rx_detail = _reaction_fraction(reaction_pct)
        components.append(Component("reaction", rx_frac * W_REACTION, W_REACTION, rx_detail))

    pen, pen_detail = _exhaustion_penalty(ran_up_pre_pct)
    components.append(Component("exhaustion", pen, 0.0, pen_detail))

    raw = sum(c.points for c in components)
    composite = _clamp(raw)
    verdict = verdict_for(composite)

    reasons: list[str] = []

    # --- Recency gate (只在已知过期时否决) --------------------------------------
    if earnings_days_ago is not None:
        if earnings_days_ago < 0:
            reasons.append(
                f"earnings_days_ago={earnings_days_ago} is negative (future?) "
                "-> treated as not-actionable, no-go."
            )
            verdict = VERDICT_NO_GO
        elif earnings_days_ago > RECENCY_MAX_TRADING_DAYS:
            reasons.append(
                f"Earnings were {earnings_days_ago} trading days ago (> "
                f"{RECENCY_MAX_TRADING_DAYS}) -> catalyst stale / priced in, "
                "recency gate fails -> no-go."
            )
            verdict = VERDICT_NO_GO
        else:
            reasons.append(
                f"Earnings {earnings_days_ago} trading day(s) ago -> recency gate ok."
            )
    else:
        reasons.append(
            "earnings_days_ago unknown -> recency gate NOT confirmed (flagged, "
            "not vetoed)."
        )

    reasons.append(f"Composite {composite:.1f} -> band verdict '{verdict_for(composite)}'.")

    return ScoreResult(
        path=PATH_CATALYST,
        composite=composite,
        verdict=verdict,
        components=components,
        reasons=reasons,
        data_completeness=completeness,
        scorable=True,
    )


def score_momentum(
    *,
    golden_cross_days: Optional[int],
    pct_from_52w_high: Optional[float],
    sector_week_positive: Optional[bool],
) -> ScoreResult:
    """Momentum path -> its own independent 0-100 composite + breakdown + verdict.

    SPEC qualifier: golden cross <=10 days AND within 3% of the 52-week high AND
    the sector is green on the week. We express each as a weighted, decaying
    component (fresher / closer / green = more), and report a ``momentum_qualified``
    reason line when all three gates pass.

    Momentum has no single "critical" field like EPS/revenue, but a missing value
    is scored conservatively (0 contribution / False sector) and flagged, so an
    under-informed symbol naturally lands below the entry band rather than being
    optimistically scored.
    """
    completeness = {
        "golden_cross_days": golden_cross_days is not None,
        "pct_from_52w_high": pct_from_52w_high is not None,
        "sector_week_positive": sector_week_positive is not None,
    }

    components: list[Component] = []

    # --- Golden-cross freshness (<=3d full, 3-10d fading, >10d disqualified) ---
    if golden_cross_days is None:
        gc_pts, gc_detail = 0.0, "Golden-cross age unknown -> 0"
    elif golden_cross_days < 0:
        gc_pts, gc_detail = 0.0, f"golden_cross_days={golden_cross_days} invalid -> 0"
    elif golden_cross_days <= 3:
        gc_pts, gc_detail = M_GOLDEN_CROSS, f"Golden cross {golden_cross_days}d ago -> fresh, full"
    elif golden_cross_days <= MOMENTUM_GOLDEN_CROSS_MAX_DAYS:
        frac = _lerp(golden_cross_days, 3.0, 10.0, 1.0, 0.5)
        gc_pts = frac * M_GOLDEN_CROSS
        gc_detail = f"Golden cross {golden_cross_days}d ago -> within 10d, fading"
    else:
        gc_pts, gc_detail = 0.0, f"Golden cross {golden_cross_days}d ago > 10d -> stale, 0"
    components.append(Component("golden_cross", gc_pts, M_GOLDEN_CROSS, gc_detail))

    # --- Proximity to 52-week high (<=1% full, 1-3% fading, >3% out) ----------
    if pct_from_52w_high is None:
        nh_pts, nh_detail = 0.0, "Distance from 52w high unknown -> 0"
    else:
        d = abs(pct_from_52w_high)  # distance below the high, as a magnitude
        if d <= 1.0:
            nh_pts, nh_detail = M_NEAR_HIGH, f"{d:.1f}% from 52w high -> at highs, full"
        elif d <= MOMENTUM_NEAR_HIGH_MAX_PCT:
            frac = _lerp(d, 1.0, 3.0, 1.0, 0.5)
            nh_pts = frac * M_NEAR_HIGH
            nh_detail = f"{d:.1f}% from 52w high -> within 3%, fading"
        else:
            nh_pts, nh_detail = 0.0, f"{d:.1f}% from 52w high > 3% -> too far, 0"
    components.append(Component("near_52w_high", nh_pts, M_NEAR_HIGH, nh_detail))

    # --- Sector green on the week --------------------------------------------
    if sector_week_positive is None:
        sec_pts, sec_detail = 0.0, "Sector week unknown -> treated negative, 0"
    elif sector_week_positive:
        sec_pts, sec_detail = M_SECTOR, "Sector green on the week -> full"
    else:
        sec_pts, sec_detail = 0.0, "Sector red on the week -> 0"
    components.append(Component("sector_week", sec_pts, M_SECTOR, sec_detail))

    composite = _clamp(sum(c.points for c in components))
    verdict = verdict_for(composite)

    qualified = (
        golden_cross_days is not None and 0 <= golden_cross_days <= MOMENTUM_GOLDEN_CROSS_MAX_DAYS
        and pct_from_52w_high is not None and abs(pct_from_52w_high) <= MOMENTUM_NEAR_HIGH_MAX_PCT
        and bool(sector_week_positive)
    )
    reasons = [
        ("All three momentum gates pass (fresh cross + near high + green sector)."
         if qualified else
         "Not all momentum gates pass -> weaker momentum setup."),
        f"Composite {composite:.1f} -> band verdict '{verdict}'.",
    ]

    return ScoreResult(
        path=PATH_MOMENTUM,
        composite=composite,
        verdict=verdict,
        components=components,
        reasons=reasons,
        data_completeness=completeness,
        scorable=True,
    )


# --------------------------------------------------------------------------- #
# Hard vetoes — SPEC: these OVERRIDE the score to no-go regardless of composite.#
# Pure: caller passes the already-evaluated market/instrument flags.            #
# --------------------------------------------------------------------------- #
@dataclass
class HardVetoInputs:
    """Market / instrument conditions that override any positive score.

    All default to the safe/false value so an unspecified condition never *adds*
    a veto by surprise — a veto only fires when the caller explicitly sets it.
    """

    macro_event_today: bool = False          # Fed / CPI / NFP same day
    earnings_within_5_days: bool = False      # post-entry earnings inside 5 trading days
    spy_down_gt_1_5pct: bool = False          # SPY down > 1.5% on the day
    is_option: bool = False                   # options banned
    is_crypto: bool = False                   # crypto banned (this is the equity path)
    price_below_5: bool = False               # penny stock < $5 banned


def apply_hard_vetoes(result: ScoreResult, vetoes: HardVetoInputs) -> ScoreResult:
    """Return a copy of ``result`` forced to no-go if any hard veto fires.

    The composite number is preserved for transparency (so the dashboard still
    shows what the setup scored) — only the verdict is overridden, with the firing
    veto(s) recorded in ``reasons``. This keeps the audit trail intact.
    """
    fired: list[str] = []
    if vetoes.macro_event_today:
        fired.append("macro event today (Fed/CPI/NFP)")
    if vetoes.earnings_within_5_days:
        fired.append("earnings within 5 trading days of entry")
    if vetoes.spy_down_gt_1_5pct:
        fired.append("SPY down > 1.5% on the day")
    if vetoes.is_option:
        fired.append("options are banned")
    if vetoes.is_crypto:
        fired.append("crypto is banned on the equity path")
    if vetoes.price_below_5:
        fired.append("penny stock (< $5) banned")

    if not fired:
        return result

    return ScoreResult(
        path=result.path,
        composite=result.composite,
        verdict=VERDICT_NO_GO,
        components=result.components,
        reasons=result.reasons + ["HARD VETO -> no-go: " + "; ".join(fired) + "."],
        data_completeness=result.data_completeness,
        scorable=result.scorable,
    )


# --------------------------------------------------------------------------- #
# Convenience: run both paths and report the stronger one (for the dashboard). #
# --------------------------------------------------------------------------- #
def score_equity(
    *,
    # catalyst inputs
    eps_beat_pct: Optional[float] = None,
    rev_beat_pct: Optional[float] = None,
    guidance: Optional[str] = None,
    reaction_pct: Optional[float] = None,
    ran_up_pre_pct: Optional[float] = None,
    earnings_days_ago: Optional[int] = None,
    # momentum inputs
    golden_cross_days: Optional[int] = None,
    pct_from_52w_high: Optional[float] = None,
    sector_week_positive: Optional[bool] = None,
    # optional hard vetoes
    vetoes: Optional[HardVetoInputs] = None,
) -> dict:
    """Run BOTH paths, apply optional vetoes, and pick the stronger verdict.

    Returns a dashboard-ready dict: the chosen path/composite/verdict plus BOTH
    full breakdowns, so the transparency feed can show the winning thesis and the
    runner-up side by side. This is a thin, still-pure convenience wrapper — the
    two scoring functions remain the primitives to unit-test.
    """
    catalyst = score_catalyst(
        eps_beat_pct=eps_beat_pct,
        rev_beat_pct=rev_beat_pct,
        guidance=guidance,
        reaction_pct=reaction_pct,
        ran_up_pre_pct=ran_up_pre_pct,
        earnings_days_ago=earnings_days_ago,
    )
    momentum = score_momentum(
        golden_cross_days=golden_cross_days,
        pct_from_52w_high=pct_from_52w_high,
        sector_week_positive=sector_week_positive,
    )

    if vetoes is not None:
        catalyst = apply_hard_vetoes(catalyst, vetoes)
        momentum = apply_hard_vetoes(momentum, vetoes)

    # Verdict rank for choosing the stronger path.
    rank = {VERDICT_GO: 2, VERDICT_WATCH: 1, VERDICT_NO_GO: 0}

    def key(r: ScoreResult) -> tuple[int, float]:
        return (rank[r.verdict], r.composite)

    winner = catalyst if key(catalyst) >= key(momentum) else momentum

    return {
        "chosen_path": winner.path,
        "composite": round(winner.composite, 1),
        "verdict": winner.verdict,
        "scorable": winner.scorable,
        "catalyst": catalyst.to_dict(),
        "momentum": momentum.to_dict(),
    }
