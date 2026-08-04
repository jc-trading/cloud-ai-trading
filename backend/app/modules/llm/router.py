"""LLM usage-log API (Direction v3 — transparency into every LLM call/cost).

  GET /llm/log   summary tiles (total calls / tokens / USD, today, per-model,
                 per-day) computed over ALL rows via SQL aggregates, plus the
                 most-recent N call rows for the dashboard table.

Auth-only (CurrentUser) — a read-only usage view, never gated behind an admin
permission (the 2026-07-01 /decisions 403 lesson).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.dependencies import DB, CurrentUser
from app.modules.llm.models import LLMCall

router = APIRouter(prefix="/llm", tags=["LLM log"])


def _f(v) -> float:
    return float(v) if v is not None else 0.0


@router.get("/log")
async def llm_log(user: CurrentUser, db: DB = None,
                  limit: int = Query(default=500, ge=1, le=2000)):
    C = LLMCall
    totals = (
        func.count().label("calls"),
        func.coalesce(func.sum(C.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(C.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(C.cost_usd), 0).label("cost_usd"),
    )

    all_time = (await db.execute(select(*totals))).one()
    today = (await db.execute(
        select(*totals).where(func.date(C.created_at) == func.current_date())
    )).one()

    by_model = (await db.execute(
        select(C.model, func.count().label("calls"),
               func.coalesce(func.sum(C.cost_usd), 0).label("cost_usd"))
        .group_by(C.model).order_by(func.sum(C.cost_usd).desc())
    )).all()

    by_day = (await db.execute(
        select(func.date(C.created_at).label("day"),
               func.count().label("calls"),
               func.coalesce(func.sum(C.cost_usd), 0).label("cost_usd"))
        .group_by(func.date(C.created_at))
        .order_by(func.date(C.created_at).desc()).limit(30)
    )).all()

    rows = list((await db.execute(
        select(C).order_by(C.created_at.desc()).limit(limit)
    )).scalars().all())

    def _tile(r):
        return {"calls": int(r.calls), "input_tokens": int(r.input_tokens),
                "output_tokens": int(r.output_tokens),
                "cost_usd": _f(r.cost_usd)}

    return {
        "summary": {
            "all_time": _tile(all_time),
            "today": _tile(today),
            "by_model": [{"model": m, "calls": int(c), "cost_usd": _f(cost)}
                         for m, c, cost in by_model],
            "by_day": [{"day": str(d), "calls": int(c), "cost_usd": _f(cost)}
                       for d, c, cost in by_day],
        },
        "items": [{
            "id": str(r.id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "context": r.context,
            "symbol": r.symbol,
            "platform": r.platform,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "unit_price_in": _f(r.unit_price_in),
            "unit_price_out": _f(r.unit_price_out),
            "cost_usd": _f(r.cost_usd),
            "latency_ms": r.latency_ms,
            "success": r.success,
            "error": r.error,
        } for r in rows],
    }
