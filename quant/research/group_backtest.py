"""Strategy-group backtests: one run or a walk-forward over a group's config.

A legacy group (``screener_json = {}``) trades the point-in-time S&P 500 like
r09, so its fixed stitched OOS can be checked against r09 in one process. A
screener group trades ``screen(previous_session(t))`` rebuilt from the panel
over today's listings (survivorship-biased by construction). Knob search
touches only funnel.* / exits.*: features are prepared once per run.

    python -m quant.research.group_backtest --parity-r09 --start 2016-01-01 --end 2026-07-29
    python -m quant.research.group_backtest --screener '<json>' --start .. --end .. --out DIR
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import sys
import warnings
from collections import Counter
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant import config
from quant.backtest import calibration, metrics, simulator
from quant.backtest.metrics import Trade
from quant.backtest.walkforward import walk_forward_windows
from quant.data import calendar, screen, securities, universe
from quant.data import sectors as sectorsmod
from quant.engine.screener import ScreenerSpec, parse_spec
from quant.engine.strategies import V1_COMPOSITE_KEY, V1Config, get_spec, validate_grid
from quant.research import r09

BACKTEST_MAX_SYMBOLS = 2500
SECTORS_MIN_KNOWN = 0.6
PLATFORM_CONSTANTS = {"per_trade_risk_pct": config.PER_TRADE_RISK_PCT,
                      "daily_loss_pause_pct": config.DAILY_LOSS_PAUSE_PCT,
                      "portfolio_drawdown_halt_pct": config.PORTFOLIO_DRAWDOWN_HALT_PCT,
                      "min_confidence": config.MIN_CONFIDENCE}
R09_FLAT_PARAMS = {"min_confidence": 65.0, "daily_loss_pause_pct": 0.02, "reversal_bars": 3,
                   "stagnation_bars": 30, "trailing_start_r": 1.5, "trailing_atr_mult": 3.0}


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class GroupConfig:
    strategy_key: str
    cfg: V1Config
    screener: ScreenerSpec | None
    starting_capital: float
    platform: dict

    @classmethod
    def from_snapshot(cls, snap: dict) -> GroupConfig:
        return cls(snap["strategy_key"], get_spec(snap["strategy_key"]).build(snap["params"]),
                   parse_spec(snap["screener"]), float(snap["starting_capital"]),
                   dict(snap["platform"]))


@dataclass(frozen=True)
class Universe:
    symbols: list
    sectors: dict
    membership_on: object
    etf_exempt: bool
    pool_held_exclude: frozenset | None
    label: str
    biased: bool
    info: dict
    pools: dict | None
    gaps: dict


def _day(d) -> date:
    return pd.Timestamp(d).date()


def sim_config(gcfg: GroupConfig, start, end, membership_on) -> simulator.SimConfig:
    p = gcfg.platform
    return simulator.SimConfig(
        start=str(start), end=str(end), starting_capital=gcfg.starting_capital,
        strategy=gcfg.cfg.strategy,
        funnel=replace(gcfg.cfg.funnel, min_confidence=max(
            gcfg.cfg.funnel.min_confidence, float(p["min_confidence"]))),
        exits=gcfg.cfg.exits,
        risk_pct=float(p["per_trade_risk_pct"]),
        daily_loss_pause_pct=float(p["daily_loss_pause_pct"]),
        drawdown_halt_pct=float(p["portfolio_drawdown_halt_pct"]),
        membership_on=membership_on)


def apply_knobs(gcfg: GroupConfig, base: simulator.SimConfig, knobs: dict, start, end
                ) -> simulator.SimConfig:
    funnel, exits = base.funnel, base.exits
    for knob, v in knobs.items():
        section, name = knob.split(".")
        if section == "funnel":
            funnel = replace(funnel, **{name: v})
        else:
            exits = replace(exits, **{name: v})
    funnel = replace(funnel, min_confidence=max(funnel.min_confidence,
                                                float(gcfg.platform["min_confidence"])))
    return replace(base, funnel=funnel, exits=exits, start=str(start), end=str(end))


def _knob_value(cfg, knob: str):
    section, name = knob.split(".")
    return getattr(getattr(cfg, section), name)


def prepare_universe(gcfg: GroupConfig, start, end, *, progress=lambda *_: None,
                     check=lambda: None) -> Universe:
    """Symbols, sectors and the entry gate a run trades under, with the
    run's universe facts for the report card. Refuses a universe the
    simulator would misjudge: too thin a sector cache, too many symbols, or
    no screenable session at all."""
    start, end = _day(start), _day(end)
    sectors = sectorsmod.load_sectors()
    if gcfg.screener is None:
        symbols = sorted(set(universe.all_symbols_in_range(start, end)) | set(config.ETF_WHITELIST))
        known = sum(1 for s in symbols if sectors.get(s, "unknown") != "unknown")
        if known < len(symbols) * SECTORS_MIN_KNOWN:
            raise RuntimeError(
                f"sector cache too thin ({known}/{len(symbols)}) — run python -m "
                f"quant.data.sectors first (silent 'unknown' collapse is the B3 bug)")
        info = {"label": "pit_sp500", "biased": False, "symbols": len(symbols),
                "sectors_known_pct": known / len(symbols) if symbols else None, "screen": None}
        return Universe(symbols, sectors, universe.constituents_set_on, True, None,
                        "pit_sp500", False, info, None, {})

    spec = gcfg.screener
    earliest = screen.earliest_start(spec)
    if earliest is None or start < earliest:
        raise RuntimeError(f"start {start} is before the first screenable session {earliest}")
    sessions = calendar.sessions_in_range(start, end)
    as_of_of = {t: calendar.previous_session(t) for t in sessions}
    as_ofs = sorted(set(as_of_of.values()))
    pools, gaps = screen.screen_history(spec, as_ofs, progress=progress, check=check)
    screened = [d for d, p in pools.items() if p is not None]
    if not screened:
        raise RuntimeError(f"no screenable session in range: gaps {dict(Counter(gaps.values()))}")
    symbols = sorted(set().union(*(pools[d] for d in screened)))
    if len(symbols) > BACKTEST_MAX_SYMBOLS:
        raise RuntimeError(f"universe too large: {len(symbols)} symbols > {BACKTEST_MAX_SYMBOLS}")
    types = securities.asset_types()
    stocks = [s for s in symbols if types.get(s) in ("stock", "adr")]
    known = sum(1 for s in stocks if sectors.get(s, "unknown") != "unknown")
    pct = known / len(stocks) if stocks else None
    if pct is not None and pct < SECTORS_MIN_KNOWN:
        raise RuntimeError(f"sector cache too thin: {known}/{len(stocks)} stock+adr symbols "
                           f"known — run the P3b Step D sector backfill")
    coverage = screen.session_coverage()
    covs = [coverage[d] for d in screened if d in coverage]
    info = {"label": "screener_current_universe", "biased": True, "symbols": len(symbols),
            "sectors_known_pct": pct,
            "screen": {"as_ofs": len(as_ofs),
                       "gaps": {r: sum(1 for g in gaps.values() if g == r)
                                for r in screen.GAP_REASONS},
                       "coverage_min": min(covs) if covs else None,
                       "first_as_of": str(as_ofs[0]), "last_as_of": str(as_ofs[-1])}}
    return Universe(symbols, sectors, lambda prev: pools[as_of_of[prev]], False,
                    frozenset(spec.exclude), "screener_current_universe", True, info,
                    pools, gaps)


def prepare_features(gcfg: GroupConfig, uni: Universe, start, end, *,
                     progress=lambda *_: None) -> dict[str, pd.DataFrame]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return simulator.prepare_features(uni.symbols, uni.sectors,
                                          sim_config(gcfg, start, end, uni.membership_on),
                                          progress=progress)


def _run(uni: Universe, feats: dict, cfg: simulator.SimConfig, check) -> simulator.SimResult:
    check()
    return simulator.run(list(feats), uni.sectors, cfg, features=feats,
                         etf_exempt=uni.etf_exempt, pool_held_exclude=uni.pool_held_exclude)


def stitch(equities: list[pd.Series], starting_capital: float) -> pd.Series:
    rets = pd.concat([e.pct_change().dropna() for e in equities])
    head = pd.Series([starting_capital], index=[equities[0].index[0]])
    return pd.concat([head, starting_capital * (1.0 + rets).cumprod()])


def fixed_oos(gcfg: GroupConfig, uni: Universe, feats: dict, base_cfg: simulator.SimConfig,
              windows, *, check=lambda: None, progress=lambda *_: None
              ) -> tuple[pd.Series, list[Trade], list[dict]]:
    equities, trades, rows = [], [], []
    for wi, win in enumerate(windows, 1):
        oos = r09.slice_features(feats, win.oos_start, win.oos_end)
        cfg = replace(base_cfg, start=str(win.oos_start.date()), end=str(win.oos_end.date()))
        res = _run(uni, oos, cfg, check)
        equities.append(res.equity)
        trades += res.trades
        rows.append({"window": wi, "oos": f"{win.oos_start.date()}..{win.oos_end.date()}",
                     "summary": metrics.payload(res.equity, res.trades)})
        progress(f"fixed OOS window {wi}/{len(windows)}: {len(res.trades)} trades")
    return stitch(equities, gcfg.starting_capital), trades, rows


def run_single(gcfg: GroupConfig, start, end, *, check=lambda: None,
               progress=lambda *_: None) -> dict:
    uni = prepare_universe(gcfg, start, end, progress=progress, check=check)
    feats = prepare_features(gcfg, uni, start, end, progress=progress)
    res = _run(uni, feats, sim_config(gcfg, start, end, uni.membership_on), check)
    return {"kind": "single", "universe": uni, "equity": res.equity, "trades": res.trades,
            "metrics": {"summary": metrics.payload(res.equity, res.trades),
                        "universe": uni.info}}


def _calibrate_window(gcfg, uni, feats, base, win, grid, check) -> tuple[dict, dict]:
    """r09's single-pass coordinate descent on the IS slice, started from the
    group's own knob values. The cache key is the knob values AFTER the
    platform floor, so grid points the floor makes identical run once."""
    is_feats = r09.slice_features(feats, win.is_start, win.is_end)
    cache: dict[tuple, float] = {}

    def evaluate(kv: dict) -> float:
        cfg = apply_knobs(gcfg, base, kv, win.is_start.date(), win.is_end.date())
        key = tuple(_knob_value(cfg, k) for k in grid)
        if key not in cache:
            cache[key] = r09.objective(_run(uni, is_feats, cfg, check))
        return cache[key]

    current = {k: _knob_value(gcfg.cfg, k) for k in grid}
    best = evaluate(current)
    trail = {"start_score": best, "steps": []}
    for knob, values in grid.items():
        for v in values:
            if v == current[knob]:
                continue
            trial = {**current, knob: v}
            score = evaluate(trial)
            if score > best:
                best, current = score, trial
        trail["steps"].append({knob: current[knob], "score": best})
    trail["end_score"] = best
    trail["runs"] = len(cache)
    return current, trail


def _entry_score(feats: dict, t: Trade) -> float | None:
    f = feats.get(t.symbol)
    if f is None:
        return None
    before = f.loc[f["date"] < t.entry_date, "confidence"]
    return float(before.iloc[-1]) if len(before) else None


def run_walk_forward(gcfg: GroupConfig, start, end, *, is_years: int, oos_years: int,
                     grid=None, check=lambda: None, progress=lambda *_: None) -> dict:
    windows = walk_forward_windows(start, end, is_years=is_years, oos_years=oos_years,
                                   step_years=oos_years)
    if not windows:
        raise ValueError(f"no walk-forward window fits {start}..{end} "
                         f"(IS {is_years}y + OOS {oos_years}y)")
    grid = validate_grid(gcfg.strategy_key, grid)
    uni = prepare_universe(gcfg, start, end, progress=progress, check=check)
    feats = prepare_features(gcfg, uni, start, end, progress=progress)
    base = sim_config(gcfg, start, end, uni.membership_on)

    sel_equities, sel_trades, rows = [], [], []
    for wi, win in enumerate(windows, 1):
        params, trail = _calibrate_window(gcfg, uni, feats, base, win, grid, check)
        oos = r09.slice_features(feats, win.oos_start, win.oos_end)
        res = _run(uni, oos, apply_knobs(gcfg, base, params, win.oos_start.date(),
                                         win.oos_end.date()), check)
        sel_equities.append(res.equity)
        sel_trades += res.trades
        rows.append({"window": wi, "is": f"{win.is_start.date()}..{win.is_end.date()}",
                     "oos": f"{win.oos_start.date()}..{win.oos_end.date()}",
                     "params": params, "is_trail": trail,
                     "selected": metrics.payload(res.equity, res.trades)})
        progress(f"window {wi}/{len(windows)}: selected {params}")
    fixed_equity, fixed_trades, fixed_rows = fixed_oos(gcfg, uni, feats, base, windows,
                                                       check=check, progress=progress)
    for row, fixed in zip(rows, fixed_rows):
        row["fixed"] = fixed["summary"]
    selected_equity = stitch(sel_equities, gcfg.starting_capital)

    finals = [r["fixed"]["final_equity"] for r in rows]
    pfs = [math.inf if r["fixed"]["profit_factor"] is None else r["fixed"]["profit_factor"]
           for r in rows]
    gate = {"windows_positive_pct": sum(1 for f in finals if f > gcfg.starting_capital) / len(rows),
            "window_pf_median": float(np.median(pfs))}

    etfs = set(config.ETF_WHITELIST)
    scores, wins, window_ids, skipped = [], [], [], 0
    for t in fixed_trades:
        if t.symbol in etfs:
            continue
        s = _entry_score(feats, t)
        if s is None:
            skipped += 1
            continue
        wi = next(i for i, w in enumerate(windows, 1)
                  if w.oos_start.date() <= t.entry_date <= w.oos_end.date())
        scores.append(s)
        wins.append(t.pnl > 0)
        window_ids.append(wi)
    cal = calibration.fit(scores, wins, window_ids)
    cal["skipped_no_score"] = skipped

    return {"kind": "walk_forward", "universe": uni,
            "equity_fixed": fixed_equity, "equity_selected": selected_equity,
            "trades_fixed": fixed_trades, "trades_selected": sel_trades,
            "metrics": {"fixed": metrics.payload(fixed_equity, fixed_trades),
                        "selected": metrics.payload(selected_equity, sel_trades),
                        "windows": rows, "gate": gate, "universe": uni.info},
            "calibration": cal}


def jsonable(obj):
    if isinstance(obj, dict):
        return {(k.isoformat() if isinstance(k, (date, datetime)) else k): jsonable(v)
                for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return [jsonable(v) for v in sorted(obj)]
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(jsonable(obj), allow_nan=False))


def _equity_csv(path: Path, equity: pd.Series) -> None:
    equity.rename("equity").rename_axis("date").to_csv(path)


def _trades_csv(path: Path, trades: list[Trade]) -> None:
    pd.DataFrame([asdict(t) for t in trades],
                 columns=[f.name for f in fields(Trade)]).to_csv(path, index=False)


def write_artifacts(dirpath, result: dict) -> None:
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=False)
    if result["kind"] == "single":
        _equity_csv(d / "equity.csv", result["equity"])
        _trades_csv(d / "trades.csv", result["trades"])
    else:
        _equity_csv(d / "equity_fixed.csv", result["equity_fixed"])
        _equity_csv(d / "equity_selected.csv", result["equity_selected"])
        _trades_csv(d / "trades_fixed.csv", result["trades_fixed"])
        _trades_csv(d / "trades_selected.csv", result["trades_selected"])
        _dump(d / "windows.json", result["metrics"]["windows"])
        _dump(d / "calibration.json", result["calibration"])
    uni = result["universe"]
    if uni.pools is not None:
        rows = [(a, s) for a, p in sorted(uni.pools.items()) if p is not None for s in sorted(p)]
        pd.DataFrame(rows, columns=["as_of", "symbol"]).to_parquet(d / "pools.parquet",
                                                                   index=False)
        _dump(d / "screen_gaps.json", uni.gaps)


def _parity(start: str, end: str) -> int:
    feats, sectors, base = r09.fixed_oos_inputs(start, end, progress=print)
    theirs = r09.stitched_run(feats, sectors, base, R09_FLAT_PARAMS)[0]
    gcfg = GroupConfig(V1_COMPOSITE_KEY, get_spec(V1_COMPOSITE_KEY).defaults, None, 2000.0,
                       PLATFORM_CONSTANTS)
    uni = Universe(sorted(feats), sectors, base.membership_on, True, None, "pit_sp500", False,
                   {}, None, {})
    windows = walk_forward_windows(start, end, is_years=3, oos_years=1, step_years=1)
    ours_cfg = sim_config(gcfg, start, end, base.membership_on)
    w1 = windows[0]
    a = asdict(replace(ours_cfg, start=str(w1.oos_start.date()), end=str(w1.oos_end.date())))
    b = asdict(r09.with_knobs(base, R09_FLAT_PARAMS, w1.oos_start.date(), w1.oos_end.date()))
    a.pop("membership_on")
    b.pop("membership_on")
    if a != b:
        print(f"CONFIG_DIFF {json.dumps(jsonable({'ours': a, 'r09': b}), default=str)}")
        return 1
    print("CONFIG_OK")
    stitched, trades, _ = fixed_oos(gcfg, uni, feats, ours_cfg, windows, progress=print)
    ours = metrics.summary(stitched, trades)
    diff = {k: (ours.get(k), v) for k, v in theirs.items()
            if not (ours.get(k) == v or abs(ours.get(k) - v) <= 1e-12 * max(1, abs(v)))}
    if diff or set(ours) != set(theirs):
        print(f"PARITY DIFF {diff} keys ours-only {sorted(set(ours) - set(theirs))}")
        return 1
    print(f"PARITY OK {json.dumps(jsonable(ours))}")
    return 0


def _screener_smoke(spec_json: str, start: str, end: str, out: str) -> int:
    gcfg = GroupConfig(V1_COMPOSITE_KEY, get_spec(V1_COMPOSITE_KEY).defaults,
                       parse_spec(json.loads(spec_json)), 2000.0, PLATFORM_CONSTANTS)
    result = run_single(gcfg, start, end, progress=lambda m: print(m, flush=True))
    print(f"SCREEN {json.dumps(jsonable(result['universe'].info), allow_nan=False)}")
    print(f"SUMMARY {json.dumps(jsonable(result['metrics']['summary']), allow_nan=False)}")
    print(f"MAXRSS_MB {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}")
    write_artifacts(out, result)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Strategy-group backtests")
    ap.add_argument("--parity-r09", action="store_true")
    ap.add_argument("--screener", default=None, help="screener_json as a JSON string")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        if args.parity_r09:
            return _parity(args.start, args.end)
        if args.screener is not None and args.out:
            return _screener_smoke(args.screener, args.start, args.end, args.out)
        ap.error("pass --parity-r09, or --screener with --out")
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
