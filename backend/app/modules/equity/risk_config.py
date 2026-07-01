"""Equity-specific risk limits + exit rules (EQUITY-universe-risk).

A dedicated, self-contained risk config for the US-equity catalyst strategy,
kept **entirely separate** from the crypto ``RiskLimit`` DB model
(``app.modules.risk.models``): this is a pure, in-code config (a frozen
dataclass + pure evaluators), so it changes nothing about how crypto is sized
or gated. The SPEC numbers live here as the single source of truth.

SPEC (per-strategy, equities):
  * position size   : <= 5% of account equity per name
  * daily loss      : stop the day at -2% of account equity
  * weekly trades   : <= 3 new entries per week (CAT had no "per-week" concept —
                      this module adds the count/gate; callers pass the tally)
  * concurrency     : hold 3-5 names at once (5 is a hard cap; 3 is a soft
                      diversification floor, informational — never blocks an entry)
  * exit rules      : hard stop -7% · 10% trailing stop from the peak · time stop
                      at 15 trading days · exit if earnings are within 5 trading
                      days · trim HALF at +25%
  * hard vetoes     : macro day / earnings within 5 trading days of entry / SPY
                      down > 1.5% today / options / penny (< $5) / crypto — these
                      OVERRIDE any positive signal (a pre-entry GATE). The veto set
                      itself lives in ``scoring.HardVetoInputs`` and is reused here
                      via ``entry_allowed`` so there is ONE list of vetoes.

Everything is pure and unit-testable: inputs are plain numbers, output is a
plain dataclass. No DB, no network.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from app.modules.equity.scoring import HardVetoInputs, evaluate_hard_vetoes

# --------------------------------------------------------------------------- #
# SPEC constants (single source of truth)                                      #
# --------------------------------------------------------------------------- #
EQUITY_MAX_POSITION_SIZE_PCT = 5.0     # per-name cap, % of account equity
EQUITY_DAILY_LOSS_LIMIT_PCT = 2.0      # stop trading for the day at -2% equity
EQUITY_MAX_TRADES_PER_WEEK = 3         # weekly new-entry cap (added concept)
EQUITY_MIN_OPEN_POSITIONS = 3          # soft diversification floor (informational)
EQUITY_MAX_OPEN_POSITIONS = 5          # hard concurrency cap

# Exit rules
EQUITY_STOP_LOSS_PCT = -7.0            # hard stop: P&L <= -7%
EQUITY_TRAILING_STOP_PCT = 10.0        # trailing stop: 10% drawdown off the peak
EQUITY_MAX_HOLD_TRADING_DAYS = 15      # time stop: 15 trading days
EQUITY_EARNINGS_BLACKOUT_DAYS = 5      # exit if next earnings within 5 trading days
EQUITY_TAKE_PROFIT_TRIM_PCT = 25.0     # at +25%, take partial profit
EQUITY_TAKE_PROFIT_TRIM_FRACTION = 0.5  # ... by trimming HALF the position

# Exit action labels
EXIT_HOLD = "hold"
EXIT_FULL = "exit"
EXIT_TRIM = "trim"


@dataclass(frozen=True)
class EquityRiskLimit:
    """Immutable equity risk config. Defaults ARE the SPEC; override for tests."""

    max_position_size_pct: float = EQUITY_MAX_POSITION_SIZE_PCT
    daily_loss_limit_pct: float = EQUITY_DAILY_LOSS_LIMIT_PCT
    max_trades_per_week: int = EQUITY_MAX_TRADES_PER_WEEK
    min_open_positions: int = EQUITY_MIN_OPEN_POSITIONS
    max_open_positions: int = EQUITY_MAX_OPEN_POSITIONS
    stop_loss_pct: float = EQUITY_STOP_LOSS_PCT
    trailing_stop_pct: float = EQUITY_TRAILING_STOP_PCT
    max_hold_trading_days: int = EQUITY_MAX_HOLD_TRADING_DAYS
    earnings_blackout_days: int = EQUITY_EARNINGS_BLACKOUT_DAYS
    take_profit_trim_pct: float = EQUITY_TAKE_PROFIT_TRIM_PCT
    take_profit_trim_fraction: float = EQUITY_TAKE_PROFIT_TRIM_FRACTION

    def position_size_cap(self, account_equity: float) -> float:
        """Max dollars for a single name = account_equity * 5%."""
        return account_equity * self.max_position_size_pct / 100.0

    def daily_loss_cap(self, account_equity: float) -> float:
        """Absolute dollar loss that halts the day = account_equity * 2%."""
        return account_equity * self.daily_loss_limit_pct / 100.0

    def to_dict(self) -> dict:
        return asdict(self)


# The canonical instance callers should use.
DEFAULT_EQUITY_RISK_LIMIT = EquityRiskLimit()


# --------------------------------------------------------------------------- #
# Pre-entry gates                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class GateResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def entry_allowed(vetoes: HardVetoInputs) -> GateResult:
    """Hard-veto GATE for a NEW entry (macro day / near earnings / SPY crash /
    options / penny / crypto). Reuses the one veto set in ``scoring`` so a change
    there is reflected here automatically. Any fired veto blocks the entry."""
    fired = evaluate_hard_vetoes(vetoes)
    if fired:
        return GateResult(False, ["HARD VETO -> entry blocked: " + "; ".join(fired)])
    return GateResult(True, ["No hard veto fired -> entry gate clear."])


def can_open_new_position(
    *,
    open_positions: int,
    trades_this_week: int,
    limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> GateResult:
    """Concurrency + weekly-turnover gate for a NEW entry.

    Blocks when already at the concurrency cap (>= 5 open) OR the weekly entry
    budget is spent (>= 3 this week). The 3-position minimum is a *diversification
    target*, not a blocker — it is surfaced as an informational note only.
    """
    reasons: list[str] = []

    if open_positions >= limit.max_open_positions:
        return GateResult(
            False,
            [f"Already {open_positions} open (cap {limit.max_open_positions}) -> no new entry."],
        )
    if trades_this_week >= limit.max_trades_per_week:
        return GateResult(
            False,
            [f"Weekly entries {trades_this_week} >= {limit.max_trades_per_week} -> weekly cap reached."],
        )

    if open_positions < limit.min_open_positions:
        reasons.append(
            f"Below the {limit.min_open_positions}-name diversification floor "
            f"({open_positions} open) -> more entries encouraged."
        )
    reasons.append(
        f"Entry allowed: {open_positions}/{limit.max_open_positions} open, "
        f"{trades_this_week}/{limit.max_trades_per_week} weekly entries used."
    )
    return GateResult(True, reasons)


def daily_loss_breached(
    *,
    day_pnl: float,
    account_equity: float,
    limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> GateResult:
    """True (blocked) when the day's realized P&L has hit the -2% equity stop."""
    cap = limit.daily_loss_cap(account_equity)
    if day_pnl <= -cap:
        return GateResult(
            False,
            [f"Daily loss {day_pnl:,.2f} <= -{cap:,.2f} ({limit.daily_loss_limit_pct}% of equity) -> stop for the day."],
        )
    return GateResult(True, [f"Daily P&L {day_pnl:,.2f} within -{cap:,.2f} stop."])


