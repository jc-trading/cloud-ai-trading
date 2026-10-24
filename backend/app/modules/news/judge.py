"""Non-price claim judgement (P7b), run inside the nightly news evaluation.

A non-price claim ("the deal closes by Q1") is judged by the LLM from the
follow-up headlines in our own database only — the model's knowledge ends
before these events. A claim waits until three days after its window closed so
the follow-ups can arrive; with no follow-up at all it scores 0 without a call;
one still unjudged ten sessions after its window is written ``excluded`` and
never counted. Runs only on the Claude CLI subscription within its own daily
budget (``llm_calls.context = 'news_judgement'``); an invalid answer is not
written and is judged again the next night.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import func, select

from quant.data import calendar as qcal

from app.config import settings
from app.modules.llm.client import call_llm, resolve_provider
from app.modules.llm.models import LLMCall
from app.modules.news import outcomes
from app.modules.news.extract import _CAP_RESERVE, _ET, _flat
from app.modules.news.models import VERDICTS, NewsItem, NewsItemSymbol, NewsOutcome

logger = logging.getLogger(__name__)

CONTEXT = "news_judgement"
_BATCH = 5
_HEADLINES = 15
_IN_WINDOW = 10
_FOLLOWUP = timedelta(days=3)
_DEADLINE_SESSIONS = 10
_RUN_SECONDS = 600
_PRICE_FIELDS = ("anchor_price", "end_price", "realized_move_pct", "spy_move_pct",
                 "excess_move_pct", "sector_etf", "sector_move_pct", "magnitude_error_pct")

JUDGE_SYSTEM = (
    "You check whether stated events happened, using ONLY the follow-up headlines given for "
    "each claim. Do not use outside knowledge. verdict: \"happened\", \"not_happened\" "
    "(headlines show it failed, was blocked or was cancelled), or \"no_evidence\". evidence: "
    "the bracketed numbers of the headlines you relied on, or \"\". Output ONLY a JSON array: "
    "[{\"i\":1,\"verdict\":\"happened\",\"evidence\":\"[2]\"}]. No prose. "
    "An event counts as happened only if it occurred by the window end."
)

_RETURN_LINE = 'Return: [{"i":1,"verdict":"happened","evidence":"[2]"}]'


class JudgeClaim(NamedTuple):
    symbol: str
    claim_text: str
    stated_at: datetime
    anchor: date
    end: date
    headlines: list[tuple[datetime, str]]


def build_prompt(claims: list[JudgeClaim]) -> str:
    lines = []
    for i, c in enumerate(claims, 1):
        lines.append(f"Claim {i}: {c.symbol} | {_flat(c.claim_text)} | "
                     f"stated {c.stated_at.astimezone(_ET):%Y-%m-%d %H:%M} ET | "
                     f"window {c.anchor} to {c.end}")
        lines.append("Follow-up headlines:")
        window_close = qcal.rth_bounds(c.end)[1].to_pydatetime()
        for j, (at, headline) in enumerate(c.headlines, 1):
            after = " (after window)" if at > window_close else ""
            lines.append(f"[{j}] {at.astimezone(_ET):%Y-%m-%d %H:%M} ET{after} | "
                         f"{_flat(headline)[:300]}")
    lines.append(_RETURN_LINE)
    return "\n".join(lines)


def _validate(obj: dict) -> tuple[str, str | None] | str:
    verdict, evidence = obj.get("verdict"), obj.get("evidence")
    if verdict not in VERDICTS:
        return "invalid: verdict"
    if not isinstance(evidence, str) or len(evidence) > 300:
        return "invalid: evidence"
    return verdict, evidence or None


def parse_response(text: str | None, n: int) -> dict[int, tuple[str, str | None] | str]:
    """Map every batch index (1-based) to (verdict, evidence), or to why it was
    rejected: "unparsable", "missing", or "invalid: ..."."""
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, list):
        return dict.fromkeys(range(1, n + 1), "unparsable")

    by_i: dict[int, list[dict]] = {}
    for obj in data:
        i = obj.get("i") if isinstance(obj, dict) else None
        if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= n:
            by_i.setdefault(i, []).append(obj)

    out: dict[int, tuple[str, str | None] | str] = {}
    for i in range(1, n + 1):
        objs = by_i.get(i)
        if not objs:
            out[i] = "missing"
        elif len(objs) > 1:
            out[i] = "invalid: duplicate i"
        else:
            out[i] = _validate(objs[0])
    return out


async def followups(db, row: NewsItemSymbol, item: NewsItem, window_close: datetime,
                    window_end: datetime) -> list[tuple[datetime, str]]:
    """Same-symbol headlines after the claim, oldest first: the newest ten inside
    the window, then the earliest of the grace period up to fifteen in all, so a
    busy name's grace-period news cannot crowd out the in-window evidence."""
    def between(after: datetime, until: datetime):
        return (select(NewsItem.published_at, NewsItem.headline)
                .join(NewsItemSymbol, NewsItemSymbol.news_id == NewsItem.id)
                .where(NewsItemSymbol.symbol == row.symbol, NewsItem.id != item.id,
                       NewsItem.published_at > after, NewsItem.published_at <= until))

    inside = (await db.execute(between(item.published_at, window_close)
                               .order_by(NewsItem.published_at.desc()).limit(_IN_WINDOW))).all()
    grace = (await db.execute(between(max(item.published_at, window_close), window_end)
                              .order_by(NewsItem.published_at)
                              .limit(_HEADLINES - len(inside)))).all()
    return sorted(((at, headline) for at, headline in [*inside, *grace]), key=lambda h: h[0])


