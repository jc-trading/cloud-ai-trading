"""Three-tier quant schedule (R1-2) — celery wrappers around simledger.cycles.

  quant.signal_cycle    daily post-close: sync bars -> publish recommendations
                        for the next session -> daily exit pass -> protections
                        -> snapshots
  quant.entry_cycle     just after the open (both DST beat slots; the wrong one
                        no-ops): protections gate -> book shortlisted entries
  quant.position_cycle  every 5 min in RTH: stop-breach checks at live quotes
  quant.heartbeat       every minute: liveness row the watchdog reads
  market.eod_correction 01:30 UTC: re-source the closed session's 1min bars
                        from SIP over the IEX stream's files (方案 Phase 8)

Every task: XNYS-calendar gated, outbound calls carry timeouts (07-04 incident
rules), never raises out of the worker, and the position/entry writers are the
ONLY writers of the sim-ledger trading tables (single-writer discipline).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.market.finnhub_client import FinnhubClient
from app.modules.notifications import TelegramNotifier
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, Recommendation
from app.modules.simledger.service import SimLedgerService
from app.modules.simledger.settings import effective_settings

logger = logging.getLogger(__name__)
async def _notify(message: str) -> bool:
    """Event notification — never lets a Telegram failure break the cycle."""
    try:
        # Plain text: messages carry dynamic names (signal_cycle, symbols)
        # whose _ would 400 Telegram's Markdown parser; no formatting is used.
        return bool(await TelegramNotifier().send_message(message, parse_mode=None))
    except Exception:
        logger.warning("telegram notify failed", exc_info=True)
        return False


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


async def _beat_meta(db, name: str) -> dict:
    """The heartbeat row's last meta — read before _beat overwrites it, so a
    once-a-day alert flag can survive across cycles without another table."""
    row = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == name)
    )).scalar_one_or_none()
    return dict(row.meta or {}) if row is not None else {}


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


async def _stamp_open_price_alert(db, today) -> None:
    """Record that today's missing-open-price alert was delivered. Its own tiny
    commit so the flag is only ever written after a successful send."""
    row = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == "entry_cycle")
    )).scalar_one_or_none()
    if row is None:
        return
    row.meta = {**(row.meta or {}), "open_price_alert": str(today)}
    await db.commit()


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
    """Books the 对照账户's shortlisted entries; the account's entry_mode picks
    the semantics.

    open_once (the default) acts once inside [open+16min, open+90min) and fills
    at the session's 09:30 SIP open — the fixed_oos scoreboard's own semantics,
    no live quote involved. A name whose 09:30 bar never arrived does not enter
    today (fail-closed) and is alerted once. intraday_ladder keeps the v3.1
    behaviour: every 15 min through RTH at the live quote, chase-capped. Either
    way the idempotency key caps it at one entry per symbol per day."""
    from quant.data import fetch as qfetch

    now_et = _now_et()
    if not _in_rth(now_et):
        return "skipped: outside RTH"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            account = await _system_account(db)
            if account is None:
                return "no system account"
            today = now_et.date()
            mode = cycles.account_entry_mode(account)
            if mode == cycles.ENTRY_MODE_OPEN_ONCE \
                    and not cycles.in_open_once_window(now_et):
                # still beat: the beat runs every 15 min, so most RTH cycles land
                # outside this window and the watchdog must not read the silence
                # as a stalled entry cycle
                await _beat(db, "entry_cycle", skipped="outside_open_once_window")
                await db.commit()
                return "skipped: outside the open_once window"
            state = await cycles.get_safety_state(db, account)
            blocked = cycles.entries_blocked_reason(state, today=today)
            if blocked:
                logger.warning("entry_cycle blocked: %s", blocked)
                return f"blocked: {blocked}"
            # A2 fail-closed: ZERO Recommendation rows for today means last
            # night's signal cycle failed or was gated — book NOTHING. Runs
            # every 15 min now, so LOG only (signal_cycle already Telegrams the
            # fail-closed once) to avoid ~26 alerts/day.
            any_rec = (await db.execute(
                select(Recommendation.id)
                .where(Recommendation.trade_date == today).limit(1)
            )).scalar_one_or_none()
            if any_rec is None:
                await _beat(db, "entry_cycle", fail_closed="no recommendations")
                await db.commit()
                logger.warning("entry_cycle fail-closed: no recommendations for %s",
                               today)
                return "fail-closed: no recommendations"

            eff = await effective_settings(db)
            prior = await _beat_meta(db, "entry_cycle")
            open_prices = None
            missing: list[str] = []
            if mode == cycles.ENTRY_MODE_OPEN_ONCE:
                wanted = await cycles.shortlist_symbols(db, today)
                try:
                    open_prices = await asyncio.to_thread(
                        qfetch.session_open_prices, wanted, today)
                except Exception:
                    logger.exception("entry_cycle: 09:30 open prices unavailable "
                                     "for %s — no entries this session", today)
                    open_prices = {}
                missing = [s for s in wanted if s not in open_prices]

            client = FinnhubClient()
            booked = await cycles.run_entries(
                db, account, today, quote_fn=_quote_fn(client), entry_mode=mode,
                open_prices=open_prices, risk_pct=eff.per_trade_risk_pct,
                chase_cap=eff.intraday_entry_chase_cap,
                max_slots=eff.max_concurrent_slots)

            alerted_on = prior.get("open_price_alert")
            meta = {"booked": booked, "mode": mode}
            if alerted_on is not None:
                # _beat replaces meta wholesale, so today's alert flag has to be
                # carried forward or it survives exactly one cycle
                meta["open_price_alert"] = alerted_on
            if missing:
                meta["missing_open"] = missing
                logger.error("entry_cycle: no 09:30 open price for %s — not "
                             "entered today", ", ".join(missing))
            await _beat(db, "entry_cycle", **meta)
            await db.commit()

            if eff.rejected:
                await _notify("⚠️ master_settings 有非法行（只允许收紧）— 该项用常量: "
                              + "; ".join(eff.rejected))
            if missing and alerted_on != str(today):
                # the once-a-day flag follows a send that actually LANDED
                # (watchdog._alert's rule): stamping it first loses the whole
                # day's alert to one failed send, which is when it matters most
                sent = await _notify(
                    "⛔ 对照账户 open_once fail-closed: 拿不到 09:30 开盘价, "
                    f"今天不进这些票: {', '.join(missing)}")
                if sent:
                    await _stamp_open_price_alert(db, today)
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
    from quant.data import corporate_actions as qactions
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
    # corporate actions FIRST: get_bars adjusts on read, so bars synced before
    # tonight's split is cached would be scored RAW (the HOOD 08-15 mode).
    # Never fatal — build_recommendations fails closed per symbol on any name
    # still showing an unadjusted jump.
    try:
        qactions.sync_universe(sync_set,
                               progress=lambda m: logger.info("signal_cycle: %s", m))
    except Exception:
        logger.warning("signal_cycle: corporate action sync failed", exc_info=True)

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
            eff = await effective_settings(db)
            batch = cycles.RecommendationBatch()
            if sync_fail_closed:
                recs, n = [], 0
                logger.error("signal_cycle fail-closed: %d/%d symbols failed "
                             "bar sync — recommendations NOT published",
                             failed, len(symbols))
            else:
                batch = cycles.build_recommendations(
                    symbols, today, bars_fn=bars_fn, sectors=sectors,
                    funnel_params=replace(cycles.RECOMMENDED_FUNNEL,
                                          min_confidence=eff.min_confidence))
                recs = batch.rows
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
            exits = cycles.ExitPass()
            drift: list[str] = []
            if account is not None:
                if cycles.account_entry_mode(account) == cycles.ENTRY_MODE_OPEN_ONCE:
                    # sentinel: today's fills were priced off the SIP 09:30 1min
                    # bar, so the stored daily open must agree with them
                    drift = await cycles.open_fill_drift(db, account, today,
                                                         bars_fn=bars_fn)
                exits = await cycles.daily_exit_management(
                    db, account, today, bars_fn=bars_fn)
                # snapshot + protections on end-of-day marks: the session's own
                # daily close (what the backtest marks equity at), quote as the
                # fallback. The memo means the close costs no extra bar read.
                positions = await SimLedgerService.get_open_positions(db, account.id)
                client = FinnhubClient()
                quotes = await cycles.closing_marks(
                    positions, today, bars_fn=bars_fn, quote_fn=_quote_fn(client))
                equity = SimLedgerService.equity(account, positions, quotes)
                await cycles.update_protections(
                    db, account, equity, today,
                    pause_pct=eff.daily_loss_pause_pct,
                    halt_pct=eff.portfolio_drawdown_halt_pct)
                await SimLedgerService.snapshot(db, account, today, quotes,
                                                positions=positions,
                                                equity=equity)
            closed = exits.closed
            meta = {"recs": n, "explained": explained, "closed": closed,
                    "synced": synced, "failed": failed,
                    "scanned": batch.scanned, "stale": batch.stale,
                    "unadjusted": batch.unadjusted,
                    "unmarked": exits.unadjusted}
            if drift:
                meta["fill_drift"] = drift
            if eff.rejected:
                meta["settings_rejected"] = list(eff.rejected)
            if sync_fail_closed:
                meta["fail_closed"] = "bar sync failures"
            # the guards are only protective if a bad night is LOUD: a cycle that
            # scans 500 names and publishes none used to leave `recs: 0` and silence
            rec_issues: list[str] = []
            if not sync_fail_closed and batch.scanned:
                if n == 0:
                    rec_issues.append(
                        f"0 recommendations published from {batch.scanned} scanned "
                        f"({batch.stale} stale-bar, {batch.unadjusted} unadjusted)")
                elif batch.excluded > 0.2 * batch.scanned:
                    rec_issues.append(
                        f"{batch.excluded}/{batch.scanned} symbols excluded "
                        f"({batch.stale} stale-bar, {batch.unadjusted} unadjusted)")
            if exits.unadjusted:
                rec_issues.append("held positions NOT marked (RAW prices): "
                                  + ", ".join(exits.unadjusted))
            if rec_issues:
                meta["fail_closed"] = "; ".join(rec_issues)
            await _beat(db, "signal_cycle", **meta)
            await db.commit()
            if sync_fail_closed:
                await _notify("⛔ signal_cycle fail-closed: bar sync failed for "
                              f"{failed}/{len(sync_set)} symbols — no "
                              "recommendations published for the next session")
            elif rec_issues:
                await _notify("⛔ signal_cycle fail-closed: " + "; ".join(rec_issues))
            if eff.rejected:
                await _notify("⚠️ master_settings 有非法行（只允许收紧）— 该项用常量: "
                              + "; ".join(eff.rejected))
            if drift:
                await _notify(
                    f"⚠️ open_once 成交价对账超 {cycles.OPEN_FILL_DRIFT_BPS:.0f}bps"
                    " — 09:30 bar 与日线 open 不一致: " + "; ".join(drift))
            if closed:
                await _notify(f"📤 对照账户 daily exits: {', '.join(closed)}")
            if exits.data_end:
                await _notify(
                    "⚠️ 对照账户 data_end 强平 — no new daily bar for "
                    f"{cycles.DATA_END_STALE_SESSIONS}+ sessions, closed at the "
                    f"last known close: {', '.join(exits.data_end)}")
            shortlist = [r["symbol"] for r in recs if r.get("shortlist_rank")]
            if shortlist:
                await _notify("🔎 明日 shortlist: " + ", ".join(shortlist[:10]))
            return f"recs={n} closed={closed} scanned={len(symbols)} synced={synced}/{len(sync_set)}"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.signal_cycle failed")
        return "error"


@shared_task(name="market.eod_correction", soft_time_limit=1500, time_limit=1700)
def eod_correction():
    """Post-close: re-source the last completed ET session's 1min bars from SIP
    (provider flips alpaca:iex -> alpaca:sip), sync that day's 1hour bars, and
    record the IEX-vs-SIP feed comparison + completeness in
    market_data_files.meta. Thin wrapper — the logic lives in
    quant.data.eod_correction so it can be replayed for a past day with
    `python -m quant.data.eod_correction --date YYYY-MM-DD`."""
    from quant.data import eod_correction as qeod

    try:
        report = qeod.run()
    except Exception:
        logger.exception("market.eod_correction failed")
        return "error"
    if report.skipped:
        return f"skipped: {report.skipped}"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            meta = {"day": str(report.day), "symbols": len(report.symbols),
                    "corrected": len(report.corrected), "partial": len(report.partial),
                    "stale": len(report.stale), "rows": report.rows}
            if report.fail_closed:
                meta["fail_closed"] = report.fail_closed
            await _beat(db, "market_eod_correction", **meta)
            await db.commit()
            if report.fail_closed:
                await _notify("⛔ market.eod_correction fail-closed: "
                              f"{report.fail_closed} for {report.day} — no 1min "
                              "file overwritten, rows marked stale")
            elif report.partial:
                await _notify(f"⚠️ {report.day} 1min incomplete for "
                              + ", ".join(report.partial[:10]))
    try:
        _run_async(_do())
    except Exception:
        logger.exception("market.eod_correction bookkeeping failed")
    return report.summary()
