"""P1 strategy registry: strategy_key -> schema + validated V1Config."""

from dataclasses import fields

import pytest

from quant.engine.exits import ExitParams
from quant.engine.funnel import FunnelParams
from quant.engine.strategies import (REGISTRY, V1_COMPOSITE_KEY, StrategyParamsError,
                                     V1Config, get_spec)
from quant.engine.strategy import StrategyParams


def test_registry_has_exactly_the_v1_key():
    assert list(REGISTRY) == [V1_COMPOSITE_KEY, "cat.m1.orb"]
    assert get_spec(V1_COMPOSITE_KEY).key == V1_COMPOSITE_KEY
    with pytest.raises(StrategyParamsError):
        get_spec("cat.v2.unknown")


def test_v1_defaults_match_the_deployed_engine():
    assert get_spec(V1_COMPOSITE_KEY).defaults == V1Config(
        StrategyParams(), FunnelParams(min_confidence=65.0), ExitParams())


def test_default_params_round_trip():
    spec = get_spec(V1_COMPOSITE_KEY)
    assert spec.build(spec.default_params()) == spec.defaults


def test_rejects_unknown_section():
    with pytest.raises(StrategyParamsError, match="unknown section"):
        get_spec(V1_COMPOSITE_KEY).build({"screener": {}})


def test_rejects_unknown_field():
    with pytest.raises(StrategyParamsError, match="unknown field"):
        get_spec(V1_COMPOSITE_KEY).build({"strategy": {"ma_medium": 10}})


def test_int_field_rejects_bool():
    with pytest.raises(StrategyParamsError, match="expected int"):
        get_spec(V1_COMPOSITE_KEY).build({"strategy": {"ma_fast": True}})


def test_int_field_rejects_float():
    with pytest.raises(StrategyParamsError, match="expected int"):
        get_spec(V1_COMPOSITE_KEY).build({"exits": {"reversal_bars": 3.0}})


def test_float_field_rejects_str_and_bool():
    spec = get_spec(V1_COMPOSITE_KEY)
    with pytest.raises(StrategyParamsError, match="expected float"):
        spec.build({"funnel": {"min_confidence": "70"}})
    with pytest.raises(StrategyParamsError, match="expected float"):
        spec.build({"funnel": {"min_confidence": False}})


def test_float_field_accepts_int_as_float():
    cfg = get_spec(V1_COMPOSITE_KEY).build({"funnel": {"min_confidence": 70}})
    assert cfg.funnel.min_confidence == 70.0
    assert type(cfg.funnel.min_confidence) is float


def test_missing_fields_take_spec_defaults():
    cfg = get_spec(V1_COMPOSITE_KEY).build({"strategy": {"ma_fast": 7}})
    assert cfg.strategy == StrategyParams(ma_fast=7)
    assert cfg.funnel == FunnelParams(min_confidence=65.0)
    assert cfg.exits == ExitParams()


def test_rejects_non_object_params():
    spec = get_spec(V1_COMPOSITE_KEY)
    with pytest.raises(StrategyParamsError):
        spec.build([])
    with pytest.raises(StrategyParamsError):
        spec.build({"exits": [1]})


def test_schema_covers_every_field_with_type_and_default():
    spec = get_spec(V1_COMPOSITE_KEY)
    schema = spec.params_schema()
    defaults = spec.default_params()
    for section, cls in (("strategy", StrategyParams), ("funnel", FunnelParams),
                         ("exits", ExitParams)):
        assert set(schema[section]) == {f.name for f in fields(cls)}
        for name, entry in schema[section].items():
            assert entry["type"] in ("int", "float")
            assert entry["default"] == defaults[section][name]
    assert set(schema) == {"strategy", "funnel", "exits"}
    assert schema["strategy"]["ma_fast"]["type"] == "int"
    assert schema["funnel"]["min_confidence"] == {"type": "float", "default": 65.0}


# --- P3a: parameter bounds -----------------------------------------------------

def _bounds(params):
    from quant.engine.strategies import check_bounds
    return check_bounds(V1_COMPOSITE_KEY, get_spec(V1_COMPOSITE_KEY).build(params))


def test_defaults_are_within_bounds():
    assert _bounds({}) == []


def test_migration_019_group1_params_are_within_bounds():
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "backend" / "migrations" / "versions"
           / "019_strategy_groups.py").read_text()
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == "_V1_PARAMS")
    assert _bounds(ast.literal_eval(node.value)) == []


def test_below_min_is_reported():
    assert _bounds({"strategy": {"ma_fast": 1}}) == [
        "params_json.strategy.ma_fast: 1 < min 2"]


def test_nan_is_reported():
    [problem] = _bounds({"funnel": {"min_price": float("nan")}})
    assert problem.startswith("params_json.funnel.min_price: nan < min ")


