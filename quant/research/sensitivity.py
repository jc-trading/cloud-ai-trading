"""D2: backtest perturbation / sensitivity analysis of the fixed-OOS scoreboard.

Is PF ~0.93 a property of the strategy, or of one lucky data path? Every
perturbation re-runs r09's fixed-OOS pipeline unchanged and moves only what it
reads: a shim over ``quant.data.bars.get_bars`` (price noise) or over
``corporate_actions.load_actions`` (a missing action). The simulator, the
engine and the stitch are never touched.

    python -m quant.research.sensitivity --n 5 --workers 1      # smoke
    python -m quant.research.sensitivity --workers 3            # full run

Outputs cat-data/sensitivity/{results.json,report.md}.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sqlite3
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

from quant import config
from quant.backtest import metrics
from quant.data import bars as barsmod
from quant.data import calendar
from quant.data import corporate_actions
from quant.data import store
from quant.research import r09

OUT_DIR = config.DATA_ROOT / "sensitivity"

# per-kind seed bases keep 1bp and 5bp noise from drawing the same factors
KINDS: dict[str, dict] = {
    "noise_1bp": {"n": 100, "bps": 1.0, "seed_base": 1_000},
    "noise_5bp": {"n": 100, "bps": 5.0, "seed_base": 2_000},
    # 10, not 50: a rescale of pre-ex-date bars cancels out of scale-invariant
    # signals AND risk-based sizing, so the kind is a weak probe by construction
    # (see the report's "Reading this") — 10 runs is enough to show that.
    "drop_action": {"n": 10, "seed_base": 3_000},
    # interleaved from the smallest |k| outward so an --n cap truncates
    # symmetrically instead of keeping only the backward shifts
    "start_shift": {"n": 8, "shifts": (-1, 1, -2, 2, -3, 3, -5, 5)},
    "bootstrap": {"n": 2000, "block": 10, "seed_base": 4_000},
}
SIM_KINDS = ("noise_1bp", "noise_5bp", "drop_action", "start_shift")
# check name -> (metric, threshold the p5 must clear)
VERDICT_CHECKS = {
    "p5_profit_factor_gt_1": ("profit_factor", 1.0),
    "p5_cagr_gt_0": ("cagr", 0.0),
    "p5_avg_r_gt_0": ("avg_r", 0.0),
}
VERDICT_METRICS = tuple(field for field, _ in VERDICT_CHECKS.values())
_DAILY_TF = ("1d", "1day", "d", "daily")


def log(msg: str) -> None:
    print(f"[sens {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --- pure perturbation primitives -----------------------------------------

def perturb_bars(df: pd.DataFrame, rng: np.random.Generator, bps: float) -> pd.DataFrame:
    """Apply ONE multiplicative draw per bar to open/high/low/close, so the
    intra-bar invariants (high >= open/close >= low) survive by construction.
    Volume and vwap are left alone: this models quote noise, not a different
    tape, and ``attrs['unadjusted']`` is passed through so the RAW-price guard
    still fails closed on the same symbols."""
    out = df.copy()
    out.attrs.update(df.attrs)
    if df.empty or bps <= 0:
        return out
    factor = 1.0 + rng.normal(0.0, bps * 1e-4, size=len(out))
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype=float) * factor
    return out


def make_bars_shim(seed: int, bps: float, *, real_get_bars=None):
    """get_bars replacement adding per-bar price noise, daily timeframes only.

    Two properties make a run reproducible. The rng is derived from
    (seed, crc32(symbol)), so a symbol's noise does not depend on the order
    symbols are read in. And the real read is always UNWINDOWED — the noise is
    applied by position on the full history, then the frame is sliced to the
    caller's window — so a windowed read returns exactly the rows the full read
    would. Without that, the SPY benchmark (read with a different `end` per
    walk-forward window) would see a different tape in each one."""
    real = real_get_bars if real_get_bars is not None else barsmod.get_bars

    def shim(symbol, timeframe="1d", start=None, end=None, **kw):
        if str(timeframe).lower() not in _DAILY_TF:
            raise ValueError(
                f"sensitivity shim is daily-only; got timeframe {timeframe!r}. "
                "Perturbing a windowed intraday read would make the noise "
                "depend on the window that asked for it.")
        df = real(symbol, timeframe, None, None, **kw)
        rng = np.random.default_rng([seed, zlib.crc32(symbol.encode())])
        out = perturb_bars(df, rng, bps)
        sliced = store.slice_range(out, start, end)
        sliced.attrs.update(out.attrs)
        return sliced

    return shim


def load_all_actions() -> pd.DataFrame:
    """Every cached corporate action — the pool the drop-one perturbation
    samples from. Read-only: the sensitivity run must never touch the cache."""
    cols = ["symbol", "ex_date", "action_type"]
    if not config.ACTIONS_DB.exists():
        return pd.DataFrame(columns=cols)
    conn = sqlite3.connect(f"file:{config.ACTIONS_DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(
            "SELECT symbol, ex_date, action_type FROM corporate_actions "
            "ORDER BY symbol, ex_date, action_type", conn)
    finally:
        conn.close()


def _action_row(row) -> dict:
    return {k: str(row[k]) for k in ("symbol", "ex_date", "action_type")}


def informative_actions(pool: pd.DataFrame, base_trades: list) -> pd.DataFrame:
    """Keep only the actions that CAN move a result. adjust() back-scales the
    bars strictly BEFORE an ex-date, so dropping an action almost never moves
    anything unless the symbol already traded before it — on the live cache
    that is ~16 of 126 splits, and sampling the raw pool spends the sweep
    confirming that a random action changes nothing."""
    if pool.empty or not base_trades:
        return pool.iloc[0:0].reset_index(drop=True)
    first: dict[str, object] = {}
    for t in base_trades:
        if t.symbol not in first or t.entry_date < first[t.symbol]:
            first[t.symbol] = t.entry_date
    ex = pd.to_datetime(pool["ex_date"]).dt.date
    keep = np.array([s in first and e > first[s] for s, e in zip(pool["symbol"], ex)])
    return pool[keep].reset_index(drop=True)


def _kind_counts(pool: pd.DataFrame) -> dict:
    counts = {k: int((pool["action_type"] == k).sum()) for k in ("split", "dividend")}
    return {**counts, "total": int(len(pool))}


def stratify_action_targets(pool: pd.DataFrame, n: int, seed: int) -> list[dict]:
    """Draw n distinct actions to drop, half splits (rounded up) and half
    dividends. Uniform sampling would be ~99% dividends — the cache holds ~150x
    more of them — so a whole sweep would almost never test the missing-SPLIT
    case this perturbation exists for. A stratum shorter than its quota hands
    the remainder to the other one."""
    if pool.empty or n <= 0:
        return []
    rng = np.random.default_rng(seed)
    want = {"split": (n + 1) // 2, "dividend": n // 2}
    frames = {k: pool[pool["action_type"] == k] for k in want}
    for a, b in (("split", "dividend"), ("dividend", "split")):
        short = max(0, want[a] - len(frames[a]))
        want[a] -= short
        want[b] += short
    out: list[dict] = []
    for kind in ("split", "dividend"):
        frame = frames[kind]
        take = min(want[kind], len(frame))
        if take <= 0:
            continue
        picked = rng.choice(len(frame), size=take, replace=False)
        out += [_action_row(frame.iloc[int(i)]) for i in picked]
    return out


def make_actions_shim(target: dict | None, *, real_load_actions=None):
    """load_actions replacement that hides exactly ONE cached action — the "we
    missed a split" failure mode, which back-scales every bar before that
    ex-date for that symbol."""
    real = real_load_actions if real_load_actions is not None else corporate_actions.load_actions

    def shim(symbol, **kw):
        df = real(symbol, **kw)
        if target is None or df.empty or symbol.upper() != target["symbol"].upper():
            return df
        hit = ((df["ex_date"].astype(str) == target["ex_date"])
               & (df["action_type"] == target["action_type"]))
        return df[~hit].reset_index(drop=True)

    return shim


def shift_sessions(d: date | str, k: int) -> date:
    """Move a date k TRADING sessions forward (k>0) or back (k<0) — a weekend
    or holiday must not eat the shift."""
    out = pd.Timestamp(d).date()
    if not calendar.is_trading_day(out):
        # snap onto the calendar FIRST: next_session() on a holiday returns that
        # holiday's own following session, which would silently eat the +1 step
        # (r09.START = 2016-01-01 is New Year's Day)
        out = calendar.next_session(out)
    step = calendar.next_session if k > 0 else calendar.previous_session
    for _ in range(abs(k)):
        out = step(out)
    return out


def block_bootstrap(trades: list, rng: np.random.Generator, *, block: int = 10) -> list:
    """One circular block resample of a trade sequence — blocks rather than
    single trades so win/loss streaks and their clustering survive."""
    n = len(trades)
    if n == 0:
        return []
    out: list = []
    while len(out) < n:
        i = int(rng.integers(n))
        out += [trades[(i + j) % n] for j in range(min(block, n - len(out)))]
    return out


def summarise_distribution(values) -> dict:
    """median / p5 / p95 of one metric across runs. Non-finite results (an
    all-winners profit factor is +inf) are dropped and counted, never silently
    turned into a number."""
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    finite = arr[np.isfinite(arr)]
    out = {"n": int(finite.size), "dropped_non_finite": int(arr.size - finite.size)}
    if finite.size == 0:
        return {**out, "median": None, "p5": None, "p95": None, "min": None, "max": None}
    return {
        **out,
        "median": float(np.median(finite)),
        "p5": float(np.percentile(finite, 5)),
        "p95": float(np.percentile(finite, 95)),
        "min": float(finite.min()),
        "max": float(finite.max()),
    }


def trade_metrics(trades: list) -> dict:
    return {
        "profit_factor": metrics.profit_factor(trades),
        "win_rate": metrics.win_rate(trades),
        "avg_r": metrics.avg_r(trades),
        "num_trades": len(trades),
    }


# --- driver ---------------------------------------------------------------

def build_jobs(kinds: list[str], params: dict, n_cap: int | None, *,
               action_pool: pd.DataFrame | None = None) -> list[dict]:
    pool = action_pool if action_pool is not None else (
        load_all_actions() if "drop_action" in kinds else pd.DataFrame())
    jobs: list[dict] = []
    for kind in kinds:
        spec = KINDS[kind]
        n = spec["n"] if n_cap is None else min(n_cap, spec["n"])
        if kind == "drop_action":
            for i, target in enumerate(stratify_action_targets(pool, n, spec["seed_base"])):
                jobs.append({"kind": kind, "params": params,
                             "seed": spec["seed_base"] + i, "target": target})
            continue
        for i in range(n):
            job = {"kind": kind, "params": params, "seed": spec.get("seed_base", 0) + i}
            if kind.startswith("noise"):
                job["bps"] = spec["bps"]
            elif kind == "start_shift":
                job["k"] = spec["shifts"][i]
                job["seed"] = spec["shifts"][i]
            jobs.append(job)
    return jobs


def run_one(job: dict) -> dict:
    """Execute one perturbed fixed-OOS run. Shims are installed for the whole
    run, so feature building AND the SPY benchmark read the perturbed tape."""
    kind, seed = job["kind"], job["seed"]
    start, detail, patches = r09.START, {}, []
    if kind.startswith("noise"):
        detail = {"bps": job["bps"]}
        patches.append(mock.patch.object(
            barsmod, "get_bars", make_bars_shim(seed, job["bps"])))
    elif kind == "drop_action":
        target = job["target"]
        detail = {"action_kind": target["action_type"] if target else None,
                  "symbol": target["symbol"] if target else None,
                  "ex_date": target["ex_date"] if target else None}
        patches.append(mock.patch.object(
            corporate_actions, "load_actions", make_actions_shim(target)))
    elif kind == "start_shift":
        start = shift_sessions(r09.START, job["k"]).isoformat()
        detail = {"sessions": job["k"], "start": start}

    t0 = time.time()
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        feats, sectors, base_cfg = r09.fixed_oos_inputs(start=start)
        summary, _, _ = r09.stitched_run(feats, sectors, base_cfg, job["params"])
    return {"kind": kind, "seed": seed, "params": detail,
            "summary": summary, "seconds": round(time.time() - t0, 1)}


def run_perturbations(jobs: list[dict], workers: int) -> list[dict]:
    if workers <= 1:
        out = []
        for i, job in enumerate(jobs, 1):
            out.append(run_one(job))
            log(f"{i}/{len(jobs)} {job['kind']} seed {job['seed']}: "
                f"PF {out[-1]['summary']['profit_factor']:.3f}")
        return out
    out = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(run_one, jobs), 1):
            out.append(res)
            log(f"{i}/{len(jobs)} {res['kind']} seed {res['seed']}: "
                f"PF {res['summary']['profit_factor']:.3f}")
    return out


def run_bootstrap(trades: list, n: int, block: int, seed_base: int) -> list[dict]:
    out = []
    for i in range(n):
        rng = np.random.default_rng(seed_base + i)
        out.append({"kind": "bootstrap", "seed": seed_base + i, "params": {"block": block},
                    "summary": trade_metrics(block_bootstrap(trades, rng, block=block))})
    return out


def distributions(runs: list[dict], fields: tuple[str, ...]) -> dict:
    return {f: summarise_distribution([r["summary"].get(f) for r in runs]) for f in fields}


def judge(runs: list[dict]) -> dict:
    """survive = the 5th percentile of the perturbed ensemble still clears every
    profitability bar (PF > 1, CAGR > 0, avg R > 0) — not the point estimate."""
    dist = distributions(runs, VERDICT_METRICS)
    checks = {name: _gt(dist[field]["p5"], threshold)
              for name, (field, threshold) in VERDICT_CHECKS.items()}
    return {"survive": all(checks.values()), "checks": checks, "distribution": dist,
            "runs": len(runs)}


def empty_verdict() -> dict:
    """The same SHAPE judge() returns, for a run with no simulated
    perturbations (`--kinds bootstrap`). build_report indexes every check and
    every metric, so a thinner placeholder crashes the report after the
    baseline has already been paid for."""
    return {"survive": False,
            "checks": {name: False for name in VERDICT_CHECKS},
            "distribution": {m: summarise_distribution([]) for m in VERDICT_METRICS},
            "runs": 0}


def _gt(value, threshold) -> bool:
    return value is not None and value > threshold


def _json_safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _store_latest_session() -> str | None:
    b = barsmod.get_bars("SPY", "1d")
    if b.empty:
        return None
    return str(b["ts"].iloc[-1].tz_convert("America/New_York").date())


REPORT_FIELDS = ("profit_factor", "cagr", "avg_r", "sharpe", "max_drawdown",
                 "win_rate", "num_trades")


def _fmt(v, digits=4) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _dist_table(base: dict, dist: dict) -> list[str]:
    rows = ["| metric | baseline | p5 | median | p95 | dropped |",
            "|---|---|---|---|---|---|"]
    for f, d in dist.items():
        n = 0 if f == "num_trades" else 4
        rows.append(f"| {f} | {_fmt(base.get(f), n)} | {_fmt(d['p5'], n)} | "
                    f"{_fmt(d['median'], n)} | {_fmt(d['p95'], n)} | "
                    f"{d['dropped_non_finite']} |")
    return rows + [""]


def build_report(payload: dict) -> str:
    base = payload["baseline"]["summary"]
    v = payload["verdict"]
    lines = [
        "# CAT D2 — backtest perturbation / sensitivity",
        "",
        f"Generated {payload['generated_for']['generated_at']} · "
        f"code {payload['generated_for'].get('code_sha', 'unknown')[:8]}"
        f"{' (dirty)' if payload['generated_for'].get('code_dirty') else ''} · "
        f"store through {payload['generated_for'].get('store_latest_session')}",
        "",
        f"Span {payload['generated_for']['start']}..{payload['generated_for']['end']}, "
        f"deployed params `{payload['generated_for']['params']}`.",
        "",
        "## Verdict",
        "",
        f"**{'SURVIVES' if v['survive'] else 'DOES NOT SURVIVE'}** perturbation "
        f"({v['runs']} simulated runs).",
        "",
        "| condition | value | required | pass |",
        "|---|---|---|---|",
    ]
    for key, (field, threshold) in VERDICT_CHECKS.items():
        lines.append(f"| p5({field}) | {_fmt(v['distribution'][field]['p5'])} | "
                     f"> {threshold:g} | {'yes' if v['checks'][key] else 'no'} |")
    lines += [
        "",
        "## Baseline (unperturbed, this data vintage)",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for f in REPORT_FIELDS:
        lines.append(f"| {f} | {_fmt(base.get(f), 0 if f == 'num_trades' else 4)} |")

    lines += ["", "## Per-perturbation distributions", ""]
    for kind, block in payload["perturbations"].items():
        lines += [f"### {kind} ({block['n']} runs)", ""]
        if "pool_informative" in block:
            tot, inf = block["pool_total"], block["pool_informative"]
            lines += [
                f"Drawn from the INFORMATIVE pool: {inf['total']} of {tot['total']} "
                f"cached actions (splits {inf['split']}/{tot['split']}, dividends "
                f"{inf['dividend']}/{tot['dividend']}) can move the result at all — "
                f"`adjust()` only rescales bars BEFORE an ex-date, so an action on a "
                f"symbol that never traded before it is almost always a no-op — "
                f"the only escape is the absolute gates (min_price / min_adv / "
                f"cash caps), which a big enough rescale can still flip.",
                "",
                f"**{block['conditional']}**",
                "",
            ]
        lines += _dist_table(base, block["distribution"])
        for name, sub in block.get("strata", {}).items():
            lines += [f"**stratum: {name}** ({sub['n']} runs)", ""]
            lines += _dist_table(base, sub["distribution"])

    lines += [
        "## Reading this",
        "",
        "- Price noise and the dropped-action runs rebuild features from the "
        "perturbed tape, so they capture the full path dependency (signals, "
        "sizing, stops), not just a re-scored trade list.",
        "- `drop_action` is the one CONDITIONAL block: it samples only actions "
        "that can move the result, so read it as \"if a relevant action is "
        "missed, this is the damage\" — not as how often a missed action bites.",
        "- **Why a missed action barely moves anything.** Dropping an action "
        "rescales every pre-ex-date bar by one constant factor, and every signal "
        "the strategy trades on is scale-invariant (MA crossover, MACD sign, "
        "RSI, ATR%, z-score). Sizing is risk-based — `shares = risk$ / (entry - "
        "stop)` — so when entry, stop and ATR all scale together, shares scale "
        "inversely and `pnl = shares x (exit - entry)` is EXACTLY invariant. "
        "What is left for a missed action to bite through is the ex-date "
        "discontinuity landing inside a lookback window, the absolute gates "
        "(min_price / min_adv / cash caps), or share rounding. That is why a "
        "20:1 AMZN split moved PF 0.9321 -> 0.9158 (281 -> 278 trades) while "
        "5:1 splits on mid-priced names cancel to 1e-15.",
        "- Dropping a symbol's ONLY cached action flips `bars.is_unadjusted` to "
        "True, but `simulator._build_features` never reads that attr — the RAW "
        "guard has no effect on the backtest path (observed on FTNT / NOW; not "
        "changed here).",
        "- The bootstrap block resamples the OOS trade SEQUENCE only; it has no "
        "equity curve, so CAGR / Sharpe / drawdown are absent there by design.",
        "- A profit factor of +inf (a resample with no losers) is dropped from "
        "the quantiles and counted in `dropped`.",
        "- ⚠️ These are backtest numbers on one data vintage. They are "
        "not a forecast of live results.",
        "",
    ]
    return "\n".join(lines)


def main(kinds: list[str], n_cap: int | None, workers: int) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    params = json.loads((r09.OUT_DIR / "results.json").read_text())["recommended_params"]
    log(f"deployed params: {params}")

    latest = _store_latest_session()
    log("baseline (unperturbed) run")
    feats, sectors, base_cfg = r09.fixed_oos_inputs(progress=log)
    base_summary, base_trades, base_windows = r09.stitched_run(
        feats, sectors, base_cfg, params, progress=log)
    del feats
    log(f"baseline PF {base_summary['profit_factor']:.4f}, "
        f"trades {base_summary['num_trades']}")

    sim_kinds = [k for k in kinds if k in SIM_KINDS]
    action_pool = informative = None
    if "drop_action" in sim_kinds:
        action_pool = load_all_actions()
        informative = informative_actions(action_pool, base_trades)
        log(f"action pool: {_kind_counts(action_pool)} cached, "
            f"{_kind_counts(informative)} can move the result")
    jobs = build_jobs(sim_kinds, params, n_cap, action_pool=informative)
    log(f"{len(jobs)} perturbed runs on {workers} worker(s)")
    runs = run_perturbations(jobs, workers) if jobs else []
    if "bootstrap" in kinds:
        spec = KINDS["bootstrap"]
        n = spec["n"] if n_cap is None else min(n_cap, spec["n"])
        runs += run_bootstrap(base_trades, n, spec["block"], spec["seed_base"])
        log(f"bootstrap: {n} resamples of {len(base_trades)} trades")

    perturbations = {}
    for kind in kinds:
        sel = [r for r in runs if r["kind"] == kind]
        if not sel:
            continue
        fields = ("profit_factor", "win_rate", "avg_r", "num_trades") \
            if kind == "bootstrap" else REPORT_FIELDS
        perturbations[kind] = {"n": len(sel), "distribution": distributions(sel, fields),
                               "runs": sel}
        if kind == "drop_action":
            strata = {}
            for name in ("split", "dividend"):
                group = [r for r in sel if r["params"].get("action_kind") == name]
                if group:
                    strata[name] = {"n": len(group),
                                    "distribution": distributions(group, fields)}
            perturbations[kind]["strata"] = strata
            perturbations[kind]["pool_total"] = _kind_counts(action_pool)
            perturbations[kind]["pool_informative"] = _kind_counts(informative)
            perturbations[kind]["conditional"] = (
                "Sampled from the informative pool only, so these quantiles are "
                "CONDITIONAL — given a relevant action was missed — not the "
                "unconditional probability that a random missed action matters.")

    sim_runs = [r for r in runs if r["kind"] in SIM_KINDS]
    payload = {
        "generated_for": {
            "start": r09.START, "end": r09.END, "params": params,
            "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
            "store_latest_session": latest,
            "kinds": kinds, "n_cap": n_cap, "workers": workers,
            **r09.experiment_stamp(),
        },
        "baseline": {"summary": base_summary, "per_window": base_windows},
        "perturbations": perturbations,
        "verdict": judge(sim_runs) if sim_runs else empty_verdict(),
        "wall_clock_seconds": round(time.time() - t0, 1),
    }
    payload = _json_safe(payload)
    (OUT_DIR / "results.json").write_text(json.dumps(payload, indent=2, default=str))
    (OUT_DIR / "report.md").write_text(build_report(payload))
    log(f"done in {payload['wall_clock_seconds']}s: survive="
        f"{payload['verdict']['survive']} -> {OUT_DIR}/results.json")
    return payload


def _main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=None,
                    help="cap runs per kind (default: the full plan)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel processes for the simulated perturbations")
    ap.add_argument("--kinds", default="all",
                    help=f"comma-separated subset of {','.join(KINDS)}")
    args = ap.parse_args(argv)
    kinds = list(KINDS) if args.kinds == "all" else args.kinds.split(",")
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        ap.error(f"unknown kind(s) {unknown}; pick from {list(KINDS)}")
    main(kinds, args.n, args.workers)


if __name__ == "__main__":
    _main()
