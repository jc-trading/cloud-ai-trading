"""Telegram command loop (R1-6): /status /pause /resume /kill.

A 1-minute poll of getUpdates (offset persisted in the heartbeats row
``telegram_updates``). Only messages from the CONFIGURED chat id are honored —
anything else is ignored and logged. Commands act on the 对照账户's safety
state; /kill additionally drops the HALT sentinel file, which blocks entries
even if the DB is unreachable (cycles check the file first).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import aiohttp
from celery import shared_task
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.config import settings
from app.modules.notifications import TelegramNotifier
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, Recommendation
from app.modules.simledger.service import SimLedgerService
from app.tasks.quant_tasks import _run_async, _system_account

logger = logging.getLogger(__name__)

_OFFSET_ROW = "telegram_updates"


async def _get_updates(offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getUpdates"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params={"offset": offset + 1, "timeout": 0},
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    return data.get("result", []) if data.get("ok") else []


async def _status_text(db) -> str:
    account = await _system_account(db)
    if account is None:
        return "no system account yet"
    positions = await SimLedgerService.get_open_positions(db, account.id)
    state = await cycles.get_safety_state(db, account)
    beats = {r.name: r.last_beat_at for r in (
        await db.execute(select(HeartbeatRecord))).scalars().all()}
    recs = list((await db.execute(
        select(Recommendation.symbol)
        .where(Recommendation.trade_date >= date.today(),
               Recommendation.shortlist_rank.isnot(None))
        .order_by(Recommendation.shortlist_rank).limit(10))).scalars().all())

    pos_lines = "\n".join(
        f"  {p.symbol}: {float(p.shares):.4g} @ {float(p.avg_cost):.2f} "
        f"stop {float(p.stop):.2f}" for p in positions) or "  (none)"
    blocked = cycles.entries_blocked_reason(state, today=date.today())
    hb = ", ".join(f"{k} {v:%H:%M}" for k, v in sorted(beats.items()))
    return (f"CAT 对照账户\n"
            f"cash: ${float(account.cash):,.2f}\n"
            f"positions:\n{pos_lines}\n"
            f"entries: {'🚫 ' + blocked if blocked else '✅ allowed'}\n"
            f"next shortlist: {', '.join(recs) or '(none)'}\n"
            f"heartbeats: {hb or '(none)'}")


async def _handle_command(db, text: str) -> str:
    cmd = text.strip().split()[0].lower().split("@")[0]
    account = await _system_account(db)
    if cmd == "/status":
        return await _status_text(db)
    if account is None:
        return "no system account yet"
    state = await cycles.get_safety_state(db, account, create=True)
    if cmd == "/pause":
        state.paused_until = date.today() + timedelta(days=30)
        state.reason = "manual /pause"
        return "⏸ entries paused for 30 days — /resume to lift"
    if cmd == "/resume":
        state.paused_until = None
        state.halted = False
        state.halted_until = None
        state.reason = "manual /resume"
        try:
            import os
            os.remove(cycles.HALT_SENTINEL)
        except FileNotFoundError:
            pass
        return "▶️ entries resumed (pause/halt cleared, sentinel removed)"
    if cmd == "/kill":
        import os
        os.makedirs(os.path.dirname(cycles.HALT_SENTINEL), exist_ok=True)
        with open(cycles.HALT_SENTINEL, "w") as f:
            f.write(f"killed via telegram {datetime.now(timezone.utc).isoformat()}\n")
        state.halted = True
        state.halted_until = None
        state.reason = "manual /kill"
        return "🛑 KILLED — HALT sentinel written; entries refused even if the DB dies. /resume to restart"
    return "commands: /status /pause /resume /kill"


@shared_task(name="quant.telegram_poll", soft_time_limit=45, time_limit=55)
def telegram_poll():
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return "telegram not configured"

    async def _do():
        async with CeleryAsyncSessionLocal() as db:
            row = (await db.execute(
                select(HeartbeatRecord).where(HeartbeatRecord.name == _OFFSET_ROW)
            )).scalar_one_or_none()
            offset = int((row.meta or {}).get("offset", 0)) if row else 0
            updates = await _get_updates(offset)
            handled = 0
            notifier = TelegramNotifier()
            for u in updates:
                offset = max(offset, int(u.get("update_id", 0)))
                msg = u.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text") or ""
                if chat_id != str(settings.TELEGRAM_CHAT_ID):
                    if text:
                        logger.warning("telegram: ignoring message from chat %s", chat_id)
                    continue
                if not text.startswith("/"):
                    continue
                # review #11: one failing command must never wedge the poll —
                # report the failure, advance the offset, keep serving
                try:
                    reply = await _handle_command(db, text)
                except Exception as e:
                    logger.exception("telegram command failed: %s", text)
                    reply = f"⚠️ command failed: {e}"
                # Plain text: /status carries heartbeat names (position_cycle
                # etc.) and error replies carry raw exception text — both 400
                # Telegram's Markdown parser when underscores are unpaired.
                await notifier.send_message(reply, parse_mode=None)
                handled += 1
            from app.tasks.quant_tasks import _beat
            await _beat(db, _OFFSET_ROW, offset=offset)   # review #18: one upsert impl
            await db.commit()
            return f"handled {handled} command(s)" if handled else "no commands"
    try:
        return _run_async(_do())
    except Exception:
        logger.exception("quant.telegram_poll failed")
        return "error"
