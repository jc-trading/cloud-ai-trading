"""P3c: per-group trade settings — parsing, entry limit and the exit overlay."""

import math
import random
from datetime import date

import pytest

from quant.engine import trade_rules as tr
from quant.engine.exits import ExitParams, Position, evaluate_exit
from quant.engine.signal import Direction

CAL = "0b8a2f7e-5d1c-4c3e-9a77-2f1e6d4b9c01"


def _pos(**kw):
    base = dict(symbol="X", shares=10, avg_cost=100.0, stop=97.0, r_unit=3.0,
                entry_date=date(2020, 1, 1), high_water=100.0)
    base.update(kw)
    return Position(**base)


def _decide(pos, *, low, close, high, open_, settings, direction=Direction.UP,
            expected_move=2.0, params=ExitParams(), bench=None):
    return tr.decide_exit(pos, low, close, signal_direction=direction,
                          expected_move=expected_move, params=params,
                          benchmark_return_since_entry=bench, bar_open=open_,
                          bar_high=high, settings=settings)


def _err(obj, timeframe="1d"):
    with pytest.raises(tr.TradeSettingsError) as e:
        tr.parse_trade_settings(obj, timeframe=timeframe)
    return str(e.value)


# --- parse / normalize ------------------------------------------------------

@pytest.mark.parametrize("obj", [{}, {"holding_style": "swing"}])
def test_empty_and_default_are_none(obj):
    assert tr.parse_trade_settings(obj, timeframe="1d") is None
    assert tr.normalize(obj) == {}


def test_unknown_key_reports_first_alphabetically():
    assert _err({"zeta": 1, "alpha": 2, "take_profit_pct": 0.1}) == \
        "trade_settings_json: unknown key 'alpha'"


@pytest.mark.parametrize("obj", [None, [], "x"])
def test_non_object_rejected(obj):
    assert _err(obj) == "trade_settings_json: must be an object"


def test_timeframe_1m_rejects_settings_but_accepts_empty():
    assert _err({"take_profit_pct": 0.1}, "1m") == \
        "trade_settings_json: only supported on timeframe 1d"
    assert tr.parse_trade_settings({}, timeframe="1m") is None


@pytest.mark.parametrize("obj", [{"entry_prob_min": 0.55}, {"calibration_run_id": CAL}])
def test_prob_and_calibration_must_come_together(obj):
    assert _err(obj) == \
        "trade_settings_json: entry_prob_min and calibration_run_id must be set together"


@pytest.mark.parametrize("v,msg", [
    (0, "must be > 0 and < 1"), (1, "must be > 0 and < 1"),
    (True, "expected a number"), ("0.5", "expected a number")])
def test_entry_prob_min_rejects(v, msg):
    assert _err({"entry_prob_min": v, "calibration_run_id": CAL}) == \
        f"trade_settings_json.entry_prob_min: {msg}"


def test_entry_prob_min_accepts_with_calibration():
    s = tr.parse_trade_settings({"entry_prob_min": 0.55, "calibration_run_id": CAL},
                                timeframe="1d")
    assert s == tr.TradeSettings(entry_prob_min=0.55, calibration_run_id=CAL)


@pytest.mark.parametrize("v", ["not-a-uuid", 123, None])
def test_calibration_run_id_must_be_uuid(v):
    assert _err({"entry_prob_min": 0.55, "calibration_run_id": v}) == \
        "trade_settings_json.calibration_run_id: expected a uuid string"


def test_uppercase_uuid_normalized_to_lowercase():
    out = tr.normalize({"entry_prob_min": 0.55, "calibration_run_id": CAL.upper()})
    assert out == {"entry_prob_min": 0.55, "calibration_run_id": CAL}


@pytest.mark.parametrize("v", [-0.10, 0.03])
def test_entry_limit_bounds_inclusive(v):
    assert tr.normalize({"entry_limit_offset_pct": v}) == {"entry_limit_offset_pct": v}


