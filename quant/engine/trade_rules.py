"""D15 per-group trade settings — shared by the simulator and the live cycles."""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from quant.engine.exits import ExitDecision, ExitParams, Position, evaluate_exit
from quant.engine.signal import Direction

ENTRY_LIMIT_BOUNDS = (-0.10, 0.03)
TAKE_PROFIT_BOUNDS = (0.01, 1.0)
MAX_HOLD_BOUNDS = (1, 250)
HOLDING_STYLES_1D = ("swing", "long")
KEYS = ("entry_prob_min", "calibration_run_id", "entry_limit_offset_pct",
        "take_profit_pct", "max_hold_days", "holding_style")
KEYS_1M = ("entry_prob_min", "calibration_run_id", "entry_limit_offset_pct",
           "entry_ttl_minutes", "stop_pct", "stop_atr_mult", "take_profit_pct",
           "take_profit_atr_mult", "atr_period", "max_hold_minutes",
           "flatten_minutes_before_close", "max_entries_per_symbol_day",
           "intraday_loss_pause_pct", "holding_style", "session")
FLOAT_BOUNDS_1M = (("entry_limit_offset_pct", (-0.05, 0.01)), ("stop_pct", (0.001, 0.10)),
                   ("stop_atr_mult", (0.25, 10)), ("take_profit_pct", (0.001, 0.20)),
                   ("take_profit_atr_mult", (0.25, 20)))
INT_BOUNDS_1M = (("entry_ttl_minutes", (1, 30)), ("atr_period", (5, 120)),
                 ("max_hold_minutes", (1, 390)), ("flatten_minutes_before_close", (1, 60)),
                 ("max_entries_per_symbol_day", (1, 10)))


@dataclass(frozen=True)
class TradeSettings:
    entry_prob_min: float | None = None
    calibration_run_id: str | None = None
    entry_limit_offset_pct: float | None = None
    take_profit_pct: float | None = None
    max_hold_days: int | None = None
    holding_style: str = "swing"


@dataclass(frozen=True)
class IntradayTradeSettings:
    entry_prob_min: float | None = None
    calibration_run_id: str | None = None
    entry_limit_offset_pct: float | None = None
    entry_ttl_minutes: int = 3
    stop_pct: float | None = None
    stop_atr_mult: float | None = 1.0
    take_profit_pct: float | None = None
    take_profit_atr_mult: float | None = None
    atr_period: int = 14
    max_hold_minutes: int | None = None
    flatten_minutes_before_close: int = 5
    max_entries_per_symbol_day: int = 1
    intraday_loss_pause_pct: float = 0.02
    holding_style: str = "scalping"
    session: str = "rth"


class TradeSettingsError(ValueError):
    pass


