"""Three-tier quant schedule (R1-2) — celery wrappers around simledger.cycles.

  quant.signal_cycle    daily post-close: sync bars -> publish recommendations
                        for the next session -> daily exit pass -> protections
                        -> snapshots
  quant.entry_cycle     just after the open (both DST beat slots; the wrong one
                        no-ops): protections gate -> book shortlisted entries
  quant.position_cycle  every 5 min in RTH: stop-breach checks at live quotes
  quant.heartbeat       every minute: liveness row the watchdog reads

Every task: XNYS-calendar gated, outbound calls carry timeouts (07-04 incident
rules), never raises out of the worker, and the position/entry writers are the
ONLY writers of the sim-ledger trading tables (single-writer discipline).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.fundamentals.finnhub_client import FinnhubClient
from app.modules.notifications import TelegramNotifier
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, SafetyState
from app.modules.simledger.service import SimLedgerService

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

async def _notify(message: str) -> None:
    """Event notification — never lets a Telegram failure break the cycle."""
    try:
        await TelegramNotifier().send_message(message)
    except Exception:
        logger.warning("telegram notify failed", exc_info=True)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(_ET)


def _in_rth(now_et: datetime | None = None) -> bool:
    from quant.data import calendar as qcal

    now_et = now_et or _now_et()
    if not qcal.is_trading_day(now_et.date()):
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


def _quote_fn(client: FinnhubClient):
    def quote(symbol: str) -> cycles.QuoteReading | None:
        q = client.quote(symbol)
        if not q:
            return None
        at = datetime.fromtimestamp(int(q.get("t") or 0), tz=timezone.utc)
        return cycles.QuoteReading(price=float(q["c"]), at=at)
    return quote


async def _system_account(db):
    """Stable 对照账户 resolution (review #31: the is_system row wins, always —
    logic owned by SimLedgerService.system_account)."""
    return await SimLedgerService.system_account(db)


async def _beat(db, name: str, **meta) -> None:
    row = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == name)
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(HeartbeatRecord(name=name, last_beat_at=now, meta=meta or None))
    else:
        row.last_beat_at = now
        row.meta = meta or None


@shared_task(name="quant.heartbeat")
def heartbeat():
    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            await _beat(db, "worker", rth=_in_rth())
            await db.commit()
    try:
        _run_async(_do())
    except Exception:
        logger.exception("quant.heartbeat failed")


@shared_task(name="quant.position_cycle", soft_time_limit=240, time_limit=280)
def position_cycle():
    """5-min intraday stop pass on the system account."""
    if not _in_rth():
        return "skipped: outside RTH"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            if account is None:
                return "no system account"
            client = FinnhubClient()
            closed = await cycles.check_stops(db, account, quote_fn=_quote_fn(client))
            await _beat(db, "position_cycle", closed=closed)
            await db.commit()
            if closed:
                await _notify(f"🛑 对照账户 stop exit: {', '.join(closed)}")
            return f"closed: {closed}" if closed else "no breaches"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.position_cycle failed")
        return "error"


@shared_task(name="quant.entry_cycle", soft_time_limit=500, time_limit=560)
def entry_cycle():
    """Book shortlisted entries just after the open. Two beat slots cover both
    DST regimes — outside the first 40 minutes after the open this no-ops."""
    now_et = _now_et()
    if not _in_rth(now_et):
        return "skipped: outside RTH"
    minutes = now_et.hour * 60 + now_et.minute
    if not ((9 * 60 + 30) <= minutes < (10 * 60 + 10)):
        return "skipped: not the open window"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            if account is None:
                return "no system account"
            state = (await db.execute(
                select(SafetyState).where(SafetyState.scope == str(account.id))
            )).scalar_one_or_none()
            blocked = cycles.entries_blocked_reason(state, today=now_et.date())
            if blocked:
                logger.warning("entry_cycle blocked: %s", blocked)
                return f"blocked: {blocked}"
            client = FinnhubClient()
            booked = await cycles.run_entries(db, account, now_et.date(),
                                             quote_fn=_quote_fn(client))
            await _beat(db, "entry_cycle", booked=booked)
            await db.commit()
            if booked:
                await _notify(f"📈 对照账户 entries booked: {', '.join(booked)}")
            return f"booked: {booked}" if booked else "nothing to book"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.entry_cycle failed")
        return "error"


@shared_task(name="quant.signal_cycle", soft_time_limit=1500, time_limit=1700)
def signal_cycle():
    """Post-close: sync daily bars for the current point-in-time universe,
    publish next session's recommendations, run the daily exit pass on the
    system account, update protections, snapshot every sim account."""
    from quant import config as qconfig
    from quant.data import calendar as qcal
    from quant.data import fetch as qfetch
    from quant.data import sectors as qsectors
    from quant.data import universe as quniverse

    now_et = _now_et()
    today = now_et.date()
    if not qcal.is_trading_day(today):
        return "skipped: not a session"

    # 1) incremental bar sync (network, bounded per-symbol; failures skip).
    # Held symbols are ALWAYS synced even after leaving the index (review #27:
    # otherwise their bars freeze and the exit pass manages ghosts).
    held_syms: set[str] = set()

    async def _held():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            if account is not None:
                return {p.symbol for p in
                        await SimLedgerService.get_open_positions(db, account.id)}
            return set()
    try:
        held_syms = _run_async(_held())
    except Exception:
        logger.warning("signal_cycle: held-symbol lookup failed", exc_info=True)
    symbols = sorted(set(quniverse.constituents_on(today))
                     | set(qconfig.ETF_WHITELIST) | held_syms)
    synced = failed = 0
    for sym in symbols:
        try:
            qfetch.sync_daily(sym)
            synced += 1
        except Exception:
            failed += 1
    logger.info("signal_cycle: bars synced for %d symbols (%d failed)", synced, failed)

    sectors = qsectors.load_sectors()

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            recs = cycles.build_recommendations(symbols, today)
            for r in recs:
                r["features"]["sector"] = sectors.get(r["symbol"], "unknown")
            n = await cycles.store_recommendations(db, recs)

            account = await _system_account(db)
            closed: list[str] = []
            if account is not None:
                closed = await cycles.daily_exit_management(db, account, today)
                # snapshot + protections on end-of-day marks
                positions = await SimLedgerService.get_open_positions(db, account.id)
                client = FinnhubClient()
                qfn = _quote_fn(client)
                quotes = {}
                for p in positions:
                    q = qfn(p.symbol)
                    if q is not None and q.price > 0:
                        quotes[p.symbol] = q.price
                equity = SimLedgerService.equity(account, positions, quotes)
                await cycles.update_protections(db, account, equity, today)
                await SimLedgerService.snapshot(db, account, today, quotes)
            await _beat(db, "signal_cycle", recs=n, closed=closed,
                        synced=synced, failed=failed)
            await db.commit()
            if closed:
                await _notify(f"📤 对照账户 daily exits: {', '.join(closed)}")
            shortlist = [r["symbol"] for r in recs if r.get("shortlist_rank")]
            if shortlist:
                await _notify("🔎 明日 shortlist: " + ", ".join(shortlist[:10]))
            return f"recs={n} closed={closed} synced={synced}/{len(symbols)}"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.signal_cycle failed")
        return "error"
