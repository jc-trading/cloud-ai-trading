"""R0-9: calibration + full-universe point-in-time walk-forward + scoreboard.

    python -m quant.research.r09          # the real run (~1-2h wall clock)
    python -m quant.research.r09 --quick  # small smoke (subset, 2 windows)

Anti-overfit discipline (plan §11 / execution manual §R0-9):
  - features (indicator params [C2]) are FIXED — only C1/C3/C5/C6/C8 knobs move
  - single-pass coordinate descent from the design defaults, coarse 3-value
    grids: few degrees of freedom by construction
  - calibration sees only each window's IN-SAMPLE; the OUT-OF-SAMPLE result is
    the judge and is never used to pick anything
  - entries are gated by point-in-time S&P500 membership (survivorship-free)
  - C7 (pyramid) is NOT calibrated: A7 fixes max adds at 1 by 拍板
Outputs land in cat-data/r09/ (results.json + trades/equity CSVs); the G1
report is written from those files, judged on recommendation quality first
(Direction v3) and the §11 portfolio gates second.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd

from quant import config
from quant.backtest import metrics, simulator
from quant.backtest.walkforward import walk_forward_windows
from quant.data import sectors as sectorsmod
from quant.data import universe
from quant.engine.exits import ExitParams
from quant.research import quality

START = "2016-01-01"
END = "2026-07-29"          # last closed session before this run
OUT_DIR = config.DATA_ROOT / "r09"

DEFAULTS = {
    "min_confidence": 0.0,        # [C1]
    "daily_loss_pause_pct": 0.02, # [C3]
    "reversal_bars": 3,           # [C5]
    "stagnation_bars": 30,        # [C6]
    "trailing_start_r": 1.5,      # [C8]
    "trailing_atr_mult": 3.0,     # [C8]
}
KNOB_GRID = {
    "min_confidence": [0.0, 50.0, 65.0],
    "daily_loss_pause_pct": [0.015, 0.02, 0.03],
    "reversal_bars": [2, 3, 5],
    "stagnation_bars": [20, 30, 45],
    "trailing_start_r": [1.0, 1.5, 2.0],
    "trailing_atr_mult": [2.0, 3.0, 4.0],
}


def log(msg: str) -> None:
    print(f"[r09 {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def with_knobs(base: simulator.SimConfig, kv: dict, start, end) -> simulator.SimConfig:
    return replace(
        base,
        start=str(start), end=str(end),
        funnel=replace(base.funnel, min_confidence=kv["min_confidence"]),
        exits=ExitParams(trailing_start_r=kv["trailing_start_r"],
                         trailing_atr_mult=kv["trailing_atr_mult"],
                         reversal_bars=kv["reversal_bars"],
                         stagnation_bars=kv["stagnation_bars"]),
        daily_loss_pause_pct=kv["daily_loss_pause_pct"],
    )


def slice_features(feats: dict[str, pd.DataFrame], start, end) -> dict[str, pd.DataFrame]:
    a = pd.Timestamp(start).date()
    b = pd.Timestamp(end).date()
    out = {}
    for s, f in feats.items():
        g = f[(f["date"] >= a) & (f["date"] <= b)]
        if len(g) > 10:
            out[s] = g.reset_index(drop=True)
    return out


def objective(res: simulator.SimResult) -> float:
    """IS calibration score: return-over-maxDD (capped), penalized when the
    window produced too few trades to mean anything."""
    s = metrics.summary(res.equity, res.trades)
    calmar = min(s["return_over_maxdd"], 10.0)
    n = s["num_trades"]
    return calmar * (1.0 if n >= 15 else n / 15.0)


def calibrate_window(feats, sectors, base_cfg, win, *, grid=KNOB_GRID) -> tuple[dict, dict]:
    """Single-pass coordinate descent on the IS slice. Returns (params, log)."""
    is_feats = slice_features(feats, win.is_start, win.is_end)
    cache: dict[tuple, float] = {}

    def evaluate(kv: dict) -> float:
        key = tuple(sorted(kv.items()))
        if key not in cache:
            cfg = with_knobs(base_cfg, kv, win.is_start.date(), win.is_end.date())
            res = simulator.run(list(is_feats), sectors, cfg, features=is_feats)
            cache[key] = objective(res)
        return cache[key]

    current = dict(DEFAULTS)
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


def recommend(chosen: list[dict]) -> dict:
    """Across-window consensus: mode, ties -> median (stability over cleverness)."""
    out = {}
    for k in DEFAULTS:
        vals = [c[k] for c in chosen]
        counts = pd.Series(vals).value_counts()
        top = counts[counts == counts.max()].index.tolist()
        out[k] = sorted(top)[len(top) // 2] if len(top) > 1 else top[0]
    return out


def main(quick: bool = False) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1) universe + sectors -------------------------------------------------
    syms = sorted(set(universe.all_symbols_in_range(START, END)) | set(config.ETF_WHITELIST))
    sectors = sectorsmod.load_sectors()
    known = sum(1 for s in syms if sectors.get(s, "unknown") != "unknown")
    log(f"universe {len(syms)} symbols; sectors known for {known}")
    if not quick and known < len(syms) * 0.6:
        raise RuntimeError(
            f"sector cache too thin ({known}/{len(syms)}) — run python -m "
            f"quant.data.sectors first (silent 'unknown' collapse is the B3 bug)")
    if quick:
        syms = syms[:80] + [s for s in config.ETF_WHITELIST if s not in syms[:80]]

    # 2) features once, warnings captured -----------------------------------
    base_cfg = simulator.SimConfig(
        start=START, end=END,
        membership_on=lambda d: universe.constituents_set_on(d))
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        feats = simulator.prepare_features(syms, sectors, base_cfg, progress=log)
    adj_warned = sorted({str(w.message).split(":")[0] for w in wlist
                         if "actions likely not synced" in str(w.message)})
    log(f"features for {len(feats)} symbols; adjustment warnings on {len(adj_warned)}")

    # 3) walk-forward calibration + OOS judgment ----------------------------
    windows = walk_forward_windows(START, END, is_years=3, oos_years=1, step_years=1)
    if quick:
        windows = windows[:2]
    chosen_list, window_rows, oos_trades = [], [], []
    for wi, win in enumerate(windows, 1):
        log(f"window {wi}/{len(windows)} IS {win.is_start.date()}..{win.is_end.date()} "
            f"OOS {win.oos_start.date()}..{win.oos_end.date()}: calibrating")
        params, trail = calibrate_window(feats, sectors, base_cfg, win)
        oos_feats = slice_features(feats, win.oos_start, win.oos_end)
        cfg = with_knobs(base_cfg, params, win.oos_start.date(), win.oos_end.date())
        res = simulator.run(list(oos_feats), sectors, cfg, features=oos_feats)
        s = metrics.summary(res.equity, res.trades, res.benchmark)
        window_rows.append({"window": wi, "is": f"{win.is_start.date()}..{win.is_end.date()}",
                            "oos": f"{win.oos_start.date()}..{win.oos_end.date()}",
                            "params": params, "calibration": trail, "oos_summary": s})
        chosen_list.append(params)
        oos_trades += res.trades
        log(f"window {wi}: OOS sharpe {s['sharpe']:.2f} vs SPY {s.get('spy_sharpe', 0):.2f}, "
            f"trades {s['num_trades']}, PF {s['profit_factor']:.2f}")

    rec = recommend(chosen_list)
    log(f"recommended params: {rec}")

    # 4) full-span run with recommended params (ILLUSTRATIVE — overlaps IS) --
    cfg = with_knobs(base_cfg, rec, pd.Timestamp(START).date(), pd.Timestamp(END).date())
    final = simulator.run(list(feats), sectors, cfg, features=feats, progress=log)
    final_summary = metrics.summary(final.equity, final.trades, final.benchmark)
    log(f"full-span (illustrative): final ${final_summary['final_equity']:,.0f}, "
        f"sharpe {final_summary['sharpe']:.2f}, trades {final_summary['num_trades']}")

    # 5) recommendation quality (v3): phase + zones -------------------------
    qsyms = syms[:60] if quick else syms
    # two horizons: 20d catches mean-reversion effects, 60d the trend-holding
    # horizon the strategy actually trades (the smoke showed 20d inverts)
    pa = quality.phase_accuracy(qsyms, START, END, horizon=20, progress=log)
    pa60 = quality.phase_accuracy(qsyms, START, END, horizon=60, progress=log)
    zq = quality.zone_quality(qsyms, START, END, progress=log)
    log(f"phase separation up-down median fwd: 20d {pa['separation_median']:+.3%} "
        f"/ 60d {pa60['separation_median']:+.3%}")

    # 6) persist ------------------------------------------------------------
    oos_agg = {
        "num_trades": len(oos_trades),
        "win_rate": metrics.win_rate(oos_trades),
        "profit_factor": metrics.profit_factor(oos_trades),
        "avg_r": metrics.avg_r(oos_trades),
    }
    results = {
        "generated_for": {"start": START, "end": END, "quick": quick,
                          "universe_size": len(syms), "features_built": len(feats),
                          "sectors_known": known,
                          "adjustment_warnings_symbols": adj_warned},
        "defaults": DEFAULTS, "grid": KNOB_GRID,
        "windows": window_rows,
        "oos_aggregate": oos_agg,
        "recommended_params": rec,
        "full_span_illustrative": final_summary,
        "phase_accuracy_20d": pa,
        "phase_accuracy_60d": pa60,
        "zone_quality": zq,
        "wall_clock_seconds": round(time.time() - t0, 1),
    }
    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, default=str))
    pd.DataFrame([t.__dict__ for t in oos_trades]).to_csv(OUT_DIR / "oos_trades.csv", index=False)
    pd.DataFrame([t.__dict__ for t in final.trades]).to_csv(OUT_DIR / "final_trades.csv", index=False)
    final.equity.rename("equity").to_csv(OUT_DIR / "final_equity.csv")
    log(f"done in {results['wall_clock_seconds']}s -> {OUT_DIR}/results.json")


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
