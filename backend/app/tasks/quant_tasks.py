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

from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.fundamentals.finnhub_client import FinnhubClient
from app.modules.notifications import TelegramNotifier
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, Recommendation
from app.modules.simledger.service import SimLedgerService

logger = logging.getLogger(__name__)
async def _notify(message: str) -> None:
    """Event notification — never lets a Telegram failure break the cycle."""
    try:
        # Plain text: messages carry dynamic names (signal_cycle, symbols)
        # whose _ would 400 Telegram's Markdown parser; no formatting is used.
        await TelegramNotifier().send_message(message, parse_mode=None)
    except Exception:
        logger.warning("telegram notify failed", exc_info=True)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_now_et = cycles.now_et
_in_rth = cycles.in_rth


def _quote_fn(client: FinnhubClient):
    def quote(symbol: str) -> cycles.QuoteReading | None:
        return cycles.finnhub_quote(client, symbol)
    return quote


async def _system_account(db):
    """Stable 对照账户 resolution (review #31: the is_system row wins, always —
    logic owned by SimLedgerService.system_account)."""
    return await SimLedgerService.system_account(db)


async def _system_watchlist(db, account) -> set[str]:
    """Stock symbols on the 对照账户 owner's watchlists — v3.1: these are always
    analyzed, ignoring the liquidity filter ('我关注的股永远在池子里')."""
    from app.modules.watchlist.models import Watchlist, WatchlistItem
    rows = (await db.execute(
        select(WatchlistItem.symbol)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .where(Watchlist.user_id == account.user_id,
               WatchlistItem.market_type == "stock")
    )).scalars().all()
    return {s.upper() for s in rows}


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
    """v3.1: runs every 15 min through RTH (not open-only). Each cycle re-checks
    shortlisted-but-not-yet-held names and books the ones whose live price is a
    good entry (run_entries' chase cap). One entry per symbol per day via the
    idempotency key, so repeated cycles never double-book."""
    now_et = _now_et()
    if not _in_rth(now_et):
        return "skipped: outside RTH"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            if account is None:
                return "no system account"
            state = await cycles.get_safety_state(db, account)
            blocked = cycles.entries_blocked_reason(state, today=now_et.date())
            if blocked:
                logger.warning("entry_cycle blocked: %s", blocked)
                return f"blocked: {blocked}"
            # A2 fail-closed: ZERO Recommendation rows for today means last
            # night's signal cycle failed or was gated — book NOTHING. Runs
            # every 15 min now, so LOG only (signal_cycle already Telegrams the
            # fail-closed once) to avoid ~26 alerts/day.
            any_rec = (await db.execute(
                select(Recommendation.id)
                .where(Recommendation.trade_date == now_et.date()).limit(1)
            )).scalar_one_or_none()
            if any_rec is None:
                await _beat(db, "entry_cycle", fail_closed="no recommendations")
                await db.commit()
                logger.warning("entry_cycle fail-closed: no recommendations for %s",
                               now_et.date())
                return "fail-closed: no recommendations"
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
    # The FULL index is synced so the liquidity ranking stays fresh (v3.1);
    # held symbols are ALWAYS synced even after leaving the index (review #27:
    # otherwise their bars freeze and the exit pass manages ghosts).
    held_syms: set[str] = set()
    watchlist_syms: set[str] = set()

    async def _context():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            held = watch = set()
            if account is not None:
                held = {p.symbol for p in
                        await SimLedgerService.get_open_positions(db, account.id)}
                watch = await _system_watchlist(db, account)
            return held, watch
    try:
        held_syms, watchlist_syms = _run_async(_context())
    except Exception:
        logger.warning("signal_cycle: context lookup failed", exc_info=True)

    index_syms = set(quniverse.constituents_on(today))
    sync_set = sorted(index_syms | set(qconfig.ETF_WHITELIST)
                      | held_syms | watchlist_syms)
    # review #1: batched sync — one Alpaca request per chunk; a failed chunk
    # falls back to per-symbol sync inside sync_daily_many
    synced, failed_syms = qfetch.sync_daily_many(sync_set)
    failed = len(failed_syms)
    logger.info("signal_cycle: bars synced for %d symbols (%d failed)", synced, failed)

    # v3.1: ANALYZE only the most-liquid slice of the index + the watchlist
    # (always, ignoring volume) + ETFs + any held name. The other ~80% of the
    # index is synced for ranking but not scanned — this is the "重点关注几只"
    # focus, done at the universe layer (no hand-deletion, survivorship-safe).
    liquid = set(quniverse.top_liquid(sorted(index_syms)))
    symbols = sorted(liquid | set(qconfig.ETF_WHITELIST)
                     | watchlist_syms | held_syms)
    logger.info("signal_cycle: analyzing %d symbols (top %.0f%% liquid %d + "
                "watchlist %d + held %d)", len(symbols),
                qconfig.LIQUIDITY_TOP_PCT * 100, len(liquid),
                len(watchlist_syms), len(held_syms))

    # A2 fail-closed: too many sync failures -> publish NOTHING (tomorrow's
    # entry cycle then fails closed on the empty Recommendation table). Exits,
    # protections and snapshots still run on whatever data is fresh — the
    # stale-bar guard in daily_exit_management protects positions.
    sync_fail_closed = bool(sync_set) and failed > 0.2 * len(sync_set)

    sectors = qsectors.load_sectors()

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            # review #2: one bar read per symbol across recommendations + exits
            bars_fn = cycles.memoized_bars_fn(today)
            if sync_fail_closed:
                recs, n = [], 0
                logger.error("signal_cycle fail-closed: %d/%d symbols failed "
                             "bar sync — recommendations NOT published",
                             failed, len(symbols))
            else:
                recs = cycles.build_recommendations(symbols, today,
                                                    bars_fn=bars_fn)
                for r in recs:
                    r["features"]["sector"] = sectors.get(r["symbol"], "unknown")
                n = await cycles.store_recommendations(db, recs)

            # v3 explanation layer (LLM, explanation-only): a one-line read on
            # the top-N names, booked into llm_calls. Never blocks the cycle —
            # call_llm swallows provider errors and leaves llm_explanation null.
            explained = 0
            if recs:
                from app.modules.llm.explain import explain_recommendations
                try:
                    explained = await explain_recommendations(
                        db, recs[0]["trade_date"], top_n=10)
                except Exception:
                    logger.warning("signal_cycle: explanation layer failed",
                                   exc_info=True)

            account = await _system_account(db)
            closed: list[str] = []
            if account is not None:
                closed = await cycles.daily_exit_management(db, account, today,
                                                            bars_fn=bars_fn)
                # snapshot + protections on end-of-day marks (review #3/#5:
                # concurrent quotes; positions/equity passed to snapshot).
                # Post-close quotes are >15min old by design, so this keeps the
                # price>0 check instead of the intraday staleness guard.
                positions = await SimLedgerService.get_open_positions(db, account.id)
                client = FinnhubClient()
                quote_map = await cycles.fetch_quotes(
                    client, [p.symbol for p in positions])
                quotes = {s: q.price for s, q in quote_map.items() if q.price > 0}
                equity = SimLedgerService.equity(account, positions, quotes)
                await cycles.update_protections(db, account, equity, today)
                await SimLedgerService.snapshot(db, account, today, quotes,
                                                positions=positions,
                                                equity=equity)
            meta = {"recs": n, "explained": explained, "closed": closed,
                    "synced": synced, "failed": failed}
            if sync_fail_closed:
                meta["fail_closed"] = "bar sync failures"
            await _beat(db, "signal_cycle", **meta)
            await db.commit()
            if sync_fail_closed:
                await _notify("⛔ signal_cycle fail-closed: bar sync failed for "
                              f"{failed}/{len(sync_set)} symbols — no "
                              "recommendations published for the next session")
            if closed:
                await _notify(f"📤 对照账户 daily exits: {', '.join(closed)}")
            shortlist = [r["symbol"] for r in recs if r.get("shortlist_rank")]
            if shortlist:
                await _notify("🔎 明日 shortlist: " + ", ".join(shortlist[:10]))
            return f"recs={n} closed={closed} scanned={len(symbols)} synced={synced}/{len(sync_set)}"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.signal_cycle failed")
        return "error"