def test_above_max_is_reported():
    assert _bounds({"strategy": {"zscore_window": 251}}) == [
        "params_json.strategy.zscore_window: 251 > max 250"]


def test_bounds_are_inclusive():
    assert _bounds({"strategy": {"zscore_window": 250, "ma_fast": 2}}) == []


def test_cross_rule_ma():
    assert _bounds({"strategy": {"ma_fast": 30, "ma_slow": 20}}) == [
        "params_json: strategy.ma_fast (30) must be < strategy.ma_slow (20)"]


def test_cross_rule_macd_is_strict():
    assert _bounds({"strategy": {"macd_fast": 26, "macd_slow": 26}}) == [
        "params_json: strategy.macd_fast (26) must be < strategy.macd_slow (26)"]


def test_cross_rule_atr_pct():
    assert _bounds({"funnel": {"atr_pct_min": 0.1, "atr_pct_max": 0.05}}) == [
        "params_json: funnel.atr_pct_min (0.1) must be < funnel.atr_pct_max (0.05)"]


def test_out_of_range_fields_come_before_cross_rules():
    assert _bounds({"strategy": {"ma_fast": 60, "ma_slow": 40},
                    "exits": {"reversal_bars": 0}}) == [
        "params_json.strategy.ma_fast: 60 > max 50",
        "params_json.exits.reversal_bars: 0 < min 1",
        "params_json: strategy.ma_fast (60) must be < strategy.ma_slow (40)"]


def test_ui_schema_bounds_every_field_around_its_default():
    from quant.engine.strategies import ui_schema

    spec = get_spec(V1_COMPOSITE_KEY)
    schema = ui_schema(V1_COMPOSITE_KEY)
    base = spec.params_schema()
    assert set(schema) == set(base)
    for section, entries in schema.items():
        assert set(entries) == set(base[section])
        for name, entry in entries.items():
            assert {k: entry[k] for k in ("type", "default")} == base[section][name]
            assert entry["min"] <= entry["default"] <= entry["max"]
    assert "min" not in spec.params_schema()["strategy"]["ma_fast"]


def _grid_error(grid) -> str:
    from quant.engine.strategies import validate_grid

    with pytest.raises(StrategyParamsError) as e:
        validate_grid(V1_COMPOSITE_KEY, grid)
    return str(e.value)


def test_validate_grid_none_returns_a_copy_of_the_default_grid():
    from quant.engine.strategies import GRIDS, validate_grid

    got = validate_grid(V1_COMPOSITE_KEY, None)
    assert got == GRIDS[V1_COMPOSITE_KEY]
    got["exits.reversal_bars"] = (9,)
    got.pop("funnel.min_confidence")
    assert GRIDS[V1_COMPOSITE_KEY]["exits.reversal_bars"] == (2, 3, 5)
    assert "funnel.min_confidence" in GRIDS[V1_COMPOSITE_KEY]


def test_validate_grid_unknown_strategy_key_raises():
    from quant.engine.strategies import validate_grid

    with pytest.raises(StrategyParamsError, match="unknown strategy_key"):
        validate_grid("cat.v2.unknown", None)


def test_validate_grid_rejects_strategy_section():
    assert "unknown knob" in _grid_error({"strategy.ma_fast": [5]})


def test_validate_grid_rejects_unknown_field():
    assert _grid_error({"exits.nope": [1]}) == \
        "grid.exits.nope: unknown knob (only funnel.* and exits.*)"


def test_validate_grid_rejects_bool_for_int():
    assert "expected int" in _grid_error({"exits.reversal_bars": [True]})


def test_validate_grid_rejects_float_for_int():
    assert "expected int" in _grid_error({"exits.reversal_bars": [2.0]})


def test_validate_grid_rejects_duplicate_after_coercion():
    assert _grid_error({"exits.trailing_start_r": [1, 1.0]}) == \
        "grid.exits.trailing_start_r: duplicate value 1.0"


def test_validate_grid_knob_and_value_limits():
    knobs = {f"exits.{k}": [1.0] for k in ("trailing_start_r", "trailing_atr_mult")}
    knobs.update({f"funnel.{k}": [1] for k in ("max_per_sector", "shortlist_max")})
    knobs.update({"funnel.min_confidence": [70.0], "funnel.min_price": [5.0],
                  "funnel.min_adv": [1e6]})
    assert _grid_error(knobs) == "grid: at most 6 knobs"
    assert _grid_error({"exits.reversal_bars": [1, 2, 3, 4, 5, 6]}) == \
        "grid.exits.reversal_bars: 1-5 values"
    assert _grid_error({"exits.reversal_bars": []}) == "grid.exits.reversal_bars: 1-5 values"
    assert _grid_error({}) == "grid: must be a non-empty object"
    assert _grid_error([("exits.reversal_bars", [2])]) == "grid: must be a non-empty object"