@pytest.mark.parametrize("v,msg", [
    (-0.1001, "-0.1001 outside [-0.1, 0.03]"), (0.0301, "0.0301 outside [-0.1, 0.03]"),
    (float("nan"), "expected a finite number")])
def test_entry_limit_rejects(v, msg):
    assert _err({"entry_limit_offset_pct": v}) == \
        f"trade_settings_json.entry_limit_offset_pct: {msg}"


@pytest.mark.parametrize("v", [0.01, 1.0])
def test_take_profit_bounds_inclusive(v):
    assert tr.parse_trade_settings({"take_profit_pct": v}, timeframe="1d").take_profit_pct == v


def test_take_profit_int_normalizes_to_float():
    out = tr.normalize({"take_profit_pct": 1})
    assert out == {"take_profit_pct": 1.0}
    assert type(out["take_profit_pct"]) is float


@pytest.mark.parametrize("v", [1, 250])
def test_max_hold_bounds_inclusive(v):
    out = tr.normalize({"max_hold_days": v})
    assert out == {"max_hold_days": v} and type(out["max_hold_days"]) is int


@pytest.mark.parametrize("v,msg", [
    (0, "0 outside [1, 250]"), (251, "251 outside [1, 250]"),
    (True, "expected int"), (3.0, "expected int")])
def test_max_hold_rejects(v, msg):
    assert _err({"max_hold_days": v}) == f"trade_settings_json.max_hold_days: {msg}"


def test_holding_style():
    assert tr.normalize({"holding_style": "long"}) == {"holding_style": "long"}
    assert _err({"holding_style": "scalping"}) == \
        "trade_settings_json.holding_style: scalping requires timeframe 1m (P5)"
    assert _err({"holding_style": "x"}) == \
        "trade_settings_json.holding_style: must be one of swing, long"


# --- entry limit ------------------------------------------------------------

def test_allows_entry_at_open():
    s = tr.TradeSettings(entry_limit_offset_pct=-0.02)
    limit = 100.0 * (1.0 + s.entry_limit_offset_pct)
    assert tr.allows_entry_at_open(500.0, 100.0, None)
    assert tr.allows_entry_at_open(500.0, 100.0, tr.TradeSettings(take_profit_pct=0.1))
    assert tr.allows_entry_at_open(limit, 100.0, s)
    assert not tr.allows_entry_at_open(math.nextafter(limit, math.inf), 100.0, s)
    assert not tr.allows_entry_at_open(1.0, 0.0, s)


# --- decide_exit --------------------------------------------------------------

def test_take_profit_inside_bar_fills_at_target():
    s = tr.TradeSettings(take_profit_pct=0.05)
    d = _decide(_pos(), low=101.0, close=103.0, high=106.0, open_=102.0, settings=s)
    assert d.action == "take_profit" and d.price == pytest.approx(105.0)


def test_take_profit_gap_fills_at_open():
    s = tr.TradeSettings(take_profit_pct=0.05)
    d = _decide(_pos(), low=107.0, close=108.0, high=110.0, open_=109.0, settings=s)
    assert d.action == "take_profit" and d.price == 109.0


def test_stop_and_take_profit_same_bar_is_hard_stop():
    s = tr.TradeSettings(take_profit_pct=0.05)
    d = _decide(_pos(stop=97.0), low=96.0, close=104.0, high=106.0, open_=100.0, settings=s)
    assert d.action == "hard_stop"


def _reversing_pos(**kw):
    pos = _pos(stop=90.0, **kw)
    pos.reversal_count = 3
    return pos


def test_long_ignores_reversal_swing_takes_it():
    kw = dict(low=100.0, close=101.0, high=101.5, open_=100.5,
              direction=Direction.DOWN, params=ExitParams(reversal_bars=3))
    assert _decide(_reversing_pos(), settings=tr.TradeSettings(holding_style="long"),
                   **kw) is None
    assert _decide(_reversing_pos(), settings=tr.TradeSettings(take_profit_pct=0.5),
                   **kw).action == "reversal"


