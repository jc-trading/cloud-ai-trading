"""Strategy registry — strategy_key -> parameter schema + validated config.

A strategy group stores its parameters as JSON. This module is the one place
that turns that JSON back into the engine's frozen param dataclasses, and it
refuses anything it does not recognise instead of silently ignoring it.
"""

from __future__ import annotations

import typing
from dataclasses import asdict, dataclass, fields

from quant.engine.exits import ExitParams
from quant.engine.funnel import FunnelParams
from quant.engine.strategy import StrategyParams

V1_COMPOSITE_KEY = "cat.v1.composite"

_SECTIONS = {"strategy": StrategyParams, "funnel": FunnelParams, "exits": ExitParams}
_TYPE_NAMES = {int: "int", float: "float"}


class StrategyParamsError(ValueError):
    pass


@dataclass(frozen=True)
class V1Config:
    strategy: StrategyParams
    funnel: FunnelParams
    exits: ExitParams


def _coerce(section: str, name: str, ftype: type, value):
    if ftype is int:
        if type(value) is not int:
            raise StrategyParamsError(f"{section}.{name}: expected int, got {value!r}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyParamsError(f"{section}.{name}: expected float, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class StrategySpec:
    key: str
    description: str
    defaults: V1Config

    def default_params(self) -> dict:
        return {section: asdict(getattr(self.defaults, section)) for section in _SECTIONS}

    def params_schema(self) -> dict:
        schema: dict = {}
        for section, cls in _SECTIONS.items():
            hints = typing.get_type_hints(cls)
            default = getattr(self.defaults, section)
            schema[section] = {
                f.name: {"type": _TYPE_NAMES[hints[f.name]],
                         "default": getattr(default, f.name)}
                for f in fields(cls)}
        return schema

    def build(self, params: dict) -> V1Config:
        """Validate a group's params JSON and return the engine config. Unknown
        sections or fields raise; missing fields take this spec's defaults; int
        fields accept only real ints (never bool or float), float fields accept
        any non-bool number."""
        if not isinstance(params, dict):
            raise StrategyParamsError(f"params must be an object, got {params!r}")
        unknown = sorted(set(params) - set(_SECTIONS))
        if unknown:
            raise StrategyParamsError(f"unknown section(s): {', '.join(unknown)}")
        built = {}
        for section, cls in _SECTIONS.items():
            given = params.get(section, {})
            if not isinstance(given, dict):
                raise StrategyParamsError(f"{section} must be an object, got {given!r}")
            hints = typing.get_type_hints(cls)
            names = {f.name for f in fields(cls)}
            extra = sorted(set(given) - names)
            if extra:
                raise StrategyParamsError(f"{section}: unknown field(s): {', '.join(extra)}")
            default = getattr(self.defaults, section)
            built[section] = cls(**{
                name: (_coerce(section, name, hints[name], given[name])
                       if name in given else getattr(default, name))
                for name in names})
        return V1Config(**built)


REGISTRY: dict[str, StrategySpec] = {
    V1_COMPOSITE_KEY: StrategySpec(
        key=V1_COMPOSITE_KEY,
        description="v1 composite technical strategy (MA / MACD / RSI / ATR) "
                    "with the R0-9 G1 funnel and exit stack",
        defaults=V1Config(StrategyParams(), FunnelParams(min_confidence=65.0),
                          ExitParams()),
    ),
}


def get_spec(key: str) -> StrategySpec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise StrategyParamsError(f"unknown strategy_key: {key!r}") from None


BOUNDS: dict[str, dict[str, dict[str, tuple[float, float]]]] = {
    V1_COMPOSITE_KEY: {
        "strategy": {
            "ma_fast": (2, 50), "ma_slow": (5, 250), "macd_fast": (2, 50),
            "macd_slow": (5, 100), "macd_signal": (2, 50), "rsi_period": (2, 50),
            "atr_period": (2, 50), "zscore_window": (20, 250),
            "rsi_overbought": (50, 95), "rsi_penalty_floor": (0, 1),
            "stop_atr_mult": (0.5, 6), "expected_move_atr_mult": (0.1, 5),
        },
        "funnel": {
            "min_adv": (0, 1e10), "min_price": (0, 1000), "atr_pct_min": (0, 0.5),
            "atr_pct_max": (0.001, 1), "max_per_sector": (1, 10),
            "shortlist_max": (1, 30), "min_confidence": (0, 100),
        },
        "exits": {
            "trailing_start_r": (0.25, 10), "trailing_atr_mult": (0.5, 10),
            "reversal_bars": (1, 20), "stagnation_bars": (5, 250),
            "stagnation_hurdle": (-0.5, 0.5),
        },
    },
}

# (left, right): left must be strictly less than right
CROSS_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    V1_COMPOSITE_KEY: (
        ("strategy.ma_fast", "strategy.ma_slow"),
        ("strategy.macd_fast", "strategy.macd_slow"),
        ("funnel.atr_pct_min", "funnel.atr_pct_max"),
    ),
}


def check_bounds(key: str, cfg: V1Config) -> list[str]:
    """Every out-of-range field (inclusive bounds) in section/field order, then
    every violated cross rule. Empty list = the config is within bounds."""
    bounds = BOUNDS[key]
    problems = []
    for section, cls in _SECTIONS.items():
        values = getattr(cfg, section)
        for f in fields(cls):
            lo, hi = bounds[section][f.name]
            v = getattr(values, f.name)
            if not lo <= v:
                problems.append(f"params_json.{section}.{f.name}: {v} < min {lo}")
            elif not v <= hi:
                problems.append(f"params_json.{section}.{f.name}: {v} > max {hi}")
    for a, b in CROSS_RULES[key]:
        va = getattr(getattr(cfg, a.split(".")[0]), a.split(".")[1])
        vb = getattr(getattr(cfg, b.split(".")[0]), b.split(".")[1])
        if not va < vb:
            problems.append(f"params_json: {a} ({va}) must be < {b} ({vb})")
    return problems


def ui_schema(key: str) -> dict:
    schema = get_spec(key).params_schema()
    for section, entries in schema.items():
        for name, entry in entries.items():
            entry["min"], entry["max"] = BOUNDS[key][section][name]
    return schema