def test_validate_grid_rejects_out_of_bounds_value():
    assert _grid_error({"exits.reversal_bars": [0]}) == (
        "grid.exits.reversal_bars: 0 out of bounds: "
        "params_json.exits.reversal_bars: 0 < min 1")


def test_validate_grid_applies_cross_rules_against_defaults():
    assert "must be < funnel.atr_pct_max" in _grid_error({"funnel.atr_pct_min": [0.09]})


def test_validate_grid_custom_grid_keeps_order_and_coerces():
    from quant.engine.strategies import validate_grid

    got = validate_grid(V1_COMPOSITE_KEY, {"exits.stagnation_bars": [45, 20],
                                           "funnel.min_confidence": [70, 80.5]})
    assert list(got) == ["exits.stagnation_bars", "funnel.min_confidence"]
    assert got == {"exits.stagnation_bars": (45, 20), "funnel.min_confidence": (70.0, 80.5)}
    assert all(type(v) is float for v in got["funnel.min_confidence"])


# --- P5a: the 1min ORB strategy and the generalised registry ---------------

M1 = "cat.m1.orb"
V1_BEFORE_P5 = (
    '{"defaults": {"exits": {"reversal_bars": 3, "stagnation_bars": 30, "stagnation_h'
    'urdle": 0.0, "trailing_atr_mult": 3.0, "trailing_start_r": 1.5}, "funnel": {"atr'
    '_pct_max": 0.08, "atr_pct_min": 0.01, "max_per_sector": 2, "min_adv": 20000000.0'
    ', "min_confidence": 65.0, "min_price": 5.0, "shortlist_max": 10}, "strategy": {"'
    'atr_period": 14, "expected_move_atr_mult": 1.0, "ma_fast": 5, "ma_slow": 20, "ma'
    'cd_fast": 12, "macd_signal": 9, "macd_slow": 26, "rsi_overbought": 70.0, "rsi_pe'
    'nalty_floor": 0.2, "rsi_period": 14, "stop_atr_mult": 2.0, "zscore_window": 60}}'
    ', "grid": {"exits.reversal_bars": [2, 3, 5], "exits.stagnation_bars": [20, 30, 4'
    '5], "exits.trailing_atr_mult": [2.0, 3.0, 4.0], "exits.trailing_start_r": [1.0, '
    '1.5, 2.0], "funnel.min_confidence": [65.0, 70.0, 75.0]}, "schema": {"exits": {"r'
    'eversal_bars": {"default": 3, "type": "int"}, "stagnation_bars": {"default": 30,'
    ' "type": "int"}, "stagnation_hurdle": {"default": 0.0, "type": "float"}, "traili'
    'ng_atr_mult": {"default": 3.0, "type": "float"}, "trailing_start_r": {"default":'
    ' 1.5, "type": "float"}}, "funnel": {"atr_pct_max": {"default": 0.08, "type": "fl'
    'oat"}, "atr_pct_min": {"default": 0.01, "type": "float"}, "max_per_sector": {"de'
    'fault": 2, "type": "int"}, "min_adv": {"default": 20000000.0, "type": "float"}, '
    '"min_confidence": {"default": 65.0, "type": "float"}, "min_price": {"default": 5'
    '.0, "type": "float"}, "shortlist_max": {"default": 10, "type": "int"}}, "strateg'
    'y": {"atr_period": {"default": 14, "type": "int"}, "expected_move_atr_mult": {"d'
    'efault": 1.0, "type": "float"}, "ma_fast": {"default": 5, "type": "int"}, "ma_sl'
    'ow": {"default": 20, "type": "int"}, "macd_fast": {"default": 12, "type": "int"}'
    ', "macd_signal": {"default": 9, "type": "int"}, "macd_slow": {"default": 26, "ty'
    'pe": "int"}, "rsi_overbought": {"default": 70.0, "type": "float"}, "rsi_penalty_'
    'floor": {"default": 0.2, "type": "float"}, "rsi_period": {"default": 14, "type":'
    ' "int"}, "stop_atr_mult": {"default": 2.0, "type": "float"}, "zscore_window": {"'
    'default": 60, "type": "int"}}}, "ui": {"exits": {"reversal_bars": {"default": 3,'
    ' "max": 20, "min": 1, "type": "int"}, "stagnation_bars": {"default": 30, "max": '
    '250, "min": 5, "type": "int"}, "stagnation_hurdle": {"default": 0.0, "max": 0.5,'
    ' "min": -0.5, "type": "float"}, "trailing_atr_mult": {"default": 3.0, "max": 10,'
    ' "min": 0.5, "type": "float"}, "trailing_start_r": {"default": 1.5, "max": 10, "'
    'min": 0.25, "type": "float"}}, "funnel": {"atr_pct_max": {"default": 0.08, "max"'
    ': 1, "min": 0.001, "type": "float"}, "atr_pct_min": {"default": 0.01, "max": 0.5'
    ', "min": 0, "type": "float"}, "max_per_sector": {"default": 2, "max": 10, "min":'
    ' 1, "type": "int"}, "min_adv": {"default": 20000000.0, "max": 10000000000.0, "mi'
    'n": 0, "type": "float"}, "min_confidence": {"default": 65.0, "max": 100, "min": '
    '0, "type": "float"}, "min_price": {"default": 5.0, "max": 1000, "min": 0, "type"'
    ': "float"}, "shortlist_max": {"default": 10, "max": 30, "min": 1, "type": "int"}'
    '}, "strategy": {"atr_period": {"default": 14, "max": 50, "min": 2, "type": "int"'
    '}, "expected_move_atr_mult": {"default": 1.0, "max": 5, "min": 0.1, "type": "flo'
    'at"}, "ma_fast": {"default": 5, "max": 50, "min": 2, "type": "int"}, "ma_slow": '
    '{"default": 20, "max": 250, "min": 5, "type": "int"}, "macd_fast": {"default": 1'
    '2, "max": 50, "min": 2, "type": "int"}, "macd_signal": {"default": 9, "max": 50,'
    ' "min": 2, "type": "int"}, "macd_slow": {"default": 26, "max": 100, "min": 5, "t'
    'ype": "int"}, "rsi_overbought": {"default": 70.0, "max": 95, "min": 50, "type": '
    '"float"}, "rsi_penalty_floor": {"default": 0.2, "max": 1, "min": 0, "type": "flo'
    'at"}, "rsi_period": {"default": 14, "max": 50, "min": 2, "type": "int"}, "stop_a'
    'tr_mult": {"default": 2.0, "max": 6, "min": 0.5, "type": "float"}, "zscore_windo'
    'w": {"default": 60, "max": 250, "min": 20, "type": "int"}}}}'
)


