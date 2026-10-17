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
