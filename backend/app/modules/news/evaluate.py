"""Nightly news evaluation (P7a), run by news.evaluate on the bulk worker.

1. Every price claim whose horizon ended by the last complete panel session gets
   one outcome; a claim without bars waits out a grace period, then is written
   ``no_data`` (excluded from scoring, never counted as a miss).
2. Every source is re-scored from all eligible (live) scored outcomes — a full,
   idempotent recompute that does not look at ``enabled``, so a disabled source
   keeps a shadow score. A platform row holds the deduplicated union of its
   authors for display and is never auto-disabled.
3. A leaf source whose lift stayed below the bar for consecutive sessions is
   disabled after the commit, with one notification per run.
4. A held name whose claim horizon just ended is reopened for review, silently.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant.data import bars as qbars, calendar as qcal, panel as qpanel, sectors as qsectors

from app.config import settings
from app.modules.news import outcomes, scoring
from app.modules.news.models import (
    NewsItem, NewsItemSource, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory
)
from app.modules.news.relevance import TIER_HELD, relevance_map
from app.tasks.quant_tasks import _notify

logger = logging.getLogger(__name__)

_COMMIT_EVERY = 100


class Prices:
    def __init__(self, bars_fn, last):
        self._bars_fn = bars_fn
        self._last = last
        self._memo: dict[str, dict | None] = {}

    def get(self, symbol: str) -> dict | None:
        if symbol not in self._memo:
            try:
                self._memo[symbol] = outcomes.daily_prices(
                    self._bars_fn(symbol, "1d", end=self._last))
            except Exception:
                logger.warning("news evaluation: bars unavailable for %s", symbol, exc_info=True)
                self._memo[symbol] = {}
        return self._memo[symbol]


def _review_reason(row: NewsItemSymbol, o: NewsOutcome) -> str:
    if o.status == "no_data":
        return f"horizon ended: no price data ({o.reason}) · held"
    if o.method == "llm":
        return f"horizon ended: event {o.verdict.replace('_', ' ')} · held"
    return (f"horizon ended: {'hit' if o.hit else 'miss'} excess "
            f"{float(o.excess_move_pct):+.1f}% vs claim {row.direction} "
            f"{float(row.expected_move_pct):g}% · held")


async def _evaluate_prices(db, *, last, prices: Prices, sector_map: dict, out: dict) -> list:
    rows = (await db.execute(
        select(NewsItemSymbol, NewsItem.origin)
        .join(NewsItem, NewsItem.id == NewsItemSymbol.news_id)
        .where(NewsItemSymbol.claim_type == "price",
               NewsItemSymbol.evaluate_after <= last,
               ~select(NewsOutcome.id)
               .where(NewsOutcome.news_symbol_id == NewsItemSymbol.id).exists())
        .order_by(NewsItemSymbol.evaluate_after, NewsItemSymbol.id)
    )).all()
    out["price_due"] = len(rows)
    written = []
    for i, (row, origin) in enumerate(rows, 1):
        try:
            etf = outcomes.sector_etf(row.symbol, sector_map)
            r = outcomes.evaluate_price(
                direction=row.direction, expected=float(row.expected_move_pct),
                anchor=row.anchor_session, end=row.evaluate_after,
                prices=prices.get(row.symbol), spy=prices.get("SPY"),
                sector=prices.get(etf) if etf else None, etf=etf)
            if r["status"] == "no_data" and last < qcal.advance(
                    row.evaluate_after, settings.NEWS_NODATA_GRACE_SESSIONS, unit="sessions"):
                out["deferred"] += 1
            else:
                async with db.begin_nested():
                    outcome = NewsOutcome(news_symbol_id=row.id, method="price",
                                          scoring_eligible=(origin == "live"),
                                          anchor_session=row.anchor_session,
                                          end_session=row.evaluate_after, **r)
                    db.add(outcome)
                    await db.flush()
                written.append((row, outcome))
                out[r["status"]] += 1
        except Exception as e:
            logger.warning("news evaluation: could not evaluate %s (%s)",
                           row.id, type(getattr(e, "orig", None) or e).__name__)
        if i % _COMMIT_EVERY == 0:
            await db.commit()
    await db.commit()
    return written


async def _score_sources(db, *, last, now, out: dict) -> None:
    eligible = (NewsOutcome.status == "scored", NewsOutcome.scoring_eligible.is_(True))
    global_n, global_hits = (await db.execute(
        select(func.count(), func.count().filter(NewsOutcome.hit.is_(True)))
        .select_from(NewsOutcome).where(*eligible)
    )).one()
    p0 = scoring.prior_mean(global_hits, global_n)

    pairs = (await db.execute(
        select(NewsItemSource.source_id, NewsOutcome.id, NewsOutcome.hit,
               NewsItemSymbol.probability)
        .distinct()
        .select_from(NewsOutcome)
        .join(NewsItemSymbol, NewsItemSymbol.id == NewsOutcome.news_symbol_id)
        .join(NewsItemSource, NewsItemSource.news_id == NewsItemSymbol.news_id)
        .where(*eligible)
    )).all()
    parent_of = dict((await db.execute(select(NewsSource.id, NewsSource.parent_id))).all())
    groups: dict = defaultdict(dict)
    for source_id, outcome_id, hit, probability in pairs:
        groups[source_id][outcome_id] = (hit, probability)
        parent = parent_of.get(source_id)
        if parent is not None:
            groups[parent][outcome_id] = (hit, probability)

    for source_id, rows in groups.items():
        n = len(rows)
        hits = sum(1 for hit, _ in rows.values() if hit)
        post = scoring.posterior(hits, n, p0, settings.NEWS_PRIOR_STRENGTH)
        lf = scoring.lift(post, p0)
        br = scoring.brier([(p, hit) for hit, p in rows.values()])
        values = {"n": n, "hits": hits, "score": round(post, 4), "lift": round(lf, 4),
                  "prior_mean": round(p0, 4), "brier": None if br is None else round(br, 4)}
        await db.execute(
            pg_insert(NewsSourceScoreHistory)
            .values(source_id=source_id, as_of=last, **values)
            .on_conflict_do_update(constraint="uq_news_source_score_day", set_=values))
        await db.execute(
            update(NewsSource).where(NewsSource.id == source_id)
            .values(n_evaluated=n, hits=hits, score=round(post, 4), lift=round(lf, 4),
                    score_updated_at=now)
            .execution_options(synchronize_session=False))

    await db.execute(
        update(NewsSource)
        .where(NewsSource.id.not_in(list(groups)),
               or_(NewsSource.n_evaluated != 0, NewsSource.hits != 0,
                   NewsSource.score.is_not(None), NewsSource.lift.is_not(None)))
        .values(n_evaluated=0, hits=0, score=None, lift=None, score_updated_at=now)
        .execution_options(synchronize_session=False))
    await db.commit()
    out.update(p0=round(p0, 4), global_n=global_n, sources_scored=len(groups))


async def _auto_disable(db, *, last, now, out: dict) -> None:
    k = settings.NEWS_DISABLE_CONSECUTIVE_DAYS
    candidates = (await db.execute(
        select(NewsSource.id, NewsSource.name, NewsSource.kind, NewsSource.enabled,
               NewsSource.n_evaluated, NewsSource.auto_disable_after_n)
        .where(NewsSource.kind != "platform", NewsSource.enabled.is_(True))
        .order_by(NewsSource.name)
    )).all()
    disabled: list[tuple[str, str]] = []
    for src in candidates:
        lifts = [float(x) for x in (await db.execute(
            select(NewsSourceScoreHistory.lift)
            .where(NewsSourceScoreHistory.source_id == src.id,
                   NewsSourceScoreHistory.as_of <= last)
            .order_by(NewsSourceScoreHistory.as_of.desc())
            .limit(k)
        )).scalars()]
        if not scoring.should_auto_disable(enabled=src.enabled, kind=src.kind,
                                           n=src.n_evaluated,
                                           auto_disable_after_n=src.auto_disable_after_n,
                                           lifts_desc=lifts):
            continue
        reason = (f"lift {lifts[0]:.2f} < {settings.NEWS_DISABLE_BELOW_LIFT:g} "
                  f"for {k} sessions (n={src.n_evaluated})")
        result = await db.execute(
            update(NewsSource)
            .where(NewsSource.id == src.id, NewsSource.enabled.is_(True))
            .values(enabled=False, disabled_at=now, disabled_by="auto", disabled_reason=reason)
            .execution_options(synchronize_session=False))
        if result.rowcount == 1:
            disabled.append((src.name, reason))
    await db.commit()
    out["auto_disabled"] = [name for name, _ in disabled]
    if disabled:
        await _notify(f"🔇 news sources auto-disabled ({len(disabled)}): "
                      + "; ".join(f"{name} — {reason}" for name, reason in disabled[:10])
                      + (f"; +{len(disabled) - 10} more" if len(disabled) > 10 else "")
                      + ". Shadow scoring continues; re-enable on News Sources.",
                      event="source_disabled", severity="warning")


async def _reopen_held(db, written: list, *, now, relevance_fn, out: dict) -> None:
    if not written:
        return
    held = {s for s, (tier, _) in (await relevance_fn(db)).items() if tier == TIER_HELD}
    for row, o in written:
        if row.symbol not in held:
            continue
        result = await db.execute(
            update(NewsItemSymbol)
            .where(NewsItemSymbol.id == row.id, NewsItemSymbol.review_status != "open")
            .values(review_status="open", review_at=now, reviewed_at=None, reviewed_by=None,
                    action_reason=_review_reason(row, o)[:200])
            .execution_options(synchronize_session=False))
        out["held_reviews"] += result.rowcount
    await db.commit()


async def run_evaluation(db, *, now, latest_complete=qpanel.latest_complete,
                         bars_fn=qbars.get_bars, sectors_fn=qsectors.load_sectors,
                         relevance_fn=relevance_map) -> dict:
    last = latest_complete()
    if last is None:
        return {"last": None, "skipped": "no complete session"}
    out = {"last": last.isoformat(), "skipped": None, "price_due": 0, "scored": 0,
           "no_data": 0, "deferred": 0, "p0": None, "global_n": 0, "sources_scored": 0,
           "auto_disabled": [], "held_reviews": 0}
    prices = Prices(bars_fn, last)
    sector_map = sectors_fn()

    written = await _evaluate_prices(db, last=last, prices=prices, sector_map=sector_map,
                                     out=out)
    await _score_sources(db, last=last, now=now, out=out)
    await _auto_disable(db, last=last, now=now, out=out)
    await _reopen_held(db, written, now=now, relevance_fn=relevance_fn, out=out)
    return out
