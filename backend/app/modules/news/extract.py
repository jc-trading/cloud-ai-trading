"""News extraction (P6b): one LLM call turns a batch of pending news items into
per-symbol claims; the deterministic rules then pick each claim's action.

Runs only on the Claude CLI subscription and within its own daily budget
(``llm_calls.context = 'news_extraction'``), so it cannot starve the nightly
recommendation explanations. A failed or unreadable answer costs the batch one
attempt; the retry is the repair, never a second call.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select, update

from app.config import settings
from app.modules.llm.client import call_llm, resolve_provider
from app.modules.llm.models import LLMCall
from app.modules.news import rules
from app.modules.news.ingest import _clean
from app.modules.news.models import (
    CLAIM_TYPES, DIRECTIONS, NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
)

logger = logging.getLogger(__name__)

CONTEXT = "news_extraction"
_ET = ZoneInfo("America/New_York")
_CAP_RESERVE = 50
_TICK_SECONDS = 360

EXTRACT_SYSTEM = (
    "You convert US stock news into JSON for a research database. You never give advice. "
    "For each input item return exactly one object. Use only the item's listed symbols. "
    "Every claim fills direction (\"up\" or \"down\"), probability (integer 0-100), "
    "expected_move_pct (0.1-50) and horizon_days (integer 1-60). The claim window starts at "
    "the open of the first regular US trading session that opens after Now (given at the top "
    "of the input) and ends at the close of the horizon_days-th session (that first session "
    "counts as 1). A \"price\" claim means: over that window the stock beats SPY in "
    "`direction` by at least max(1, expected_move_pct/2) percentage points (for \"down\": it "
    "trails SPY by at least that much); probability is your estimate that this comes true. "
    "Use claim_type \"non_price\" only when the item is about a future event whose occurrence "
    "is uncertain (deal closing, regulatory decision, product launch); then claim_text states "
    "the event and its deadline in at most 120 characters, probability is the chance the "
    "event happens within the window, and direction and expected_move_pct are the stock move "
    "you expect if it happens. effective_date is the first US trading day the market can "
    "react to the content (after-close news -> next day). If the item gives no basis for a "
    "directional view, set no_claim true and claims []. Output ONLY a JSON array. No prose, "
    "no code fences."
)

_RETURN_LINE = (
    'Return: [{"i":1,"no_claim":false,"feedback":"<=200 chars","effective_date":"YYYY-MM-DD",'
    '"claims":[{"symbol":"AAA","claim_type":"price","claim_text":null,"direction":"up",'
    '"probability":60,"expected_move_pct":2.5,"horizon_days":5,"rationale":"<=160 chars"}]}]'
)


class PromptItem(NamedTuple):
    published_at: datetime
    symbols: list[str]
    headline: str
    summary: str | None


class ClaimOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    claim_type: str
    claim_text: str | None = None
    direction: str
    probability: int = Field(ge=0, le=100)
    expected_move_pct: float = Field(ge=0.1, le=50)
    horizon_days: int = Field(ge=1, le=60)
    rationale: str | None = None

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("claim_type")
    @classmethod
    def _claim_type(cls, v: str) -> str:
        if v not in CLAIM_TYPES:
            raise ValueError(f"claim_type must be one of {CLAIM_TYPES}")
        return v

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str) -> str:
        if v not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        return v

    @field_validator("probability", mode="before")
    @classmethod
    def _whole_number(cls, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (
                isinstance(v, float) and not v.is_integer()):
            raise ValueError("probability must be a whole number")
        return int(v)

    @field_validator("expected_move_pct")
    @classmethod
    def _round(cls, v: float) -> float:
        return round(v, 2)

    @field_validator("claim_text")
    @classmethod
    def _claim_text_clean(cls, v: str | None) -> str | None:
        return _clean(v)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, v: str | None) -> str | None:
        return _clean(v)[:300] if v is not None else None

    @model_validator(mode="after")
    def _claim_text(self):
        if self.claim_type == "price" and self.claim_text is not None:
            raise ValueError("price claim must not have claim_text")
        if self.claim_type == "non_price" and not (self.claim_text and len(self.claim_text) <= 200):
            raise ValueError("non_price claim needs claim_text of 1-200 chars")
        return self


class ItemOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    i: int
    no_claim: bool
    feedback: str
    effective_date: date
    claims: list[ClaimOut]

    @field_validator("feedback")
    @classmethod
    def _feedback(cls, v: str) -> str:
        return _clean(v)[:400]

    @model_validator(mode="after")
    def _claims_match_no_claim(self):
        if self.no_claim != (not self.claims):
            raise ValueError("claims must be empty exactly when no_claim is true")
        return self


def _flat(value: str) -> str:
    return " ".join(html.unescape(value).split())


def build_prompt(items: list[PromptItem], now_et: datetime) -> str:
    lines = [f"Now (ET): {now_et:%Y-%m-%d %H:%M}. Items:"]
    for i, item in enumerate(items, 1):
        published = item.published_at.astimezone(_ET)
        lines.append(f"[{i}] published {published:%Y-%m-%d %H:%M} ET | "
                     f"symbols: {','.join(item.symbols)} | "
                     f"headline: {_flat(item.headline)[:300]} | "
                     f"summary: {_flat(item.summary or '')[:300] or '-'}")
    lines.append(_RETURN_LINE)
    return "\n".join(lines)


def _validate(obj: dict, item: PromptItem) -> ItemOut | str:
    try:
        out = ItemOut.model_validate(obj)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"])
        msg = f"{where}: {err['msg']}" if where else err["msg"]
        return f"invalid: {msg[:120]}"
    published = item.published_at.astimezone(_ET).date()
    if not published - timedelta(days=1) <= out.effective_date <= published + timedelta(days=120):
        return "invalid: effective_date out of range"
    seen: set[str] = set()
    for claim in out.claims:
        if claim.symbol not in item.symbols:
            return f"invalid: symbol {claim.symbol} not in item"
        if claim.symbol in seen:
            return f"invalid: duplicate symbol {claim.symbol}"
        seen.add(claim.symbol)
    return out


def parse_response(text: str | None, items: list[PromptItem]) -> dict[int, ItemOut | str]:
    """Map every batch index (1-based) to its validated object, or to why it was
    rejected: "unparsable" (not a JSON array), "missing", or "invalid: ..."."""
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, list):
        return dict.fromkeys(range(1, len(items) + 1), "unparsable")

    by_i: dict[int, list[dict]] = {}
    for obj in data:
        i = obj.get("i") if isinstance(obj, dict) else None
        if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= len(items):
            by_i.setdefault(i, []).append(obj)

    out: dict[int, ItemOut | str] = {}
    for i, item in enumerate(items, 1):
        objs = by_i.get(i)
        if not objs:
            out[i] = "missing"
        elif len(objs) > 1:
            out[i] = "invalid: duplicate i"
        else:
            out[i] = _validate(objs[0], item)
    return out


async def _next_batch(db) -> list[NewsItem]:
    pending = (select(NewsItem).where(NewsItem.status == "pending")
               .order_by(NewsItem.priority, NewsItem.published_at.desc()))
    retry = (await db.execute(pending.where(NewsItem.extract_attempts >= 1).limit(1))).scalars().all()
    if retry:
        return list(retry)
    return list((await db.execute(
        pending.where(NewsItem.extract_attempts == 0).limit(settings.NEWS_BATCH_SIZE)
    )).scalars().all())


async def _symbol_rows(db, news_ids) -> dict:
    rows: dict = {news_id: [] for news_id in news_ids}
    for row in (await db.execute(
        select(NewsItemSymbol).where(NewsItemSymbol.news_id.in_(news_ids))
        .order_by(NewsItemSymbol.symbol)
    )).scalars():
        rows[row.news_id].append(row)
    return rows


async def _source_stats(db, news_ids) -> dict:
    stats: dict = {news_id: [] for news_id in news_ids}
    for r in (await db.execute(
        select(NewsItemSource.news_id, NewsSource.enabled, NewsSource.kind,
               NewsSource.n_evaluated, NewsSource.lift)
        .join(NewsSource, NewsSource.id == NewsItemSource.source_id)
        .where(NewsItemSource.news_id.in_(news_ids), NewsSource.kind != "platform")
    )).all():
        stats[r.news_id].append(rules.SourceStat(
            r.enabled, r.kind, r.n_evaluated, float(r.lift) if r.lift is not None else None))
    return stats


def _attempt_failed(item: NewsItem, at: datetime, out: dict) -> None:
    item.extract_attempts += 1
    item.updated_at = at
    if item.extract_attempts >= settings.NEWS_MAX_ATTEMPTS:
        item.status = "failed"
        out["failed"] += 1


def _write_item(item: NewsItem, res: ItemOut, symbol_rows: list[NewsItemSymbol], stats, *,
                call_id, at: datetime) -> None:
    item.effective_date = res.effective_date
    item.llm_feedback = res.feedback
    item.llm_call_id = call_id
    item.extracted_at = at
    item.updated_at = at
    item.status = "no_claim" if res.no_claim else "extracted"
    anchor = rules.next_open_session(at)
    rows = {r.symbol: r for r in symbol_rows}
    for claim in res.claims:
        _write_claim(rows[claim.symbol], claim, stats, at=at, anchor=anchor)


def _write_claim(row: NewsItemSymbol, claim: ClaimOut, stats, *, at: datetime,
                 anchor: date) -> None:
    row.claim_type = claim.claim_type
    row.claim_text = claim.claim_text
    row.direction = claim.direction
    row.probability = claim.probability
    row.expected_move_pct = claim.expected_move_pct
    row.horizon_days = claim.horizon_days
    row.rationale = claim.rationale
    row.anchor_session = anchor
    row.evaluate_after = rules.evaluate_after(anchor, claim.horizon_days)
    action, reason, review_at = rules.decide_action(
        claim_type=claim.claim_type, direction=claim.direction, probability=claim.probability,
        relevance=row.relevance, stats=stats, now=at, anchor=anchor)
    row.suggested_action = action
    row.action_reason = reason[:200]
    row.review_at = review_at
    row.review_status = "open" if review_at else "none"


async def run_extraction(db, *, now: datetime,
                         clock=lambda: datetime.now(timezone.utc)) -> dict:
    """One tick of extraction: mark stale pending items, then call the LLM batch
    by batch until the tick's call limit, the daily news budget, an empty queue,
    or the first call that was skipped or failed. Commits after every batch."""
    if not settings.NEWS_ENABLED:
        return {"llm": "disabled"}
    out = {"llm": "ok", "calls": 0, "extracted": 0, "no_claim": 0, "failed": 0, "stale": 0,
           "llm_stop": None}

    stale = await db.execute(
        update(NewsItem)
        .where(NewsItem.status == "pending",
               NewsItem.published_at < now - timedelta(hours=settings.NEWS_EXTRACT_MAX_AGE_HOURS))
        .values(status="skipped", skip_reason="stale", updated_at=now)
        .execution_options(synchronize_session=False))
    await db.commit()
    out["stale"] = stale.rowcount

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

    max_calls = min(settings.NEWS_MAX_CALLS_PER_TICK,
                    int(_TICK_SECONDS // settings.CLAUDE_CLI_TIMEOUT_SECONDS))
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(max_calls):
        used = (await db.execute(
            select(func.count()).select_from(LLMCall)
            .where(LLMCall.context == CONTEXT, LLMCall.created_at >= day_start)
        )).scalar()
        if used >= settings.NEWS_LLM_DAILY_BUDGET:
            out["llm_stop"] = "news budget reached"
            break
        batch = await _next_batch(db)
        if not batch:
            break
        news_ids = [item.id for item in batch]
        symbols = await _symbol_rows(db, news_ids)
        prompt_items = [PromptItem(item.published_at, [r.symbol for r in symbols[item.id]],
                                   item.headline, item.summary) for item in batch]

        result = await call_llm(db, context=CONTEXT,
                                prompt=build_prompt(prompt_items, clock().astimezone(_ET)),
                                system=EXTRACT_SYSTEM)
        await db.commit()
        t = clock()
        if result.skipped:
            out["llm_stop"] = result.error
            break
        out["calls"] += 1
        if not result.success:
            for item in batch:
                _attempt_failed(item, t, out)
            out["llm_stop"] = (result.error or "")[:120]
            await db.commit()
            break

        parsed = parse_response(result.text, prompt_items)
        stats = await _source_stats(db, news_ids)
        for i, (item, news_id) in enumerate(zip(batch, news_ids), 1):
            res = parsed[i]
            if isinstance(res, str):
                _attempt_failed(item, t, out)
                continue
            try:
                async with db.begin_nested():
                    _write_item(item, res, symbols[news_id], stats[news_id],
                                call_id=result.call_id, at=t)
            except Exception as e:
                logger.warning("news extraction: could not store item id=%s (%s)",
                               news_id, type(getattr(e, "orig", None) or e).__name__)
                await db.refresh(item)
                _attempt_failed(item, t, out)
                continue
            out[item.status] += 1
        await db.commit()
    return out
