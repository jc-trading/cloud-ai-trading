"""Per-night confidence IC with Fama-MacBeth aggregation (D1, 2026-09-10).

The pooled Spearman IC in ``outcomes.build_report`` is dominated by
between-night return-level differences rather than within-night ranking skill —
adding a single night moved the pooled 1d IC by ~0.10
(``qa/outcomes_ic_report_2026-08-14.md``). This module ranks confidence against
forward returns WITHIN each trade_date and averages the nightly ICs
Fama-MacBeth style, split by strategy era so the old sector-cap nights are never
pooled with the Phase A / Phase C ones. Read-only: SELECTs ``recommendations``
+ ``recommendation_outcomes``, writes nothing back to the DB. Not scheduled:

    docker compose exec -T backend python -m app.modules.simledger.outcomes_ic \
        > qa/outcomes_ic_report_<date>.md

Markdown goes to stdout; the machine-readable payload is written to
``cat-data/outcomes_ic/results_<date>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from quant import config

from app.modules.simledger.models import Recommendation, RecommendationOutcome
from app.modules.simledger.outcomes import HORIZONS, spearman_ic

# era boundaries on trade_date: pre < ERA_CUTS[0] <= phase_a < ERA_CUTS[1] <= phase_c
ERA_CUTS = ("2026-09-09", "2026-09-11")
ERA_NAMES = ("pre", "phase_a", "phase_c")
MIN_NIGHT_N = 10
OUT_DIR = config.DATA_ROOT / "outcomes_ic"


# ── pure computation ─────────────────────────────────────────────

def _as_date(value) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def assign_era(trade_date, cuts: tuple = ERA_CUTS) -> str:
    """Era label for one trade_date (the session the rec is FOR, not created_at)."""
    first, second = (_as_date(c) for c in cuts)
    d = _as_date(trade_date)
    if d < first:
        return ERA_NAMES[0]
    return ERA_NAMES[1] if d < second else ERA_NAMES[2]


def era_windows(cuts: tuple = ERA_CUTS) -> dict[str, str]:
    first, second = (_as_date(c).isoformat() for c in cuts)
    return {
        ERA_NAMES[0]: f"trade_date < {first}",
        ERA_NAMES[1]: f"{first} <= trade_date < {second}",
        ERA_NAMES[2]: f"trade_date >= {second}",
    }


def per_night_ic(df: pd.DataFrame, horizon: int, *,
                 min_n: int = MIN_NIGHT_N) -> list[dict]:
    """One IC record per trade_date; ic is None for nights that cannot be scored.

    A night is dropped when fewer than ``min_n`` recs have a return for the
    horizon, or when confidence has no cross-sectional variance — both make the
    rank correlation meaningless rather than merely noisy.
    """
    col = f"ret_{horizon}d"
    records: list[dict] = []
    if df.empty:
        return records
    for trade_date, group in df.groupby("trade_date", sort=True):
        sub = group[["confidence", col]].dropna()
        n = int(len(sub))
        ic = None
        if n >= min_n and sub["confidence"].nunique() >= 2:
            ic = spearman_ic(sub["confidence"], sub[col])
        records.append({"trade_date": _as_date(trade_date), "n": n, "ic": ic})
    return records


def _usable(ics) -> list[float]:
    return [float(x) for x in ics if x is not None and math.isfinite(float(x))]


def fama_macbeth(ics) -> dict:
    """Mean of the nightly ICs with its cross-night standard error and t-stat."""
    vals = _usable(ics)
    n = len(vals)
    out = {"mean_ic": None, "se": None, "t": None, "n_nights": n}
    if n == 0:
        return out
    mean = sum(vals) / n
    out["mean_ic"] = mean
    if n < 2:
        return out
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    se = math.sqrt(var / n)
    out["se"] = se
    out["t"] = mean / se if se > 0 else None
    return out


def effective_lag(n: int, lag: int) -> int:
    """The lag a sample of n nights can actually carry."""
    return max(0, min(int(lag), int(n) - 1))


def newey_west_se(ics, lag: int = 0) -> float | None:
    """Newey-West (Bartlett) standard error of the mean nightly IC.

    Overlapping forward windows make consecutive nightly ICs autocorrelated, so
    the naive Fama-MacBeth SE understates the true one for 3d/5d; pass
    ``lag = horizon - 1``. Autocovariances carry the (n-1) small-sample
    denominator, so lag=0 reduces exactly to the naive SE. Returns None when
    fewer than 2*(lag+1) nights are available: the autocovariances are then
    estimated off a handful of products and the estimate is as likely to sit
    BELOW the naive SE as above it, which would read as spurious precision.
    """
    vals = _usable(ics)
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    lag = effective_lag(n, lag)
    if n < 2 * (lag + 1):
        return None

    def gamma(k: int) -> float:
        return sum((vals[t] - mean) * (vals[t - k] - mean)
                   for t in range(k, n)) / (n - 1)

    s = gamma(0)
    for k in range(1, lag + 1):
        s += 2.0 * (1.0 - k / (lag + 1)) * gamma(k)
    return math.sqrt(max(s, 0.0) / n)


def _block(df: pd.DataFrame, window: str, *, horizons: tuple[int, ...],
           min_n: int) -> dict:
    block = {
        "window": window,
        "n": int(len(df)),
        "nights": int(df["trade_date"].nunique()) if not df.empty else 0,
        "coverage": [],
        "horizons": {},
    }
    for h in horizons:
        nights = per_night_ic(df, h, min_n=min_n)
        ics = [r["ic"] for r in nights]
        stats = fama_macbeth(ics)
        stats["nw_se"] = newey_west_se(ics, lag=h - 1)
        stats["nw_lag"] = effective_lag(stats["n_nights"], h - 1)
        stats["t_nw"] = (stats["mean_ic"] / stats["nw_se"]
                         if stats["nw_se"] else None)
        stats["nights_pending"] = sum(1 for r in nights if r["n"] == 0)
        stats["nights_dropped"] = sum(1 for r in nights
                                      if r["ic"] is None and r["n"] > 0)
        stats["per_night"] = nights
        block["horizons"][str(h)] = stats

    if not df.empty:
        by_night = {h: {r["trade_date"]: r for r in
                        block["horizons"][str(h)]["per_night"]}
                    for h in horizons}
        for trade_date, group in df.groupby("trade_date", sort=True):
            row = {"trade_date": _as_date(trade_date), "n": int(len(group))}
            for h in horizons:
                rec = by_night[h][_as_date(trade_date)]
                row[f"with_{h}d"] = rec["n"]
                row[f"ic_{h}d"] = rec["ic"]
            block["coverage"].append(row)
    return block


def analyse(df: pd.DataFrame, total_recs: int, *,
            horizons: tuple[int, ...] = HORIZONS, min_n: int = MIN_NIGHT_N,
            cuts: tuple = ERA_CUTS) -> dict:
    """Full result tree: pooled baseline + global and per-era Fama-MacBeth blocks."""
    windows = era_windows(cuts)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "era_cuts": [_as_date(c).isoformat() for c in cuts],
        "min_night_n": int(min_n),
        "horizons": list(horizons),
        "recommendations_in_db": int(total_recs),
        "evaluated": int(len(df)),
        "direction_counts": {},
        "pooled_ic": {},
        "global": _block(df.iloc[0:0], "all nights",
                         horizons=horizons, min_n=min_n),
        "eras": {name: _block(df.iloc[0:0], windows[name],
                              horizons=horizons, min_n=min_n)
                 for name in ERA_NAMES},
    }
    if df.empty:
        return result

    result["direction_counts"] = {k: int(v) for k, v in
                                  df["direction"].value_counts().items()}
    up = df[df["direction"] == "up"]
    result["analysed"] = int(len(up))
    for h in horizons:
        col = f"ret_{h}d"
        result["pooled_ic"][str(h)] = {
            "ic": spearman_ic(up["confidence"], up[col]),
            "n": int(up[col].notna().sum()),
        }
    result["global"] = _block(up, "all nights", horizons=horizons, min_n=min_n)
    era = up["trade_date"].map(lambda d: assign_era(d, cuts))
    result["eras"] = {
        name: _block(up[era == name], windows[name],
                     horizons=horizons, min_n=min_n)
        for name in ERA_NAMES
    }
    return result


def build_payload(result: dict):
    """JSON-safe copy: dates to ISO strings, NaN/inf to null."""
    if isinstance(result, dict):
        return {k: build_payload(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [build_payload(v) for v in result]
    if isinstance(result, date):
        return result.isoformat()
    if isinstance(result, float):
        return result if math.isfinite(result) else None
    return result


# ── report ───────────────────────────────────────────────────────

def _f(value, fmt: str = "{:+.3f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _era_section(name: str, block: dict, horizons: tuple[int, ...]) -> list[str]:
    lines = [f"### {name} — `{block['window']}`", ""]
    if block["n"] == 0:
        lines += ["no evaluable nights yet", ""]
        return lines
    lines.append(f"- recs: {block['n']}; nights: {block['nights']}")
    lines.append("")
    head = "| trade_date | n |" + "".join(
        f" with_{h}d | IC_{h}d |" for h in horizons)
    lines.append(head)
    lines.append("|---|---|" + "---|---|" * len(horizons))
    for row in block["coverage"]:
        cells = [row["trade_date"].isoformat(), str(row["n"])]
        for h in horizons:
            cells += [str(row[f"with_{h}d"]), _f(row[f"ic_{h}d"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("| horizon | nights used | dropped | pending | FM mean IC | "
                 "SE | t | NW-SE (lag) | t_NW |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for h in horizons:
        s = block["horizons"][str(h)]
        nw = (f"{s['nw_se']:.3f} (lag {s['nw_lag']})"
              if s["nw_se"] is not None else "n/a")
        lines.append(
            f"| ret_{h}d | {s['n_nights']} | {s['nights_dropped']} | "
            f"{s['nights_pending']} | {_f(s['mean_ic'])} | "
            f"{_f(s['se'], '{:.3f}')} | {_f(s['t'], '{:+.2f}')} | "
            f"{nw} | {_f(s['t_nw'], '{:+.2f}')} |")
    lines.append("")
    return lines


def build_markdown(result: dict, df: pd.DataFrame) -> str:
    """Report body: pooled baseline, global + per-era FM, quintile/shortlist tables."""
    horizons = tuple(result["horizons"])
    lines = ["# Recommendation outcomes — per-night IC (Fama-MacBeth)", ""]
    lines.append(f"> Generated {result['generated_at']} by "
                 "`python -m app.modules.simledger.outcomes_ic`. Read-only over "
                 "`recommendations` + `recommendation_outcomes`; run "
                 "`outcomes.py` first to refresh the forward returns.")
    lines.append("")
    if df.empty:
        lines.append("no evaluable outcomes yet")
        return "\n".join(lines) + "\n"

    lines.append(f"- recommendations in DB: {result['recommendations_in_db']}; "
                 f"evaluated: {result['evaluated']} "
                 f"(rest pending: trade_date bar not stored yet)")
    lines.append(f"- directions: {result['direction_counts']}")
    lines.append(f"- analysed (direction=up): {result.get('analysed', 0)}; "
                 f"era cuts on trade_date: {result['era_cuts']}; "
                 f"min recs per night: {result['min_night_n']}")
    lines.append("")

    lines.append("## Pooled Spearman IC (baseline — the number this report replaces)")
    lines.append("")
    for h in horizons:
        p = result["pooled_ic"][str(h)]
        lines.append(f"- ret_{h}d: IC = {_f(p['ic'])} (n={p['n']})")
    lines.append("")
    lines.append("Pooled ranks every rec of every night in one basket, so it "
                 "mostly measures which nights had good returns. The per-night "
                 "numbers below are the ones to read.")
    lines.append("")

    lines.append("## Per-night IC — Fama-MacBeth")
    lines.append("")
    lines += _era_section("global", result["global"], horizons)
    for name in ERA_NAMES:
        lines += _era_section(name, result["eras"][name], horizons)

    up = df[df["direction"] == "up"]
    lines.append("## Mean forward return by confidence quintile (direction=up)")
    lines.append("")
    q = up.dropna(subset=["ret_1d"]).copy()
    if len(q) >= 10:
        q["quintile"] = pd.qcut(q["confidence"], 5, labels=False,
                                duplicates="drop") + 1
        tbl = q.groupby("quintile").agg(
            n=("symbol", "size"), conf_min=("confidence", "min"),
            conf_max=("confidence", "max"), ret_1d=("ret_1d", "mean"),
            ret_3d=("ret_3d", "mean"), ret_5d=("ret_5d", "mean"))
        lines.append("```")
        lines.append(tbl.to_string(float_format=lambda x: f"{x:+.4f}"))
        lines.append("```")
    else:
        lines.append("(fewer than 10 evaluable up-recs — skipped)")
    lines.append("")

    lines.append("## Shortlist vs below-the-cut (direction=up, mean returns)")
    lines.append("")
    up2 = up.copy()
    up2["bucket"] = up2["shortlist_rank"].notna().map(
        {True: "shortlist", False: "below_cut"})
    tbl2 = up2.groupby("bucket")[["ret_1d", "ret_3d", "ret_5d"]].agg(
        ["mean", "count"])
    lines.append("```")
    lines.append(tbl2.to_string(float_format=lambda x: f"{x:+.4f}"))
    lines.append("```")
    lines.append("")

    lines.append("> ⚠️ Read with care: signal nights overlap in time (same "
                 "market regime), symbols within a night are cross-correlated, "
                 "and n is tiny — this is plumbing for the eventual verdict, "
                 "not the verdict.")
    lines.append("> The naive FM t is optimistic: it assumes the nightly "
                 "ICs are independent, which 3d/5d overlapping windows are "
                 "not — read t_NW instead wherever it exists. NW-SE "
                 "shows n/a when fewer than 2*(lag+1) nights are available; "
                 "that is not a pass for the naive t, it means neither SE "
                 "is trustworthy yet.")
    return "\n".join(lines) + "\n"


# ── data access ──────────────────────────────────────────────────

async def load_frame(db) -> tuple[pd.DataFrame, int]:
    """Joined recommendations + outcomes as a float frame, plus the total rec count."""
    rows = (await db.execute(
        select(Recommendation.trade_date, Recommendation.symbol,
               Recommendation.direction, Recommendation.confidence,
               Recommendation.shortlist_rank,
               RecommendationOutcome.ret_1d, RecommendationOutcome.ret_3d,
               RecommendationOutcome.ret_5d)
        .join(RecommendationOutcome,
              RecommendationOutcome.recommendation_id == Recommendation.id)
    )).all()
    total_recs = len((await db.execute(select(Recommendation.id))).all())
    df = pd.DataFrame(rows, columns=[
        "trade_date", "symbol", "direction", "confidence", "shortlist_rank",
        "ret_1d", "ret_3d", "ret_5d"])
    for c in ("confidence", "ret_1d", "ret_3d", "ret_5d"):
        df[c] = df[c].astype(float)
    return df, total_recs


# ── entrypoint ───────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="outcomes_ic",
        description="Per-night confidence IC (Fama-MacBeth) by strategy era.")
    p.add_argument("--era-cuts", nargs=2, default=list(ERA_CUTS),
                   metavar=("PHASE_A_FROM", "PHASE_C_FROM"),
                   help="trade_date boundaries, ISO (default: %(default)s)")
    p.add_argument("--min-n", type=int, default=MIN_NIGHT_N,
                   help="minimum recs for a night to be scored (default: %(default)s)")
    p.add_argument("--out-dir", default=str(OUT_DIR),
                   help="JSON output directory (default: %(default)s)")
    return p.parse_args(argv)


async def _main(argv=None) -> None:
    args = _parse_args(argv)
    from app.database import AsyncSessionLocal, engine

    # DEBUG builds the engine with echo=True, which streams every statement to
    # stdout — where the markdown report goes
    engine.sync_engine.echo = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    async with AsyncSessionLocal() as db:
        df, total_recs = await load_frame(db)

    result = analyse(df, total_recs, min_n=args.min_n,
                     cuts=tuple(args.era_cuts))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"results_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(build_payload(result), indent=2,
                                   allow_nan=False))
    print(build_markdown(result, df))
    print(f"payload written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_main())
