"""News API (P6c).

  GET    /news/items?status=&symbol=&action=&origin=&limit=50&cursor=
                                          stories, newest first (keyset cursor)
  GET    /news/items/{item_id}            one story: claims, sources, its LLM call
  GET    /news/review-queue?include_upcoming=false&limit=200
                                          open reviews, earliest due first
  PATCH  /news/symbols/{symbol_id}/review close an open review as done / dismissed (admin)
  GET    /news/sources?kind=              sources with score, lift and state
  PATCH  /news/sources/{source_id}        enable / disable an author source (admin)

No endpoint calls the LLM, runs extraction or queues a task. A source toggle
only changes how items extracted after it are ranked and labelled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import aliased

from app.config import settings
from app.dependencies import DB, AdminUser, CurrentUser
from app.modules.llm.models import LLMCall
from app.modules.news.models import (
    ACTIONS, ITEM_STATUSES, ORIGINS, SOURCE_KINDS,
    NewsItem, NewsItemSource, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory,
)
from app.modules.simledger.models import HeartbeatRecord

router = APIRouter(prefix="/news", tags=["News"])

NEWS_CONTEXT = "news_extraction"
_HISTORY_DAYS = 60


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewUpdate(_Strict):
    status: Literal["done", "dismissed"]
    note: Optional[str] = Field(None, max_length=500)


class SourceUpdate(_Strict):
    enabled: bool
    reason: Optional[str] = Field(None, max_length=300)


def _iso(v):
    return v.isoformat() if v is not None else None


def _num(v):
    return float(v) if v is not None else None


def _one_of(value, allowed, name: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"{name} must be one of {', '.join(allowed)}")


def _cursor(item: NewsItem) -> str:
    return f"{item.published_at.isoformat()}|{item.id}"


def _parse_cursor(raw: str) -> tuple[datetime, UUID]:
    at, sep, key = raw.rpartition("|")
    try:
        if not sep:
            raise ValueError
        ts, uid = datetime.fromisoformat(at), UUID(key)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail="cursor is not a value this API returned") from None
    if ts.tzinfo is None:
        raise HTTPException(status_code=422, detail="cursor is not a value this API returned")
    return ts, uid


async def _children(db, ids) -> tuple[dict, dict]:
    symbols = {i: [] for i in ids}
    sources = {i: [] for i in ids}
    if not ids:
        return symbols, sources
    for s in (await db.execute(
        select(NewsItemSymbol).where(NewsItemSymbol.news_id.in_(ids))
        .order_by(NewsItemSymbol.symbol)
    )).scalars():
        symbols[s.news_id].append(s)
    for link, src in (await db.execute(
        select(NewsItemSource, NewsSource)
        .join(NewsSource, NewsSource.id == NewsItemSource.source_id)
        .where(NewsItemSource.news_id.in_(ids))
        .order_by(NewsItemSource.fetched_at, NewsItemSource.id)
    )).all():
        sources[link.news_id].append((link, src))
    return symbols, sources


def _symbol_out(s: NewsItemSymbol, full: bool = False) -> dict:
    out = {
        "id": str(s.id), "symbol": s.symbol, "relevance": s.relevance,
        "claim_type": s.claim_type, "direction": s.direction, "probability": s.probability,
        "expected_move_pct": _num(s.expected_move_pct), "horizon_days": s.horizon_days,
        "suggested_action": s.suggested_action, "review_status": s.review_status,
    }
    if full:
        out.update({
            "claim_text": s.claim_text, "rationale": s.rationale,
            "anchor_session": _iso(s.anchor_session), "evaluate_after": _iso(s.evaluate_after),
            "action_reason": s.action_reason, "review_at": _iso(s.review_at),
            "reviewed_at": _iso(s.reviewed_at), "review_note": s.review_note,
        })
    return out


def _outcome_out(o: Optional[NewsOutcome]) -> Optional[dict]:
    if o is None:
        return None
    return {
        "status": o.status, "method": o.method, "scoring_eligible": o.scoring_eligible,
        "anchor_session": _iso(o.anchor_session), "end_session": _iso(o.end_session),
        "anchor_price": _num(o.anchor_price), "end_price": _num(o.end_price),
        "realized_move_pct": _num(o.realized_move_pct), "spy_move_pct": _num(o.spy_move_pct),
        "excess_move_pct": _num(o.excess_move_pct), "sector_etf": o.sector_etf,
        "sector_move_pct": _num(o.sector_move_pct), "hit": o.hit, "score": _num(o.score),
        "magnitude_error_pct": _num(o.magnitude_error_pct), "verdict": o.verdict,
        "evidence": o.evidence, "reason": o.reason, "evaluated_at": _iso(o.evaluated_at),
    }


def _item_out(item: NewsItem, symbols: list, sources: list) -> dict:
    return {
        "id": str(item.id), "headline": item.headline,
        "published_at": _iso(item.published_at), "discovered_at": _iso(item.discovered_at),
        "origin": item.origin, "status": item.status, "skip_reason": item.skip_reason,
        "effective_date": _iso(item.effective_date), "extracted_at": _iso(item.extracted_at),
        "sources": [{"name": src.name, "kind": src.kind, "url": link.url}
                    for link, src in sources],
        "symbols": [_symbol_out(s) for s in symbols],
    }


def _queue_row(s: NewsItemSymbol, item: NewsItem) -> dict:
    return {**_symbol_out(s, full=True), "news_id": str(item.id), "headline": item.headline,
            "published_at": _iso(item.published_at)}


def _source_out(src: NewsSource, parent: Optional[str]) -> dict:
    return {
        "id": str(src.id), "kind": src.kind, "name": src.name, "parent": parent,
        "enabled": src.enabled, "disabled_at": _iso(src.disabled_at),
        "disabled_by": src.disabled_by, "disabled_reason": src.disabled_reason,
        "score": _num(src.score), "lift": _num(src.lift), "n_evaluated": src.n_evaluated,
        "hits": src.hits, "auto_disable_after_n": src.auto_disable_after_n,
        "score_updated_at": _iso(src.score_updated_at),
    }


async def _meta(db) -> dict:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = (await db.execute(
        select(func.count()).select_from(LLMCall)
        .where(LLMCall.context == NEWS_CONTEXT, LLMCall.created_at >= day_start)
    )).scalar()
    pending = (await db.execute(
        select(func.count()).select_from(NewsItem).where(NewsItem.status == "pending")
    )).scalar()
    beat = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == "news_cycle")
    )).scalar_one_or_none()
    return {
        "budget_used_today": used, "budget": settings.NEWS_LLM_DAILY_BUDGET,
        "pending": pending,
        "last_cycle": {"at": _iso(beat.last_beat_at), "meta": beat.meta} if beat else None,
    }


@router.get("/items")
async def list_items(user: CurrentUser, db: DB,
                     status: Optional[str] = Query(default=None),
                     symbol: Optional[str] = Query(default=None, max_length=20),
                     action: Optional[str] = Query(default=None),
                     origin: Optional[str] = Query(default=None),
                     limit: int = Query(default=50, ge=1, le=200),
                     cursor: Optional[str] = Query(default=None)):
    _one_of(status, ITEM_STATUSES, "status")
    _one_of(action, ACTIONS, "action")
    _one_of(origin, ORIGINS, "origin")
    stmt = select(NewsItem)
    if status is not None:
        stmt = stmt.where(NewsItem.status == status)
    if origin is not None:
        stmt = stmt.where(NewsItem.origin == origin)
    if symbol is not None and symbol.strip():
        stmt = stmt.where(select(NewsItemSymbol.id).where(
            NewsItemSymbol.news_id == NewsItem.id,
            NewsItemSymbol.symbol == symbol.strip().upper()).exists())
    if action is not None:
        stmt = stmt.where(select(NewsItemSymbol.id).where(
            NewsItemSymbol.news_id == NewsItem.id,
            NewsItemSymbol.suggested_action == action).exists())
    if cursor is not None:
        at, uid = _parse_cursor(cursor)
        stmt = stmt.where(tuple_(NewsItem.published_at, NewsItem.id) < tuple_(at, uid))
    rows = (await db.execute(
        stmt.order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(limit)
    )).scalars().all()
    symbols, sources = await _children(db, [r.id for r in rows])
    return {
        "items": [_item_out(r, symbols[r.id], sources[r.id]) for r in rows],
        "next_cursor": _cursor(rows[-1]) if len(rows) == limit else None,
        "meta": await _meta(db),
    }


@router.get("/items/{item_id}")
async def get_item(item_id: UUID, user: CurrentUser, db: DB):
    item = await db.get(NewsItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="news item not found")
    symbols, sources = await _children(db, [item.id])
    outs = {o.news_symbol_id: o for o in (await db.execute(
        select(NewsOutcome).where(NewsOutcome.news_symbol_id.in_([s.id for s in symbols[item.id]]))
    )).scalars()}
    llm = None
    call = await db.get(LLMCall, item.llm_call_id) if item.llm_call_id else None
    if call is not None:
        batch_size = (await db.execute(
            select(func.count()).select_from(NewsItem).where(NewsItem.llm_call_id == call.id)
        )).scalar()
        llm = {
            "id": str(call.id), "created_at": _iso(call.created_at),
            "platform": call.platform, "model": call.model, "billing": call.billing,
            "input_tokens": call.input_tokens, "cache_read_tokens": call.cache_read_tokens,
            "cache_creation_tokens": call.cache_creation_tokens,
            "total_input_tokens": ((call.input_tokens or 0) + (call.cache_read_tokens or 0)
                                   + (call.cache_creation_tokens or 0)),
            "output_tokens": call.output_tokens, "cost_usd": _num(call.cost_usd),
            "latency_ms": call.latency_ms, "batch_size": batch_size,
        }
    return {
        **_item_out(item, symbols[item.id], sources[item.id]),
        "summary": item.summary, "llm_feedback": item.llm_feedback,
        "priority": item.priority, "extract_attempts": item.extract_attempts,
        "updated_at": _iso(item.updated_at),
        "symbols": [{**_symbol_out(s, full=True), "outcome": _outcome_out(outs.get(s.id))}
                    for s in symbols[item.id]],
        "sources": [{
            "id": str(link.id), "provider": link.provider, "external_id": link.external_id,
            "url": link.url, "published_at": _iso(link.published_at),
            "fetched_at": _iso(link.fetched_at),
            "source": {"id": str(src.id), "name": src.name, "kind": src.kind,
                       "enabled": src.enabled, "score": _num(src.score),
                       "lift": _num(src.lift), "n_evaluated": src.n_evaluated},
        } for link, src in sources[item.id]],
        "llm": llm,
    }


@router.get("/review-queue")
async def review_queue(user: CurrentUser, db: DB, include_upcoming: bool = False,
                       limit: int = Query(default=200, ge=1, le=500)):
    stmt = (select(NewsItemSymbol, NewsItem)
            .join(NewsItem, NewsItem.id == NewsItemSymbol.news_id)
            .where(NewsItemSymbol.review_status == "open"))
    if not include_upcoming:
        stmt = stmt.where(NewsItemSymbol.review_at <= datetime.now(timezone.utc))
    rows = (await db.execute(
        stmt.order_by(NewsItemSymbol.review_at, NewsItemSymbol.id).limit(limit)
    )).all()
    return [_queue_row(s, item) for s, item in rows]


@router.patch("/symbols/{symbol_id}/review")
async def review_symbol(symbol_id: UUID, data: ReviewUpdate, admin: AdminUser, db: DB):
    row = await db.get(NewsItemSymbol, symbol_id)
    if row is None:
        raise HTTPException(status_code=404, detail="news symbol not found")
    result = await db.execute(
        update(NewsItemSymbol)
        .where(NewsItemSymbol.id == symbol_id, NewsItemSymbol.review_status == "open")
        .values(review_status=data.status, reviewed_at=datetime.now(timezone.utc),
                reviewed_by=admin.id, review_note=(data.note or "").strip() or None)
        .execution_options(synchronize_session=False))
    if result.rowcount == 0:
        await db.refresh(row)
        raise HTTPException(status_code=409, detail=f"review is {row.review_status}, not open")
    await db.refresh(row)
    return _queue_row(row, await db.get(NewsItem, row.news_id))


@router.get("/sources")
async def list_sources(user: CurrentUser, db: DB, kind: Optional[str] = Query(default=None)):
    _one_of(kind, SOURCE_KINDS, "kind")
    parent = aliased(NewsSource)
    stmt = (select(NewsSource, parent.name)
            .outerjoin(parent, parent.id == NewsSource.parent_id))
    if kind is not None:
        stmt = stmt.where(NewsSource.kind == kind)
    rows = (await db.execute(stmt.order_by(NewsSource.kind, NewsSource.name))).all()
    h = NewsSourceScoreHistory
    ranked = select(h.source_id, h.as_of, h.lift, h.n, h.brier,
                    func.row_number().over(partition_by=h.source_id, order_by=h.as_of.desc())
                    .label("rn")).subquery()
    history, brier = {}, {}
    for r in (await db.execute(
        select(ranked).where(ranked.c.rn <= _HISTORY_DAYS)
        .order_by(ranked.c.source_id, ranked.c.as_of)
    )).all():
        history.setdefault(r.source_id, []).append(
            {"as_of": _iso(r.as_of), "lift": _num(r.lift), "n": r.n})
        if r.rn == 1:
            brier[r.source_id] = _num(r.brier)
    return {
        "sources": [{**_source_out(src, parent_name), "history": history.get(src.id, []),
                     "brier": brier.get(src.id)} for src, parent_name in rows],
        "thresholds": {
            "min_n": settings.NEWS_SOURCE_MIN_N,
            "action_min_lift": settings.NEWS_ACTION_MIN_LIFT,
            "disable_below_lift": settings.NEWS_DISABLE_BELOW_LIFT,
            "consecutive_days": settings.NEWS_DISABLE_CONSECUTIVE_DAYS,
        },
    }


@router.patch("/sources/{source_id}")
async def update_source(source_id: UUID, data: SourceUpdate, admin: AdminUser, db: DB):
    src = await db.get(NewsSource, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="news source not found")
    if src.kind == "platform":
        raise HTTPException(status_code=409, detail=(
            "platform sources are not toggled here: "
            "disabling a platform does not cover its author sources"))
    reason = (data.reason or "").strip()
    if not data.enabled and not reason:
        raise HTTPException(status_code=422, detail=[
            {"loc": ["body", "reason"], "msg": "a reason is required to disable a source"}])
    if src.enabled != data.enabled:
        if data.enabled:
            src.enabled = True
            src.disabled_at = src.disabled_by = src.disabled_reason = None
            src.auto_disable_after_n = src.n_evaluated + settings.NEWS_SOURCE_MIN_N
        else:
            src.enabled = False
            src.disabled_at = datetime.now(timezone.utc)
            src.disabled_by = "manual"
            src.disabled_reason = reason
        await db.flush()
    parent = await db.get(NewsSource, src.parent_id) if src.parent_id else None
    return _source_out(src, parent.name if parent else None)
