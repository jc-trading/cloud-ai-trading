"""intraday.nightly — on the bulk worker every night: build each active 1m
group's watch list for the next session and backfill from SIP the 1min files
of the INTRADAY_WARMUP_SESSIONS (2) sessions before it. Never raises.

Every beat slot re-decides from the heartbeat: once the next session's lists
are built and backfilled for the same group set, later slots return at once.
Quant modules are imported inside the calls so the worker boots without them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import text

from app.celery_database import CeleryAsyncSessionLocal, celery_engine
from app.tasks.quant_tasks import _beat, _beat_meta, _notify, _run_async

logger = logging.getLogger(__name__)

INTRADAY_LOCK_KEY = 7310602
BEAT_NAME = "intraday_nightly"


def build_watch(session, groups, *, shortlist, registry) -> dict:
    from quant.data.screen import PanelIncomplete
    from quant.engine.screener import parse_spec
    from quant.engine.strategies import get_spec

    results: dict[str, dict] = {}
    symbols: set[str] = set()
    for g in groups:
        try:
            cfg = get_spec(g.strategy_key).build(g.params)
            spec = parse_spec(g.screener)
            if spec is None:
                results[g.name] = {"status": "error", "n": 0, "error": "no screener rules"}
                continue
            ranked = shortlist(spec, cfg, session)
            rows = [(sym, i, round(float(v), 6),
                     f"ret_std({cfg.rank.rank_window})={v:.4f} rank {i}")
                    for i, (sym, v) in enumerate(ranked, 1)]
            registry.replace_watch_rows(session, g.group_id, rows)
        except PanelIncomplete:
            results[g.name] = {"status": "pending", "n": 0}
            continue
        except Exception as e:
            logger.exception("intraday watch build failed for group %s", g.name)
            results[g.name] = {"status": "error", "n": 0,
                               "error": f"{type(e).__name__}: {e}"[:200]}
            continue
        results[g.name] = {"status": "ok", "n": len(rows)}
        symbols.update(sym for sym, *_ in rows)
    return {"groups": results, "symbols": sorted(symbols)}


def warmup_pairs(session, previous, *, registry) -> set:
    """The (symbol, day) files the nightly backfill needs: ``session``'s
    shortlist over its warmup sessions plus ``previous``'s shortlist on
    ``previous`` itself. ``session`` is never included — it has not traded yet,
    so asking for it would keep the run pending forever."""
    from quant import config as qconfig
    from quant.data import intraday_data

    days = intraday_data.completed_sessions_before(session, qconfig.INTRADAY_WARMUP_SESSIONS)
    return ({(r.symbol, d) for r in registry.intraday_watch_rows(session) for d in days}
            | {(r.symbol, previous) for r in registry.intraday_watch_rows(previous)})


async def run_nightly(db, *, now, registry, shortlist, backfill, notify) -> str:
    """One slot: skip when no session has closed, return early when the next
    session is already done for the current group set, otherwise rebuild the
    watch lists, backfill the warmup files and record the outcome in the
    ``intraday_nightly`` heartbeat. A fail-closed backfill alerts once per
    session."""
    from quant.data import calendar, eod_correction

    previous = eod_correction.last_completed_session(now)
    if previous is None:
        await _beat(db, BEAT_NAME, skipped="no completed session")
        return "skipped"
    session = calendar.next_session(previous)
    groups = registry.active_1m_groups()
    names = sorted(g.name for g in groups)
    prev = await _beat_meta(db, BEAT_NAME)
    if (prev.get("session") == session.isoformat() and prev.get("done") is True
            and sorted(prev.get("groups") or {}) == names):
        return "done"

    built = build_watch(session, groups, shortlist=shortlist, registry=registry)
    pairs = warmup_pairs(session, previous, registry=registry)
    if not pairs:
        # backfill_sessions' disk guard fails closed even on an empty set
        bf = {"done": True, "written": [], "skipped": 0, "empty": [], "iex_uncorrected": [],
              "failed": [], "remaining": 0, "requests": 0, "fail_closed": None}
    else:
        bf = backfill(pairs, registry=registry, deadline_s=1200)

    fail_closed = bf["fail_closed"]
    done = (all(g["status"] == "ok" for g in built["groups"].values())
            and fail_closed is None and not bf["failed"] and bf["remaining"] == 0)
    alerted = prev.get("alerted") if prev.get("session") == session.isoformat() else None
    if fail_closed is not None and alerted != session.isoformat():
        await notify(f"⛔ intraday.nightly fail-closed ({fail_closed}): 1min warmup "
                     f"backfill for {session} stopped",
                     event="data_fail_closed", severity="critical")
        alerted = session.isoformat()

    await _beat(db, BEAT_NAME, session=session.isoformat(), built_from=previous.isoformat(),
                groups=built["groups"], symbols=len(built["symbols"]),
                backfill={"pairs": len(pairs), "requests": bf["requests"],
                          "written": len(bf["written"]), "skipped": bf["skipped"],
                          "empty": len(bf["empty"]),
                          "iex_uncorrected": len(bf["iex_uncorrected"]),
                          "failed": bf["failed"][:5], "remaining": bf["remaining"],
                          "fail_closed": fail_closed},
                done=done, alerted=alerted)
    return "done" if done else "pending"


async def _intraday_nightly() -> str:
    try:
        from quant.data import intraday_data as qid
        from quant.data import registry as qreg
        from quant.research import intraday_backtest as ib

        async with celery_engine.connect() as lock_conn:
            got = (await lock_conn.execute(text("select pg_try_advisory_xact_lock(:k)"),
                                           {"k": INTRADAY_LOCK_KEY})).scalar()
            if not got:
                return "busy"
            async with CeleryAsyncSessionLocal() as db:
                result = await run_nightly(db, now=datetime.now(timezone.utc), registry=qreg,
                                           shortlist=ib.shortlist_for_session,
                                           backfill=qid.backfill_sessions, notify=_notify)
                await db.commit()
        return result
    except Exception:
        logger.exception("intraday.nightly failed")
        return "error"


@shared_task(name="intraday.nightly", soft_time_limit=1500, time_limit=1600)
def intraday_nightly():
    return _run_async(_intraday_nightly())