def _number(k: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TradeSettingsError(f"trade_settings_json.{k}: expected a number")
    if not math.isfinite(v):
        raise TradeSettingsError(f"trade_settings_json.{k}: expected a finite number")
    return v


def _bounded(k: str, v, lo, hi) -> None:
    if not lo <= v <= hi:
        raise TradeSettingsError(f"trade_settings_json.{k}: {v} outside [{lo}, {hi}]")


def parse_trade_settings(obj, *, timeframe: str) -> TradeSettings | None:
    """Validate a stored trade_settings_json and return its TradeSettings, or None
    when it is empty or equal to the defaults (so such a group behaves exactly like
    one without settings). Raises TradeSettingsError with a user-facing message."""
    if not isinstance(obj, dict):
        raise TradeSettingsError("trade_settings_json: must be an object")
    if obj == {}:
        return None
    if timeframe == "1m":
        return _parse_intraday(obj)
    unknown = sorted(k for k in obj if k not in KEYS)
    if unknown:
        raise TradeSettingsError(f"trade_settings_json: unknown key '{unknown[0]}'")
    if timeframe != "1d":
        raise TradeSettingsError("trade_settings_json: only supported on timeframe 1d")

    fields: dict = {}
    for k, (lo, hi) in (("entry_limit_offset_pct", ENTRY_LIMIT_BOUNDS),
                        ("take_profit_pct", TAKE_PROFIT_BOUNDS)):
        if k in obj:
            v = _number(k, obj[k])
            _bounded(k, v, lo, hi)
            fields[k] = float(v)
    if "entry_prob_min" in obj:
        v = _number("entry_prob_min", obj["entry_prob_min"])
        if not 0 < v < 1:
            raise TradeSettingsError("trade_settings_json.entry_prob_min: must be > 0 and < 1")
        fields["entry_prob_min"] = float(v)
    if "max_hold_days" in obj:
        v = obj["max_hold_days"]
        if type(v) is not int:
            raise TradeSettingsError("trade_settings_json.max_hold_days: expected int")
        _bounded("max_hold_days", v, *MAX_HOLD_BOUNDS)
        fields["max_hold_days"] = v
    if "calibration_run_id" in obj:
        v = obj["calibration_run_id"]
        try:
            if not isinstance(v, str):
                raise ValueError
            fields["calibration_run_id"] = str(UUID(v))
        except ValueError:
            raise TradeSettingsError(
                "trade_settings_json.calibration_run_id: expected a uuid string") from None
    if "holding_style" in obj:
        v = obj["holding_style"]
        if v == "scalping":
            raise TradeSettingsError(
                "trade_settings_json.holding_style: scalping requires timeframe 1m (P5)")
        if v not in HOLDING_STYLES_1D:
            raise TradeSettingsError(
                "trade_settings_json.holding_style: must be one of swing, long")
        fields["holding_style"] = v
    if ("entry_prob_min" in obj) != ("calibration_run_id" in obj):
        raise TradeSettingsError(
            "trade_settings_json: entry_prob_min and calibration_run_id must be set together")

    s = TradeSettings(**fields)
    return None if s == TradeSettings() else s


def _parse_intraday(obj: dict) -> IntradayTradeSettings | None:
    unknown = sorted(k for k in obj if k not in KEYS_1M)
    if unknown:
        raise TradeSettingsError(f"trade_settings_json: unknown key '{unknown[0]}'")
    fields: dict = {}
    for k, (lo, hi) in FLOAT_BOUNDS_1M:
        if k in obj:
            v = _number(k, obj[k])
            _bounded(k, v, lo, hi)
            fields[k] = float(v)
    if "intraday_loss_pause_pct" in obj:
        v = _number("intraday_loss_pause_pct", obj["intraday_loss_pause_pct"])
        if not 0 < v <= 0.2:
            raise TradeSettingsError(
                "trade_settings_json.intraday_loss_pause_pct: must be > 0 and <= 0.2")
        fields["intraday_loss_pause_pct"] = float(v)
    for k, (lo, hi) in INT_BOUNDS_1M:
        if k in obj:
            v = obj[k]
            if type(v) is not int:
                raise TradeSettingsError(f"trade_settings_json.{k}: expected int")
            _bounded(k, v, lo, hi)
            fields[k] = v
    if "entry_prob_min" in obj:
        v = _number("entry_prob_min", obj["entry_prob_min"])
        if not 0 < v < 1:
            raise TradeSettingsError("trade_settings_json.entry_prob_min: must be > 0 and < 1")
        fields["entry_prob_min"] = float(v)
    if "calibration_run_id" in obj:
        v = obj["calibration_run_id"]
        try:
            if not isinstance(v, str):
                raise ValueError
            fields["calibration_run_id"] = str(UUID(v))
        except ValueError:
            raise TradeSettingsError(
                "trade_settings_json.calibration_run_id: expected a uuid string") from None
    if "holding_style" in obj:
        if obj["holding_style"] != "scalping":
            raise TradeSettingsError(
                "trade_settings_json.holding_style: 1m groups support scalping only")
        fields["holding_style"] = "scalping"
    if "session" in obj:
        if obj["session"] != "rth":
            raise TradeSettingsError(
                "trade_settings_json.session: extended hours not supported (IEX prints too thin)")
        fields["session"] = "rth"
    if "stop_pct" in obj and "stop_atr_mult" in obj:
        raise TradeSettingsError("trade_settings_json.stop_pct: cannot combine with stop_atr_mult")
    if "take_profit_pct" in obj and "take_profit_atr_mult" in obj:
        raise TradeSettingsError(
            "trade_settings_json.take_profit_pct: cannot combine with take_profit_atr_mult")
    if ("entry_prob_min" in obj) != ("calibration_run_id" in obj):
        raise TradeSettingsError(
            "trade_settings_json: entry_prob_min and calibration_run_id must be set together")
    if "stop_pct" in fields:
        fields["stop_atr_mult"] = None

    s = IntradayTradeSettings(**fields)
    return None if s == IntradayTradeSettings() else s


def normalize(obj, *, timeframe: str = "1d") -> dict:
    if timeframe == "1m":
        s = parse_trade_settings(obj, timeframe="1m")
        return {} if s is None else {
            k: getattr(s, k) for k in KEYS_1M
            if getattr(s, k) is not None and getattr(s, k) != getattr(IntradayTradeSettings(), k)}
    s = parse_trade_settings(obj, timeframe=timeframe)
    if s is None:
        return {}
    out: dict = {}
    for k in ("entry_prob_min", "calibration_run_id", "entry_limit_offset_pct",
              "take_profit_pct", "max_hold_days"):
        v = getattr(s, k)
        if v is not None:
            out[k] = v
    if s.holding_style == "long":
        out["holding_style"] = "long"
    return out


def allows_entry_at_open(open_px: float, ref_close: float, s: TradeSettings | None) -> bool:
    if s is None or s.entry_limit_offset_pct is None:
        return True
    if not ref_close > 0:
        return False
    return open_px <= ref_close * (1.0 + s.entry_limit_offset_pct)


def decide_exit(pos: Position, bar_low: float, bar_close: float, *,
                signal_direction: Direction, expected_move: float, params: ExitParams,
                benchmark_return_since_entry: float | None, bar_open: float,
                bar_high: float, settings: TradeSettings | None) -> ExitDecision | None:
    """evaluate_exit plus the group's take-profit / holding-style / max-hold rules.
    Priority: resting stop (hard/trailing) > take-profit > reversal/stagnation
    (swing only) > max hold. settings=None or defaults equals evaluate_exit."""
    d = evaluate_exit(pos, bar_low, bar_close, signal_direction=signal_direction,
                      expected_move=expected_move, params=params,
                      benchmark_return_since_entry=benchmark_return_since_entry, bar_open=bar_open)
    if settings is None or (d is not None and d.action in ("hard_stop", "trailing")):
        return d
    if settings.take_profit_pct is not None:
        target = pos.avg_cost * (1.0 + settings.take_profit_pct)
        if bar_high >= target:
            return ExitDecision("take_profit", max(target, bar_open), f"high {bar_high:.2f} >= target {target:.2f}")
    if d is not None and settings.holding_style == "swing":
        return d
    if settings.max_hold_days is not None and pos.bars_held >= settings.max_hold_days:
        return ExitDecision("max_hold", bar_close, f"held {pos.bars_held} bars >= {settings.max_hold_days}")
    return None


def effective_min_confidence(base: float, s: TradeSettings | None,
                             threshold: float | None) -> float:
    if s is None or s.entry_prob_min is None:
        return base
    return max(base, threshold if threshold is not None else math.inf)


def registry_entry() -> dict:
    return {
        "available": True,
        "keys": {
            "entry_prob_min": {"type": "float", "gt": 0.0, "lt": 1.0,
                               "requires": "calibration_run_id"},
            "calibration_run_id": {"type": "uuid", "requires": "entry_prob_min"},
            "entry_limit_offset_pct": {"type": "float", "min": -0.10, "max": 0.03},
            "take_profit_pct": {"type": "float", "min": 0.01, "max": 1.0},
            "max_hold_days": {"type": "int", "min": 1, "max": 250},
            "holding_style": {"type": "enum", "values": ["swing", "long"],
                              "default": "swing"},
        },
        "holding_styles_1d": ["swing", "long"],
        "keys_1m": {
            "entry_prob_min": {"type": "float", "gt": 0.0, "lt": 1.0,
                               "requires": "calibration_run_id"},
            "calibration_run_id": {"type": "uuid", "requires": "entry_prob_min"},
            "entry_limit_offset_pct": {"type": "float", "min": -0.05, "max": 0.01},
            "entry_ttl_minutes": {"type": "int", "min": 1, "max": 30, "default": 3},
            "stop_pct": {"type": "float", "min": 0.001, "max": 0.10,
                         "excludes": "stop_atr_mult"},
            "stop_atr_mult": {"type": "float", "min": 0.25, "max": 10, "default": 1.0,
                              "excludes": "stop_pct"},
            "take_profit_pct": {"type": "float", "min": 0.001, "max": 0.20,
                                "excludes": "take_profit_atr_mult"},
            "take_profit_atr_mult": {"type": "float", "min": 0.25, "max": 20,
                                     "excludes": "take_profit_pct"},
            "atr_period": {"type": "int", "min": 5, "max": 120, "default": 14},
            "max_hold_minutes": {"type": "int", "min": 1, "max": 390},
            "flatten_minutes_before_close": {"type": "int", "min": 1, "max": 60,
                                             "default": 5},
            "max_entries_per_symbol_day": {"type": "int", "min": 1, "max": 10, "default": 1},
            "intraday_loss_pause_pct": {"type": "float", "gt": 0.0, "max": 0.2,
                                        "default": 0.02},
            "holding_style": {"type": "enum", "values": ["scalping"], "default": "scalping"},
            "session": {"type": "enum", "values": ["rth"], "default": "rth"},
        },
        "holding_styles_1m": ["scalping"],
        "sessions_1m": ["rth"],
    }