# --------------------------------------------------------------------------- #
# Exit rules                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class ExitEvaluation:
    """What to do with an OPEN position now. ``action`` is one of hold/exit/trim;
    a full exit takes precedence over a trim (risk-off before profit-taking)."""

    action: str
    triggers: list[str] = field(default_factory=list)
    trim_fraction: Optional[float] = None

    @property
    def should_exit(self) -> bool:
        return self.action == EXIT_FULL

    @property
    def should_trim(self) -> bool:
        return self.action == EXIT_TRIM

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "triggers": list(self.triggers),
            "trim_fraction": self.trim_fraction,
        }


def evaluate_exit(
    *,
    pnl_pct: Optional[float] = None,
    drawdown_from_peak_pct: Optional[float] = None,
    trading_days_held: Optional[int] = None,
    trading_days_to_earnings: Optional[int] = None,
    limit: EquityRiskLimit = DEFAULT_EQUITY_RISK_LIMIT,
) -> ExitEvaluation:
    """Apply the SPEC exit rules to one open position.

    Inputs (all optional; a missing input simply can't trigger its own rule):
      * ``pnl_pct``                   current unrealized P&L, percent (−7 => -7%).
      * ``drawdown_from_peak_pct``    how far below the peak-since-entry, as a
                                      POSITIVE percent (10 => 10% off the high).
      * ``trading_days_held``         trading days since entry.
      * ``trading_days_to_earnings``  trading days until the NEXT earnings report.

    Precedence: any risk-off exit (stop / trailing / time / earnings) wins over
    the +25% profit trim.
    """
    exits: list[str] = []

    if pnl_pct is not None and pnl_pct <= limit.stop_loss_pct:
        exits.append(f"hard stop: P&L {pnl_pct:+.1f}% <= {limit.stop_loss_pct:.0f}%")

    if drawdown_from_peak_pct is not None and drawdown_from_peak_pct >= limit.trailing_stop_pct:
        exits.append(
            f"trailing stop: {drawdown_from_peak_pct:.1f}% off peak >= {limit.trailing_stop_pct:.0f}%"
        )

    if trading_days_held is not None and trading_days_held >= limit.max_hold_trading_days:
        exits.append(
            f"time stop: held {trading_days_held} trading days >= {limit.max_hold_trading_days}"
        )

    if (
        trading_days_to_earnings is not None
        and 0 <= trading_days_to_earnings < limit.earnings_blackout_days
    ):
        exits.append(
            f"earnings blackout: next report in {trading_days_to_earnings} trading days "
            f"(< {limit.earnings_blackout_days}) -> exit before the event"
        )

    if exits:
        return ExitEvaluation(action=EXIT_FULL, triggers=exits, trim_fraction=None)

    if pnl_pct is not None and pnl_pct >= limit.take_profit_trim_pct:
        return ExitEvaluation(
            action=EXIT_TRIM,
            triggers=[
                f"take-profit: P&L {pnl_pct:+.1f}% >= {limit.take_profit_trim_pct:.0f}% "
                f"-> trim {limit.take_profit_trim_fraction:.0%}"
            ],
            trim_fraction=limit.take_profit_trim_fraction,
        )

    return ExitEvaluation(action=EXIT_HOLD, triggers=[], trim_fraction=None)
