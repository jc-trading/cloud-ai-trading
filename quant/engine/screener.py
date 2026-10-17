"""Rule-based stock screener — a group's ``screener_json`` -> today's pool.

Pure: spec parsing, point-in-time share counts, feature math and rule
application, no I/O. The data layer (``quant.data.screen``) feeds it panel
frames and SEC share facts; the backtest will feed it the same frames, so a
live pool and a backtest pool for the same session are computed identically.

Price-level fields (price, adv_usd, market_cap) read RAW closes and volumes;
ratio fields (ret_std, close_vs_ema) read split/dividend-adjusted returns. A
window with any missing session is NULL, never interpolated, and each rule's
``on_null`` decides whether a NULL passes.
"""

from __future__ import annotations

import math
import operator
import re
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from quant import config
from quant.engine import indicators

FIELDS: dict[str, tuple[int, int] | None] = {
    "market_cap": None,
    "price": None,
    "adv_usd": (1, 250),
    "ret_std": (2, 250),
    "close_vs_ema": (2, 60),
}
OPS = (">=", "<=", ">", "<", "==")
VALUE_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "market_cap": (0, None),
    "price": (0, None),
    "adv_usd": (0, None),
    "ret_std": (0, 1),
    "close_vs_ema": (-1, 10),
}
MAX_RULES = 20
MAX_LIST = 500
DEFAULT_SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}
SHARE_CONCEPTS = ("dei:EntityCommonStockSharesOutstanding",
                  "us-gaap:CommonStockSharesOutstanding")
NO_MARKET_CAP_TYPES = ("etf", "adr")
# a foreign private issuer reports ORDINARY shares; its US line may be an ADR
# trading at a ratio, so shares x that price is not its market cap
FOREIGN_FORMS = ("20-F", "6-K")

_TOP_KEYS = ("rules", "include", "exclude", "asset_types")
_RULE_KEYS = ("field", "op", "value", "window", "on_null")
_ON_NULL = ("fail", "pass")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,14}$")
_OP_FN = {">=": operator.ge, "<=": operator.le, ">": operator.gt,
          "<": operator.lt, "==": operator.eq}


class ScreenerSpecError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    field: str
    op: str
    value: float
    window: int | None
    on_null: str

    @property
    def key(self) -> str:
        return self.field if self.window is None else f"{self.field}:{self.window}"


