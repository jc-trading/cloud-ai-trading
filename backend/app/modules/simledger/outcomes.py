"""Recommendation forward-outcome backfill + confidence-IC report (P1).

Answers "did the recommendations actually work?" after the fact, with zero
LLM tokens: for every published Recommendation, compute forward returns from
the trade_date session OPEN to the close of the 1st / 3rd / 5th session after
it, using the same Parquet daily bars (adjusted on read) the engine itself
uses. Derived analytics only — reads ``recommendations`` + ``cat-data`` bars,
writes ``recommendation_outcomes``. NOT scheduled; run manually:

    docker compose exec -T backend python -m app.modules.simledger.outcomes

Idempotent: one row per recommendation_id; a re-run rewrites every row of a
symbol whose bars store has advanced (``evaluated_through`` < latest stored
session) — this both extends horizons and re-absorbs corporate-action
read-time re-adjustments — and creates rows for recs whose trade_date bar has
since arrived. Recs whose trade_date session has no bar yet are counted as
pending and skipped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd
from sqlalchemy import select

from quant.data import bars as qbars

from app.modules.simledger.models import Recommendation, RecommendationOutcome

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 5)


# ── pure computation ─────────────────────────────────────────────

def forward_outcome(daily: pd.DataFrame, trade_date: date,
                    horizons: tuple[int, ...] = HORIZONS) -> dict | None:
    """Forward returns for one (symbol, trade_date) from a daily-bars frame.

    Anchors at the trade_date session open; ret_Nd = close(t+N)/open(t) - 1
    where N counts SESSIONS present in the frame (the store only holds
    completed sessions, so gaps/holidays are handled by construction).
    Returns None when the trade_date bar is absent (pending — not yet
    evaluable); horizons beyond the last stored session come back None.
    """
    if daily is None or daily.empty:
        return None
    d = daily.copy()
    # Alpaca daily ts is midnight ET expressed in UTC (04:00/05:00Z), so the
    # UTC calendar date IS the session date — do not tz-convert to ET here.
    d["_date"] = pd.to_datetime(d["ts"], utc=True).dt.date
    d = d.sort_values("_date").reset_index(drop=True)
    hit = d.index[d["_date"] == trade_date]
    if len(hit) == 0:
        return None
    i = int(hit[0])
    base_open = float(d.at[i, "open"])
    out: dict = {
        "base_open": base_open,
        "close_d0": float(d.at[i, "close"]),
        "evaluated_through": d["_date"].iloc[-1],
    }
    for n in horizons:
        j = i + n
        out[f"ret_{n}d"] = (
            float(d.at[j, "close"]) / base_open - 1.0 if j < len(d) else None
        )
    return out


def spearman_ic(conf: pd.Series, rets: pd.Series) -> float | None:
    """Spearman rank correlation (confidence vs forward return)."""
    df = pd.DataFrame({"c": conf, "r": rets}).dropna()
    if len(df) < 3 or df["c"].nunique() < 2 or df["r"].nunique() < 2:
        return None
    return float(df["c"].rank().corr(df["r"].rank()))


# ── backfill ─────────────────────────────────────────────────────

async def backfill(db, *, bars_fn=qbars.get_bars) -> dict:
    """Upsert recommendation_outcomes for every evaluable recommendation."""
    recs = (await db.execute(select(Recommendation))).scalars().all()
    existing = {
        o.recommendation_id: o
        for o in (await db.execute(select(RecommendationOutcome))).scalars().all()
    }
    bars_cache: dict[str, pd.DataFrame] = {}
    stats = {"recs": len(recs), "created": 0, "updated": 0,
             "unchanged": 0, "pending": 0, "no_bars": 0}

    for rec in recs:
        if rec.symbol not in bars_cache:
            try:
                bars_cache[rec.symbol] = bars_fn(rec.symbol, "1d")
            except Exception as e:                      # missing parquet etc.
                logger.warning("bars unavailable for %s: %s", rec.symbol, e)
                bars_cache[rec.symbol] = pd.DataFrame()
        daily = bars_cache[rec.symbol]
        if daily.empty:
            stats["no_bars"] += 1
            continue

        res = forward_outcome(daily, rec.trade_date)
        if res is None:
            stats["pending"] += 1                       # session bar not stored yet
            continue

        row = existing.get(rec.id)
        if row is None:
            db.add(RecommendationOutcome(
                recommendation_id=rec.id, symbol=rec.symbol,
                trade_date=rec.trade_date, **res))
            stats["created"] += 1
        elif row.evaluated_through < res["evaluated_through"]:
            for k, v in res.items():
                setattr(row, k, v)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    await db.commit()
    return stats


# ── report ───────────────────────────────────────────────────────

async def build_report(db) -> str:
    """Confidence-IC + coverage report over the joined outcomes."""
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

    if not rows:
        return "# Recommendation outcomes\n\nno evaluable outcomes yet\n"

    df = pd.DataFrame(rows, columns=[
        "trade_date", "symbol", "direction", "confidence", "shortlist_rank",
        "ret_1d", "ret_3d", "ret_5d"])
    for c in ("confidence", "ret_1d", "ret_3d", "ret_5d"):
        df[c] = df[c].astype(float)

    lines = ["# Recommendation outcomes — forward returns & confidence IC", ""]
    lines.append(f"- recommendations in DB: {total_recs}; evaluated: {len(df)} "
                 f"(rest pending: trade_date bar not stored yet)")
    lines.append(f"- directions: {df['direction'].value_counts().to_dict()}")
    lines.append("")

    lines.append("## Coverage per trade_date")
    cov = df.groupby("trade_date").agg(
        n=("symbol", "size"),
        with_1d=("ret_1d", "count"),
        with_3d=("ret_3d", "count"),
        with_5d=("ret_5d", "count")).reset_index()
    lines.append(cov.to_string(index=False))
    lines.append("")

    up = df[df["direction"] == "up"]
    lines.append(f"## Confidence IC (Spearman, direction=up, n={len(up)})")
    for h in HORIZONS:
        col = f"ret_{h}d"
        ic = spearman_ic(up["confidence"], up[col])
        n = int(up[col].notna().sum())
        lines.append(f"- {col}: IC = {'n/a' if ic is None else f'{ic:+.3f}'} "
                     f"(n={n})")
    lines.append("")

    lines.append("## Mean forward return by confidence quintile (direction=up)")
    q = up.dropna(subset=["ret_1d"]).copy()
    if len(q) >= 10:
        q["quintile"] = pd.qcut(q["confidence"], 5, labels=False,
                                duplicates="drop") + 1
        tbl = q.groupby("quintile").agg(
            n=("symbol", "size"), conf_min=("confidence", "min"),
            conf_max=("confidence", "max"), ret_1d=("ret_1d", "mean"),
            ret_3d=("ret_3d", "mean"), ret_5d=("ret_5d", "mean"))
        lines.append(tbl.to_string(float_format=lambda x: f"{x:+.4f}"))
    else:
        lines.append("(fewer than 10 evaluable up-recs — skipped)")
    lines.append("")

    lines.append("## Shortlist vs below-the-cut (direction=up, mean returns)")
    up2 = up.copy()
    up2["bucket"] = up2["shortlist_rank"].notna().map(
        {True: "shortlist", False: "below_cut"})
    tbl2 = up2.groupby("bucket")[["ret_1d", "ret_3d", "ret_5d"]].agg(
        ["mean", "count"])
    lines.append(tbl2.to_string(float_format=lambda x: f"{x:+.4f}"))
    lines.append("")

    lines.append("> ⚠️ Read with care: signal nights overlap in time (same "
                 "market regime), symbols within a night are cross-correlated, "
                 "and n is tiny — this is plumbing for the eventual verdict, "
                 "not the verdict.")
    return "\n".join(lines) + "\n"


# ── entrypoint ───────────────────────────────────────────────────

async def _main() -> None:
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        stats = await backfill(db)
        print(f"backfill: {stats}")
        print()
        print(await build_report(db))


if __name__ == "__main__":
    asyncio.run(_main())