def test_orb_default_params_round_trip():
    spec = get_spec(M1)
    assert spec.build(spec.default_params()) == spec.defaults


def test_timeframes_come_from_the_spec():
    assert get_spec(M1).timeframe == "1m"
    assert get_spec(V1_COMPOSITE_KEY).timeframe == "1d"


def test_v1_schema_ui_defaults_and_grid_are_unchanged_by_p5():
    import json

    from quant.engine import strategies as s

    k = V1_COMPOSITE_KEY
    now = json.dumps({"schema": s.get_spec(k).params_schema(), "ui": s.ui_schema(k),
                      "defaults": s.get_spec(k).default_params(),
                      "grid": {a: list(b) for a, b in s.GRIDS[k].items()}}, sort_keys=True)
    assert now == V1_BEFORE_P5


def _orb_bounds(params):
    from quant.engine.strategies import check_bounds
    return check_bounds(M1, get_spec(M1).build(params))


def test_orb_defaults_are_within_bounds():
    assert _orb_bounds({}) == []


def test_orb_out_of_range_is_reported():
    assert _orb_bounds({"signal": {"range_minutes": 4}, "rank": {"watch_max": 31}}) == [
        "params_json.signal.range_minutes: 4 < min 5",
        "params_json.rank.watch_max: 31 > max 30"]


def test_orb_cross_rule_range_before_entry_window():
    assert _orb_bounds({"signal": {"range_minutes": 60, "entry_window_minutes": 30}}) == [
        "params_json: signal.range_minutes (60) must be < signal.entry_window_minutes (30)"]


def test_orb_grid_rejects_v1_knobs():
    from quant.engine.strategies import validate_grid

    with pytest.raises(StrategyParamsError) as e:
        validate_grid(M1, {"funnel.min_confidence": [70.0]})
    assert str(e.value) == "grid.funnel.min_confidence: unknown knob (only signal.*)"


def test_orb_ui_schema_bounds_every_field_around_its_default():
    from quant.engine.strategies import ui_schema

    schema = ui_schema(M1)
    assert set(schema) == {"signal", "rank"}
    for entries in schema.values():
        for entry in entries.values():
            assert entry["min"] <= entry["default"] <= entry["max"]


def test_orb_rejects_unknown_section():
    with pytest.raises(StrategyParamsError, match="unknown section"):
        get_spec(M1).build({"strategy": {}})


def test_orb_default_grid():
    from quant.engine.strategies import GRIDS, validate_grid

    assert validate_grid(M1, None) == GRIDS[M1]


def test_orb_grid_coerces_and_checks_bounds():
    from quant.engine.strategies import validate_grid

    assert validate_grid(M1, {"signal.min_volume_ratio": [0, 2]}) == {
        "signal.min_volume_ratio": (0.0, 2.0)}
    with pytest.raises(StrategyParamsError, match="out of bounds"):
        validate_grid(M1, {"signal.range_minutes": [90]})