@dataclass(frozen=True)
class ScreenerSpec:
    rules: tuple[Rule, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    asset_types: tuple[str, ...]


@dataclass(frozen=True)
class RuleStat:
    index: int
    field: str
    window: int | None
    op: str
    value: float
    on_null: str
    passed: int
    null: int
    passed_cumulative: int


@dataclass(frozen=True)
class ScreenResult:
    as_of: date
    evaluated: int
    rule_stats: tuple[RuleStat, ...]
    screened: tuple[str, ...]
    include_added: tuple[str, ...]
    excluded_removed: tuple[str, ...]
    pool: tuple[str, ...]
    include_ignored: tuple[str, ...] = ()


def _err(path: str, message: str) -> ScreenerSpecError:
    return ScreenerSpecError(f"{path}: {message}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounds_text(lo, hi) -> str:
    if hi is None:
        return f"must be >= {lo}"
    return f"must be within [{lo}, {hi}]"


def _parse_rule(path: str, obj) -> Rule:
    if not isinstance(obj, dict):
        raise _err(path, "must be an object")
    for key in obj:
        if key not in _RULE_KEYS:
            raise _err(path, f"unknown key {key!r}")
    field = obj.get("field")
    if field is None:
        raise _err(f"{path}.field", "required")
    if field not in FIELDS:
        raise _err(f"{path}.field", f"unknown field {field!r}")
    op = obj.get("op")
    if op not in OPS:
        raise _err(f"{path}.op", f"must be one of {', '.join(OPS)}")
    value = obj.get("value")
    if not _is_number(value) or not math.isfinite(value):
        raise _err(f"{path}.value", "must be a finite number")
    lo, hi = VALUE_BOUNDS[field]
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise _err(f"{path}.value", f"{_bounds_text(lo, hi)} for {field}")
    window_range = FIELDS[field]
    window = obj.get("window")
    if window_range is None:
        if "window" in obj:
            raise _err(f"{path}.window", f"not allowed for {field}")
    else:
        if window is None:
            raise _err(f"{path}.window", f"required for {field}")
        if type(window) is not int:
            raise _err(f"{path}.window", "must be an integer")
        if not window_range[0] <= window <= window_range[1]:
            raise _err(f"{path}.window",
                       f"{_bounds_text(*window_range)} for {field}")
    on_null = obj.get("on_null", "fail")
    if on_null not in _ON_NULL:
        raise _err(f"{path}.on_null", "must be 'fail' or 'pass'")
    return Rule(field, op, value, window, on_null)


def _parse_symbols(path: str, obj) -> tuple[str, ...]:
    if not isinstance(obj, list):
        raise _err(path, "must be a list")
    if len(obj) > MAX_LIST:
        raise _err(path, f"at most {MAX_LIST} symbols")
    seen: list[str] = []
    for i, sym in enumerate(obj):
        if not isinstance(sym, str) or not _SYMBOL_RE.match(sym):
            raise _err(f"{path}[{i}]", f"invalid symbol {sym!r}")
        if sym in seen:
            raise _err(path, f"duplicate symbol {sym!r}")
        seen.append(sym)
    return tuple(seen)


def _parse_asset_types(path: str, obj) -> tuple[str, ...]:
    if not isinstance(obj, list) or not obj:
        raise _err(path, "must be a non-empty list")
    seen: list[str] = []
    for i, t in enumerate(obj):
        if t not in config.ELIGIBLE_ASSET_TYPES:
            raise _err(f"{path}[{i}]", f"unknown asset type {t!r}")
        if t in seen:
            raise _err(path, f"duplicate asset type {t!r}")
        seen.append(t)
    return tuple(seen)


def parse_spec(obj) -> ScreenerSpec | None:
    """Validate a group's ``screener_json``. ``{}`` means the legacy v1 pool
    and returns None; anything else either parses completely or raises
    ScreenerSpecError naming the offending path. Symbols are never case-folded:
    a lowercase ticker is an error, not a guess."""
    root = "screener_json"
    if not isinstance(obj, dict):
        raise _err(root, "must be an object")
    if obj == {}:
        return None
    for key in obj:
        if key not in _TOP_KEYS:
            raise _err(root, f"unknown key {key!r}")
    rules_obj = obj.get("rules", [])
    if not isinstance(rules_obj, list):
        raise _err(f"{root}.rules", "must be a list")
    if len(rules_obj) > MAX_RULES:
        raise _err(f"{root}.rules", f"at most {MAX_RULES} rules")
    rules = tuple(_parse_rule(f"{root}.rules[{i}]", r) for i, r in enumerate(rules_obj))
    include = _parse_symbols(f"{root}.include", obj.get("include", []))
    exclude = _parse_symbols(f"{root}.exclude", obj.get("exclude", []))
    both = sorted(set(include) & set(exclude))
    if both:
        raise _err(root, f"{both[0]!r} is in both include and exclude")
    asset_types = (_parse_asset_types(f"{root}.asset_types", obj["asset_types"])
                   if "asset_types" in obj else config.ELIGIBLE_ASSET_TYPES)
    if not rules and not include:
        raise _err(root, "no rules and no include — selects nothing")
    return ScreenerSpec(rules, include, exclude, asset_types)


def required_lookback(spec: ScreenerSpec) -> int:
    """Sessions of history (ending at as_of) the spec's rules read."""
    need = 1
    for r in spec.rules:
        if r.field == "adv_usd":
            need = max(need, r.window)
        elif r.field == "ret_std":
            need = max(need, r.window + 1)
        elif r.field == "close_vs_ema":
            need = max(need, 4 * r.window)
    return need


def shares_as_of(facts: pd.DataFrame, splits: pd.DataFrame,
                 cik_by_code: dict[str, int], as_of: date) -> pd.Series:
    """Point-in-time shares outstanding per code.

    Per CIK, only facts FILED on or before as_of count; of those the latest
    period end wins (same end: the dei concept first, then the latest filing).
    A period end older than SHARES_STALE_DAYS is NULL. Splits with an ex-date
    after that period end (after the filing date for a us-gaap count, already
    restated to it) and on/before as_of scale the count by their ratio, so a
    1:10 reverse split divides it by ten. A count taken from a foreign
    private issuer's form (20-F / 6-K, amendments included) is NULL; a
    Canadian 40-F issuer lists its ordinary shares 1:1 and keeps its count. Returns
    float shares (NaN when unknown) indexed by every code in ``cik_by_code``.
    """
    out = pd.Series(np.nan, index=sorted(cik_by_code), dtype="float64")
    if facts is None or facts.empty or not cik_by_code:
        return out
    f = facts[pd.to_datetime(facts["filed"]).dt.date <= as_of].copy()
    if f.empty:
        return out
    f["end_d"] = pd.to_datetime(f["end_date"]).dt.date
    f["filed_d"] = pd.to_datetime(f["filed"]).dt.date
    rank = {c: i for i, c in enumerate(SHARE_CONCEPTS)}
    f["concept_rank"] = f["concept"].map(rank).fillna(len(rank))
    f = f.sort_values(["cik", "end_d", "concept_rank", "filed_d"],
                      ascending=[True, False, True, False])
    best = f.drop_duplicates("cik", keep="first").set_index("cik")
    stale_before = as_of - timedelta(days=config.SHARES_STALE_DAYS)
    by_symbol: dict[str, pd.DataFrame] = {}
    if splits is not None and not splits.empty:
        sp = splits.copy()
        sp["ex_d"] = pd.to_datetime(sp["ex_date"]).dt.date
        by_symbol = {s: g for s, g in sp.groupby("symbol")}
    for code, cik in cik_by_code.items():
        if cik is None or cik not in best.index:
            continue
        row = best.loc[cik]
        if row["end_d"] < stale_before:
            continue
        form = row.get("form")
        if isinstance(form, str) and form.split("/")[0] in FOREIGN_FORMS:
            continue
        shares = float(row["value"])
        # a us-gaap count comes from the financial statements, which restate it
        # for splits up to the filing date (SAB Topic 4C); the dei cover-page
        # count is as of its own date
        since = row["filed_d"] if str(row["concept"]).startswith("us-gaap:") else row["end_d"]
        g = by_symbol.get(code)
        if g is not None:
            hit = g[(g["ex_d"] > since) & (g["ex_d"] <= as_of)]
            for ratio in hit["ratio"]:
                if ratio:
                    shares *= float(ratio)
        out[code] = shares
    return out


def _window(frame: pd.DataFrame, n: int) -> pd.DataFrame | None:
    return frame.iloc[-n:] if len(frame) >= n else None


def compute_features(spec: ScreenerSpec, as_of: date, sessions, close: pd.DataFrame,
                     volume: pd.DataFrame, ret: pd.DataFrame, shares: pd.Series,
                     asset_type) -> pd.DataFrame:
    """One column per rule (``field`` or ``field:N``), one row per code.

    ``close`` / ``volume`` are RAW and ``ret`` is the adjusted simple daily
    return, all wide (index = session date, columns = code). Rows after as_of
    are ignored and missing sessions become NaN, so a window with a gap is
    NULL and appending future rows can never change the result.
    """
    days = [d for d in sessions if d <= as_of]
    codes = list(close.columns)
    close = close.reindex(index=days, columns=codes)
    volume = volume.reindex(index=days, columns=codes)
    ret = ret.reindex(index=days, columns=codes)
    types = pd.Series(asset_type).reindex(codes)
    has_last = bool(days) and days[-1] == as_of
    price = close.iloc[-1] if has_last else pd.Series(np.nan, index=codes)
    out = pd.DataFrame(index=pd.Index(codes, name="code"))
    for r in spec.rules:
        if r.key in out.columns:
            continue
        if r.field == "price":
            col = price
        elif r.field == "market_cap":
            col = shares.reindex(codes).astype("float64") * price
            col[types.isin(NO_MARKET_CAP_TYPES)] = np.nan
        elif r.field == "adv_usd":
            w = _window(close * volume, r.window) if has_last else None
            col = w.mean(skipna=False) if w is not None else pd.Series(np.nan, index=codes)
        elif r.field == "ret_std":
            c = _window(close, r.window + 1) if has_last else None
            if c is None:
                col = pd.Series(np.nan, index=codes)
            else:
                rw = ret.iloc[-r.window:]
                col = rw.std(ddof=1)
                col[c.isna().any() | rw.isna().any()] = np.nan
        else:
            n = 4 * r.window
            c = _window(close, n) if has_last else None
            if c is None:
                col = pd.Series(np.nan, index=codes)
            else:
                rw = ret.iloc[-(n - 1):]
                index = (1.0 + rw).cumprod()
                index = pd.concat([pd.DataFrame(1.0, index=[c.index[0]], columns=codes),
                                   index])
                ema = indicators.ema(index, r.window).iloc[-1]
                col = index.iloc[-1] / ema - 1.0
                col[c.isna().any() | rw.isna().any()] = np.nan
        out[r.key] = col.astype("float64")
    return out


def apply(spec: ScreenerSpec, features: pd.DataFrame, held=(),
          as_of: date | None = None) -> ScreenResult:
    """Run the rules over the feature frame and assemble the pool:
    (rule passers ∪ include − exclude) ∪ (held − exclude), sorted. A NULL
    feature passes only when its rule says ``on_null: "pass"``; with no rules
    nothing is screened in and the pool is the include list (plus held)."""
    codes = features.index
    mask = pd.Series(bool(spec.rules), index=codes)
    stats: list[RuleStat] = []
    for i, r in enumerate(spec.rules):
        vals = features[r.key]
        null = vals.isna()
        hit = _OP_FN[r.op](vals, r.value) & ~null
        if r.on_null == "pass":
            hit = hit | null
        mask &= hit
        stats.append(RuleStat(i, r.field, r.window, r.op, r.value, r.on_null,
                              int(hit.sum()), int(null.sum()), int(mask.sum())))
    screened = sorted(codes[mask.to_numpy()])
    exclude = set(spec.exclude)
    picked = set(screened) | set(spec.include)
    pool = sorted((picked - exclude) | (set(held) - exclude))
    return ScreenResult(
        as_of=as_of,
        evaluated=len(codes),
        rule_stats=tuple(stats),
        screened=tuple(screened),
        include_added=tuple(sorted(set(spec.include) - set(screened))),
        excluded_removed=tuple(sorted(set(screened) & exclude)),
        pool=tuple(pool),
    )
