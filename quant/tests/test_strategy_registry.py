"""P1 strategy registry: strategy_key -> schema + validated V1Config."""

from dataclasses import fields

import pytest

from quant.engine.exits import ExitParams
from quant.engine.funnel import FunnelParams
from quant.engine.strategies import (REGISTRY, V1_COMPOSITE_KEY, StrategyParamsError,
                                     V1Config, get_spec)
from quant.engine.strategy import StrategyParams


def test_registry_has_exactly_the_v1_key():
    assert list(REGISTRY) == [V1_COMPOSITE_KEY]
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
