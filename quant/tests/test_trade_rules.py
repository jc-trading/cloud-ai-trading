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
    assert tr.parse_trade_settings({"take_profit_pct": 0.1}, timeframe="1m") == \
        tr.IntradayTradeSettings(take_profit_pct=0.1)
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


# --- P5a: 1m trade settings -------------------------------------------------

INT_1M = {"entry_ttl_minutes": (1, 30), "atr_period": (5, 120), "max_hold_minutes": (1, 390),
          "flatten_minutes_before_close": (1, 60), "max_entries_per_symbol_day": (1, 10)}
FLOAT_1M = {"entry_limit_offset_pct": (-0.05, 0.01), "stop_pct": (0.001, 0.10),
            "stop_atr_mult": (0.25, 10), "take_profit_pct": (0.001, 0.20),
            "take_profit_atr_mult": (0.25, 20)}


def _parse_1m(obj):
    return tr.parse_trade_settings(obj, timeframe="1m")


@pytest.mark.parametrize("k", sorted(FLOAT_1M))
def test_1m_float_bounds_are_inclusive(k):
    lo, hi = FLOAT_1M[k]
    for v in (lo, hi):
        assert getattr(_parse_1m({k: v}), k) == v
    for v in (lo - 1e-6, hi + 1e-6):
        assert _err({k: v}, "1m") == f"trade_settings_json.{k}: {v} outside [{lo}, {hi}]"
    assert _err({k: "1"}, "1m") == f"trade_settings_json.{k}: expected a number"
    assert _err({k: True}, "1m") == f"trade_settings_json.{k}: expected a number"


@pytest.mark.parametrize("k", sorted(INT_1M))
def test_1m_int_bounds_and_type(k):
    lo, hi = INT_1M[k]
    assert getattr(_parse_1m({k: hi}), k) == hi
    low = _parse_1m({k: lo})
    assert low is None or getattr(low, k) == lo
    for v in (lo - 1, hi + 1):
        assert _err({k: v}, "1m") == f"trade_settings_json.{k}: {v} outside [{lo}, {hi}]"
    for v in (float(lo), True, "3"):
        assert _err({k: v}, "1m") == f"trade_settings_json.{k}: expected int"


@pytest.mark.parametrize("v", [0, -0.01, 0.2000001, "0.1"])
def test_1m_loss_pause_bounds(v):
    msg = _err({"intraday_loss_pause_pct": v}, "1m")
    assert msg in ("trade_settings_json.intraday_loss_pause_pct: must be > 0 and <= 0.2",
                   "trade_settings_json.intraday_loss_pause_pct: expected a number")


def test_1m_loss_pause_upper_bound_inclusive():
    assert _parse_1m({"intraday_loss_pause_pct": 0.2}).intraday_loss_pause_pct == 0.2


def test_1m_stop_pct_and_atr_mult_are_exclusive():
    assert _err({"stop_pct": 0.01, "stop_atr_mult": 2.0}, "1m") == \
        "trade_settings_json.stop_pct: cannot combine with stop_atr_mult"


def test_1m_take_profit_pct_and_atr_mult_are_exclusive():
    assert _err({"take_profit_pct": 0.01, "take_profit_atr_mult": 2.0}, "1m") == \
        "trade_settings_json.take_profit_pct: cannot combine with take_profit_atr_mult"


@pytest.mark.parametrize("style", ["swing", "long", "day"])
def test_1m_rejects_non_scalping(style):
    assert _err({"holding_style": style}, "1m") == \
        "trade_settings_json.holding_style: 1m groups support scalping only"


@pytest.mark.parametrize("session", ["extended", "all"])
def test_1m_rejects_extended_hours(session):
    assert _err({"session": session}, "1m") == \
        "trade_settings_json.session: extended hours not supported (IEX prints too thin)"


@pytest.mark.parametrize("obj", [{}, {"holding_style": "scalping", "session": "rth"},
                                 {"stop_atr_mult": 1.0, "entry_ttl_minutes": 3}])
def test_1m_empty_and_default_are_none(obj):
    assert _parse_1m(obj) is None
    assert tr.normalize(obj, timeframe="1m") == {}


def test_1m_stop_pct_alone_clears_the_default_atr_stop():
    s = _parse_1m({"stop_pct": 0.01})
    assert s.stop_pct == 0.01 and s.stop_atr_mult is None


def test_1m_normalize_drops_defaults_and_none():
    out = tr.normalize({"stop_pct": 0.01, "entry_ttl_minutes": 3, "atr_period": 20,
                        "holding_style": "scalping", "max_hold_minutes": 30}, timeframe="1m")
    assert out == {"stop_pct": 0.01, "atr_period": 20, "max_hold_minutes": 30}


def test_1m_rejects_1d_only_keys():
    assert _err({"max_hold_days": 5}, "1m") == "trade_settings_json: unknown key 'max_hold_days'"


def test_1m_unknown_key_reports_first_alphabetically():
    assert _err({"zeta": 1, "alpha": 2, "stop_pct": 0.01}, "1m") == \
        "trade_settings_json: unknown key 'alpha'"


def test_1m_prob_and_calibration_copy_the_1d_rules():
    s = _parse_1m({"entry_prob_min": 0.55, "calibration_run_id": CAL})
    assert s.entry_prob_min == 0.55 and s.calibration_run_id == CAL
    assert _err({"entry_prob_min": 0.55}, "1m") == \
        "trade_settings_json: entry_prob_min and calibration_run_id must be set together"
    assert _err({"entry_prob_min": 1, "calibration_run_id": CAL}, "1m") == \
        "trade_settings_json.entry_prob_min: must be > 0 and < 1"
    assert _err({"entry_prob_min": 0.5, "calibration_run_id": "x"}, "1m") == \
        "trade_settings_json.calibration_run_id: expected a uuid string"


def test_1m_registry_keys_match_keys_1m():
    r = tr.registry_entry()
    assert set(r["keys_1m"]) == set(tr.KEYS_1M) and len(tr.KEYS_1M) == 15
    assert r["keys_1m"]["stop_pct"]["excludes"] == "stop_atr_mult"
    assert r["keys_1m"]["stop_atr_mult"]["excludes"] == "stop_pct"
    assert r["keys_1m"]["take_profit_pct"]["excludes"] == "take_profit_atr_mult"
    assert r["keys_1m"]["take_profit_atr_mult"]["excludes"] == "take_profit_pct"
    assert r["holding_styles_1m"] == ["scalping"] and r["sessions_1m"] == ["rth"]
    defaults = tr.IntradayTradeSettings()
    for k, entry in r["keys_1m"].items():
        if "default" in entry:
            assert entry["default"] == getattr(defaults, k)
    for k, (lo, hi) in {**INT_1M, **FLOAT_1M}.items():
        assert (r["keys_1m"][k]["min"], r["keys_1m"][k]["max"]) == (lo, hi)


def test_1m_keys_follow_the_d5_order():
    from dataclasses import fields

    assert tr.KEYS_1M == tuple(f.name for f in fields(tr.IntradayTradeSettings))


def test_unknown_timeframe_still_takes_the_1d_refusal():
    assert _err({"take_profit_pct": 0.1}, "1w") == \
        "trade_settings_json: only supported on timeframe 1d"