def _price_fields(row: NewsItemSymbol, prices, sector_map: dict) -> dict:
    etf = outcomes.sector_etf(row.symbol, sector_map)
    r = outcomes.evaluate_price(
        direction=row.direction, expected=float(row.expected_move_pct),
        anchor=row.anchor_session, end=row.evaluate_after,
        prices=prices.get(row.symbol), spy=prices.get("SPY"),
        sector=prices.get(etf) if etf else None, etf=etf)
    return {k: r[k] if r["status"] == "scored" else None for k in _PRICE_FIELDS}


async def _store(db, outcome: NewsOutcome) -> bool:
    try:
        async with db.begin_nested():
            db.add(outcome)
            await db.flush()
    except Exception as e:
        logger.warning("news judgement: could not store outcome for %s (%s)",
                       outcome.news_symbol_id, type(getattr(e, "orig", None) or e).__name__)
        return False
    return True


def _review_reason(verdict: str) -> str:
    return f"horizon ended: event {verdict.replace('_', ' ')} · held"


def _scored(base: dict, verdict: str, evidence: str | None = None, call_id=None) -> NewsOutcome:
    happened = verdict == "happened"
    return NewsOutcome(**base, status="scored", verdict=verdict, evidence=evidence,
                       hit=happened, score=1.0 if happened else 0.0, llm_call_id=call_id)


async def run_judgement(db, *, last, now, prices, sector_map, written) -> dict:
    """Judge every non-price claim whose window ended by ``last``: exclude the
    ones past the deadline, leave the ones still collecting follow-ups, score
    the ones without follow-ups 0, then ask the LLM five claims per call until
    the run's call limit, the judge budget, or the first skipped or failed call.
    Each scored outcome is appended to ``written`` as (row id, symbol, review
    reason) for the held-position review, only once its commit went through."""
    out = {"llm": "ok", "due": 0, "waiting": 0, "no_evidence": 0, "judged": 0, "invalid": 0,
           "excluded": 0, "calls": 0, "llm_stop": None}
    rows = (await db.execute(
        select(NewsItemSymbol, NewsItem)
        .join(NewsItem, NewsItem.id == NewsItemSymbol.news_id)
        .where(NewsItemSymbol.claim_type == "non_price",
               NewsItemSymbol.evaluate_after <= last,
               ~select(NewsOutcome.id)
               .where(NewsOutcome.news_symbol_id == NewsItemSymbol.id).exists())
        .order_by(NewsItemSymbol.evaluate_after, NewsItemSymbol.id)
    )).all()
    out["due"] = len(rows)

    pending, no_evidence = [], []
    for row, item in rows:
        base = {"news_symbol_id": row.id, "method": "llm",
                "scoring_eligible": item.origin == "live",
                "anchor_session": row.anchor_session, "end_session": row.evaluate_after,
                **_price_fields(row, prices, sector_map)}
        if last >= qcal.advance(row.evaluate_after, _DEADLINE_SESSIONS, unit="sessions"):
            if await _store(db, NewsOutcome(**base, status="excluded",
                                            reason="judge_unavailable")):
                out["excluded"] += 1
            continue
        window_close = qcal.rth_bounds(row.evaluate_after)[1].to_pydatetime()
        window_end = window_close + _FOLLOWUP
        if now < window_end:
            out["waiting"] += 1
            continue
        headlines = await followups(db, row, item, window_close, window_end)
        if not headlines:
            if await _store(db, _scored(base, "no_evidence")):
                no_evidence.append((row.id, row.symbol, _review_reason("no_evidence")))
                out["no_evidence"] += 1
            continue
        pending.append((JudgeClaim(row.symbol, row.claim_text, item.published_at,
                                   row.anchor_session, row.evaluate_after, headlines), base,
                        (row.id, row.symbol)))
    await db.commit()
    written.extend(no_evidence)

    try:
        platform = resolve_provider().platform
    except ValueError:
        platform = None
    if platform != "claude-cli":
        out["llm"] = "skipped: provider not claude_cli"
        return out
    if (settings.NEWS_LLM_DAILY_BUDGET + settings.NEWS_JUDGE_DAILY_BUDGET
            > settings.CLAUDE_CLI_DAILY_CAP - _CAP_RESERVE):
        out["llm"] = "skipped: news budget exceeds cap reserve"
        return out

    max_calls = int(_RUN_SECONDS // settings.CLAUDE_CLI_TIMEOUT_SECONDS)
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    batches = [pending[k:k + _BATCH] for k in range(0, len(pending), _BATCH)]
    for batch in batches[:max_calls]:
        used = (await db.execute(
            select(func.count()).select_from(LLMCall)
            .where(LLMCall.context == CONTEXT, LLMCall.created_at >= day_start)
        )).scalar()
        if used >= settings.NEWS_JUDGE_DAILY_BUDGET:
            out["llm_stop"] = "judge budget reached"
            break
        result = await call_llm(db, context=CONTEXT, prompt=build_prompt([c for c, _, _ in batch]),
                                system=JUDGE_SYSTEM)
        await db.commit()
        if result.skipped:
            out["llm_stop"] = result.error
            break
        out["calls"] += 1
        if not result.success:
            out["llm_stop"] = (result.error or "")[:120]
            break
        parsed = parse_response(result.text, len(batch))
        judged = []
        for i, (_, base, (row_id, symbol)) in enumerate(batch, 1):
            res = parsed[i]
            if isinstance(res, str):
                out["invalid"] += 1
                continue
            if await _store(db, _scored(base, *res, call_id=result.call_id)):
                judged.append((row_id, symbol, _review_reason(res[0])))
                out["judged"] += 1
        await db.commit()
        written.extend(judged)
    return out