def test_swing_take_profit_beats_reversal():
    d = _decide(_reversing_pos(), low=100.0, close=101.0, high=106.0, open_=100.5,
                direction=Direction.DOWN, settings=tr.TradeSettings(take_profit_pct=0.05))
    assert d.action == "take_profit"


def test_max_hold_boundary_exits_at_close():
    s = tr.TradeSettings(max_hold_days=5)
    pos = _pos(stop=90.0)
    pos.bars_held = 4
    kw = dict(low=100.0, close=101.0, high=101.5, open_=100.5, settings=s)
    assert _decide(pos, **kw) is None
    pos.bars_held = 5
    d = _decide(pos, **kw)
    assert d.action == "max_hold" and d.price == 101.0


def test_swing_reversal_beats_max_hold():
    pos = _reversing_pos()
    pos.bars_held = 10
    d = _decide(pos, low=100.0, close=101.0, high=101.5, open_=100.5,
                direction=Direction.DOWN, settings=tr.TradeSettings(max_hold_days=5))
    assert d.action == "reversal"


def test_no_settings_and_default_settings_equal_evaluate_exit():
    rng = random.Random(20261019)
    dirs = list(Direction)
    for _ in range(200):
        avg = rng.uniform(20, 200)
        pos = _pos(avg_cost=avg, stop=avg * rng.uniform(0.85, 1.1), r_unit=avg * 0.03,
                   high_water=avg * rng.uniform(1.0, 1.3))
        pos.reversal_count = rng.randint(0, 4)
        pos.bars_held = rng.randint(0, 40)
        open_ = avg * rng.uniform(0.85, 1.2)
        low = open_ * rng.uniform(0.9, 1.0)
        high = open_ * rng.uniform(1.0, 1.1)
        close = rng.uniform(low, high)
        args = dict(signal_direction=rng.choice(dirs), expected_move=rng.uniform(0, 10),
                    params=ExitParams(stagnation_bars=rng.randint(5, 35)),
                    benchmark_return_since_entry=rng.choice([None, rng.uniform(-0.1, 0.1)]),
                    bar_open=open_)
        want = evaluate_exit(pos, low, close, **args)
        for s in (None, tr.TradeSettings()):
            assert tr.decide_exit(pos, low, close, bar_high=high, settings=s, **args) == want


# --- confidence floor / registry --------------------------------------------

def test_effective_min_confidence():
    prob = tr.TradeSettings(entry_prob_min=0.55, calibration_run_id=CAL)
    assert tr.effective_min_confidence(65.0, None, 80.0) == 65.0
    assert tr.effective_min_confidence(65.0, tr.TradeSettings(take_profit_pct=0.1), 80.0) == 65.0
    assert tr.effective_min_confidence(65.0, prob, 80.0) == 80.0
    assert tr.effective_min_confidence(65.0, prob, 50.0) == 65.0
    assert tr.effective_min_confidence(65.0, prob, None) == math.inf


def test_registry_entry_keys_and_fresh_copy():
    r = tr.registry_entry()
    assert r["available"] is True
    assert set(r["keys"]) == set(tr.KEYS) and len(r["keys"]) == 6
    r["keys"].pop("take_profit_pct")
    r["holding_styles_1d"].append("scalping")
    again = tr.registry_entry()
    assert "take_profit_pct" in again["keys"]
    assert again["holding_styles_1d"] == ["swing", "long"]


def test_take_profit_fires_when_the_high_equals_the_target_exactly():
    s = tr.TradeSettings(take_profit_pct=0.25)
    pos = _pos(avg_cost=100.0)
    target = pos.avg_cost * (1.0 + s.take_profit_pct)
    assert target == 125.0
    d = _decide(pos, low=105.0, close=120.0, high=target, open_=110.0, settings=s)
    assert d.action == "take_profit" and d.price == target
