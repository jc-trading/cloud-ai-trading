"""Tests for the equity-specific risk config, gates, and exit rules.

All pure — no DB, no network. Asserts acceptance criteria (2) + (3):

  * the SPEC numbers (5% size / -2% daily / weekly <= 3 / 3-5 concurrency /
    exit rules) are exactly what the config exposes;
  * the weekly-turnover + concurrency gate blocks correctly;
  * the exit evaluator fires each SPEC rule and respects exit>trim precedence;
  * the hard-veto entry GATE (macro / near-earnings / SPY crash / options /
    penny / crypto) blocks a new entry.

Crypto's risk model is untouched (a separate DB model); nothing here imports it.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.equity.risk_config import (
    DEFAULT_EQUITY_RISK_LIMIT,
    EXIT_FULL,
    EXIT_HOLD,
    EXIT_TRIM,
    EquityRiskLimit,
    can_open_new_position,
    daily_loss_breached,
    entry_allowed,
    evaluate_exit,
)
from app.modules.equity.scoring import HardVetoInputs


# --------------------------------------------------------------------------- #
# SPEC numbers                                                                  #
# --------------------------------------------------------------------------- #
def test_spec_constants():
    limit = DEFAULT_EQUITY_RISK_LIMIT
    assert limit.max_position_size_pct == 5.0
    assert limit.daily_loss_limit_pct == 2.0
    assert limit.max_trades_per_week == 3
    assert limit.min_open_positions == 3
    assert limit.max_open_positions == 5
    assert limit.stop_loss_pct == -7.0
    assert limit.trailing_stop_pct == 10.0
    assert limit.max_hold_trading_days == 15
    assert limit.earnings_blackout_days == 5
    assert limit.take_profit_trim_pct == 25.0
    assert limit.take_profit_trim_fraction == 0.5


def test_position_and_daily_caps():
    limit = DEFAULT_EQUITY_RISK_LIMIT
    assert limit.position_size_cap(100_000) == 5_000       # 5%
    assert limit.daily_loss_cap(100_000) == 2_000          # 2%


def test_is_frozen():
    limit = EquityRiskLimit()
    try:
        limit.max_position_size_pct = 10.0  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Concurrency + weekly-turnover gate                                           #
# --------------------------------------------------------------------------- #
def test_entry_allowed_normal():
    g = can_open_new_position(open_positions=2, trades_this_week=1)
    assert g.allowed is True


def test_entry_blocked_at_concurrency_cap():
    g = can_open_new_position(open_positions=5, trades_this_week=0)
    assert g.allowed is False
    assert any("cap" in r for r in g.reasons)


def test_entry_blocked_at_weekly_cap():
    g = can_open_new_position(open_positions=1, trades_this_week=3)
    assert g.allowed is False
    assert any("weekly" in r.lower() for r in g.reasons)


def test_below_min_positions_is_note_not_block():
    g = can_open_new_position(open_positions=1, trades_this_week=0)
    assert g.allowed is True
    assert any("diversification floor" in r for r in g.reasons)


# --------------------------------------------------------------------------- #
# Daily loss stop                                                              #
# --------------------------------------------------------------------------- #
def test_daily_loss_within_limit():
    g = daily_loss_breached(day_pnl=-1000, account_equity=100_000)  # -1%
    assert g.allowed is True


def test_daily_loss_breached_at_2pct():
    g = daily_loss_breached(day_pnl=-2000, account_equity=100_000)  # -2%
    assert g.allowed is False


# --------------------------------------------------------------------------- #
# Hard-veto entry gate                                                         #
# --------------------------------------------------------------------------- #
def test_entry_gate_clear_when_no_veto():
    g = entry_allowed(HardVetoInputs())
    assert g.allowed is True


def test_entry_gate_blocks_macro_day():
    g = entry_allowed(HardVetoInputs(macro_event_today=True))
    assert g.allowed is False
    assert any("macro" in r.lower() for r in g.reasons)


def test_entry_gate_blocks_near_earnings():
    g = entry_allowed(HardVetoInputs(earnings_within_5_days=True))
    assert g.allowed is False


def test_entry_gate_blocks_spy_crash_and_penny_and_options():
    assert entry_allowed(HardVetoInputs(spy_down_gt_1_5pct=True)).allowed is False
    assert entry_allowed(HardVetoInputs(price_below_5=True)).allowed is False
    assert entry_allowed(HardVetoInputs(is_option=True)).allowed is False


# --------------------------------------------------------------------------- #
# Exit rules                                                                   #
# --------------------------------------------------------------------------- #
def test_exit_hold_when_nothing_triggers():
    e = evaluate_exit(pnl_pct=5.0, drawdown_from_peak_pct=2.0, trading_days_held=3,
                      trading_days_to_earnings=20)
    assert e.action == EXIT_HOLD


def test_exit_hard_stop_at_minus_7():
    e = evaluate_exit(pnl_pct=-7.0)
    assert e.action == EXIT_FULL
    assert any("hard stop" in t for t in e.triggers)


def test_exit_trailing_stop_at_10pct_off_peak():
    e = evaluate_exit(pnl_pct=8.0, drawdown_from_peak_pct=10.0)
    assert e.action == EXIT_FULL
    assert any("trailing" in t for t in e.triggers)


def test_exit_time_stop_at_15_days():
    e = evaluate_exit(pnl_pct=3.0, trading_days_held=15)
    assert e.action == EXIT_FULL
    assert any("time stop" in t for t in e.triggers)


def test_exit_earnings_blackout_within_5_days():
    e = evaluate_exit(pnl_pct=3.0, trading_days_to_earnings=4)
    assert e.action == EXIT_FULL
    assert any("earnings" in t for t in e.triggers)


def test_earnings_exactly_5_days_out_does_not_exit():
    e = evaluate_exit(pnl_pct=3.0, trading_days_to_earnings=5)
    assert e.action == EXIT_HOLD


def test_exit_trim_half_at_plus_25():
    e = evaluate_exit(pnl_pct=25.0)
    assert e.action == EXIT_TRIM
    assert e.trim_fraction == 0.5


def test_full_exit_beats_trim_precedence():
    """A +25% winner that is ALSO 15 days old must EXIT (risk-off), not just trim."""
    e = evaluate_exit(pnl_pct=25.0, trading_days_held=15)
    assert e.action == EXIT_FULL


def test_missing_inputs_cannot_trigger_their_rule():
    e = evaluate_exit(pnl_pct=None, drawdown_from_peak_pct=None,
                      trading_days_held=None, trading_days_to_earnings=None)
    assert e.action == EXIT_HOLD


def test_exit_to_dict_serializable():
    import json
    json.dumps(evaluate_exit(pnl_pct=-7.0).to_dict())
