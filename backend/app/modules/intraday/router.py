"""Intraday API (P5b).

  GET /intraday/watch?session=        the realtime subscription set by tier, what
                                      the cap dropped, and the stream's view of it
                                      (admin)

Read-only: the set is resolved the same way the market stream resolves it, and
the subscribed / rejected flags come from the stream's last heartbeat.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from quant import config as qconfig
from quant.data import registry as qregistry

from app.dependencies import DB, AdminUser
from app.modules.simledger.models import HeartbeatRecord

router = APIRouter(prefix="/intraday", tags=["Intraday"])


def _entry(e) -> dict:
    return {"symbol": e.symbol, "tier": e.tier, "groups": list(e.groups), "reason": e.reason}


@router.get("/watch")
async def get_watch(admin: AdminUser, db: DB = None, session: date | None = Query(None)):
    beat = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == "market_stream")
    )).scalar_one_or_none()
    meta = (beat.meta if beat is not None else None) or {}
    cap_effective = int(meta.get("cap_effective") or qconfig.STREAM_SYMBOL_CAP)
    cap = min(qconfig.STREAM_SYMBOL_CAP, cap_effective)
    s = session or qregistry.watch_session()
    try:
        kept, dropped = await asyncio.to_thread(qregistry.resolve_watch, qregistry,
                                                session=s, cap=cap)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    subscribed = meta.get("subscribed")
    rejected = set(meta.get("rejected") or [])
    return {
        "session": s.isoformat(), "cap": cap, "cap_effective": cap_effective,
        "entries": [{**_entry(e),
                     "subscribed": None if subscribed is None else e.symbol in subscribed,
                     "rejected": e.symbol in rejected} for e in kept],
        "dropped": [_entry(e) for e in dropped],
        "stream": {
            "age_s": (None if beat is None
                      else round(time.time() - beat.last_beat_at.timestamp(), 1)),
            "connected": meta.get("connected"),
            "period_key": meta.get("period_key"),
            "errors": meta.get("stream_errors") or [],
        },
    }
