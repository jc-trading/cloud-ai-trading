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
from quant.engine.intraday_strategies import ORBConfig, ORBSignal, RankParams
from quant.engine.strategy import StrategyParams

V1_COMPOSITE_KEY = "cat.v1.composite"
M1_ORB_KEY = "cat.m1.orb"

_TYPE_NAMES = {int: "int", float: "float"}


class StrategyParamsError(ValueError):
    pass


@dataclass(frozen=True)
class V1Config:
    strategy: StrategyParams
    funnel: FunnelParams
    exits: ExitParams


def _sections(cls) -> dict[str, type]:
    hints = typing.get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


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
    timeframe: str = "1d"

    def default_params(self) -> dict:
        return {section: asdict(getattr(self.defaults, section))
                for section in _sections(type(self.defaults))}

    def params_schema(self) -> dict:
        schema: dict = {}
        for section, cls in _sections(type(self.defaults)).items():
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
        sections = _sections(type(self.defaults))
        unknown = sorted(set(params) - set(sections))
        if unknown:
            raise StrategyParamsError(f"unknown section(s): {', '.join(unknown)}")
        built = {}
        for section, cls in sections.items():
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
        return type(self.defaults)(**built)


REGISTRY: dict[str, StrategySpec] = {
    V1_COMPOSITE_KEY: StrategySpec(
        key=V1_COMPOSITE_KEY,
        description="v1 composite technical strategy (MA / MACD / RSI / ATR) "
                    "with the R0-9 G1 funnel and exit stack",
        defaults=V1Config(StrategyParams(), FunnelParams(min_confidence=65.0),
                          ExitParams()),
    ),
}
REGISTRY[M1_ORB_KEY] = StrategySpec(
    M1_ORB_KEY, "Opening-range breakout on 1min bars (long-only)",
    ORBConfig(ORBSignal(), RankParams()), "1m")


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
BOUNDS[M1_ORB_KEY] = {
    "signal": {
        "range_minutes": (5, 60), "breakout_buffer_pct": (0, 0.01),
        "entry_window_minutes": (10, 390), "min_volume_ratio": (0, 10),
        "atr_period": (5, 120),
    },
    "rank": {"rank_window": (2, 60), "watch_max": (1, 30)},
}

# (left, right): left must be strictly less than right
CROSS_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    V1_COMPOSITE_KEY: (
        ("strategy.ma_fast", "strategy.ma_slow"),
        ("strategy.macd_fast", "strategy.macd_slow"),
        ("funnel.atr_pct_min", "funnel.atr_pct_max"),
    ),
}
CROSS_RULES[M1_ORB_KEY] = (("signal.range_minutes", "signal.entry_window_minutes"),)


def check_bounds(key: str, cfg: V1Config) -> list[str]:
    """Every out-of-range field (inclusive bounds) in section/field order, then
    every violated cross rule. Empty list = the config is within bounds."""
    bounds = BOUNDS[key]
    problems = []
    for section, cls in _sections(type(cfg)).items():
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


GRIDS: dict[str, dict[str, tuple]] = {
    V1_COMPOSITE_KEY: {
        "funnel.min_confidence": (65.0, 70.0, 75.0),
        "exits.reversal_bars": (2, 3, 5),
        "exits.stagnation_bars": (20, 30, 45),
        "exits.trailing_start_r": (1.0, 1.5, 2.0),
        "exits.trailing_atr_mult": (2.0, 3.0, 4.0),
    },
}
GRIDS[M1_ORB_KEY] = {
    "signal.range_minutes": (5, 15, 30),
    "signal.breakout_buffer_pct": (0.0, 0.0005, 0.001),
    "signal.min_volume_ratio": (0.0, 1.0, 1.5),
}
GRID_MAX_KNOBS = 6
GRID_MAX_VALUES = 5
GRID_SECTIONS: dict[str, tuple[str, ...]] = {V1_COMPOSITE_KEY: ("funnel", "exits"),
                                             M1_ORB_KEY: ("signal",)}


def validate_grid(key: str, grid) -> dict[str, tuple]:
    """Validate a walk-forward search grid. None means the strategy's default
    grid; otherwise only the key's GRID_SECTIONS knobs, 1-5 distinct values each,
    every value type-checked like params_json and within bounds when applied
    on top of the defaults. Keeps the caller's knob order."""
    spec = get_spec(key)
    sections = GRID_SECTIONS[key]
    if grid is None:
        return {k: tuple(v) for k, v in GRIDS[key].items()}
    if not isinstance(grid, dict) or not grid:
        raise StrategyParamsError("grid: must be a non-empty object")
    if len(grid) > GRID_MAX_KNOBS:
        raise StrategyParamsError(f"grid: at most {GRID_MAX_KNOBS} knobs")
    out: dict[str, tuple] = {}
    for knob, values in grid.items():
        section, _, name = str(knob).partition(".")
        cls = _sections(type(spec.defaults)).get(section) if section in sections else None
        if cls is None or name not in {f.name for f in fields(cls)}:
            raise StrategyParamsError(
                f"grid.{knob}: unknown knob (only {' and '.join(s + '.*' for s in sections)})")
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= GRID_MAX_VALUES:
            raise StrategyParamsError(f"grid.{knob}: 1-{GRID_MAX_VALUES} values")
        hint = typing.get_type_hints(cls)[name]
        coerced: list = []
        for v in values:
            c = _coerce(section, name, hint, v)
            if c in coerced:
                raise StrategyParamsError(f"grid.{knob}: duplicate value {v}")
            problems = check_bounds(key, spec.build({section: {name: c}}))
            if problems:
                raise StrategyParamsError(f"grid.{knob}: {v} out of bounds: {problems[0]}")
            coerced.append(c)
        out[knob] = tuple(coerced)
    return out
