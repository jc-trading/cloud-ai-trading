"""Strategy group resolution for the cycles, Telegram and (Batch 2) the API.

``load_groups`` is THE way the trading chain finds its accounts: a group's
account is resolved only through ``strategy_groups.sim_account_id``. A group
whose settings P1 cannot honour is refused loudly rather than run with part of
its configuration silently ignored.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quant.engine.screener import ScreenerSpec, ScreenerSpecError, parse_spec
from quant.engine.strategies import StrategyParamsError, V1Config, check_bounds, get_spec

from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, SafetyState, SimAccount
from app.modules.simledger.service import SimLedgerService, _dec
from app.modules.strategy_groups.models import MANAGED_STATUSES, StrategyGroup

logger = logging.getLogger(__name__)


class GroupRefused(Exception):
    pass


class GroupInvalid(ValueError):
    pass


class GroupConflict(Exception):
    pass


@dataclass(frozen=True)
class GroupRuntime:
    group: StrategyGroup
    account: SimAccount
    config: V1Config
    screener: ScreenerSpec | None = None


def validate_group(group: StrategyGroup) -> V1Config:
    """The engine config for a group, or GroupRefused naming why it cannot run:
    only the US daily v1 chain exists, the screener spec must parse, and trade
    settings / risk overrides are not implemented yet."""
    def refuse(reason: str) -> GroupRefused:
        return GroupRefused(f"{group.name}: {reason}")

    if group.market != "us":
        raise refuse(f"market {group.market!r} is not supported")
    if group.timeframe != "1d":
        raise refuse(f"timeframe {group.timeframe!r} is not supported")
    try:
        parse_spec(group.screener_json)
    except ScreenerSpecError as e:
        raise refuse(f"screener_json invalid: {e}") from None
    for field in ("trade_settings_json", "risk_json"):
        if getattr(group, field):
            raise refuse(f"{field} is not supported yet and must be empty")
    try:
        cfg = get_spec(group.strategy_key).build(group.params_json)
    except StrategyParamsError as e:
        raise refuse(str(e)) from None
    problems = check_bounds(group.strategy_key, cfg)
    if problems:
        raise refuse(f"params out of bounds: {'; '.join(problems)}")
    return cfg


async def load_groups(db: AsyncSession, statuses) -> tuple[list[GroupRuntime], list[str]]:
    rows = (await db.execute(
        select(StrategyGroup, SimAccount)
        .join(SimAccount, SimAccount.id == StrategyGroup.sim_account_id)
        .where(StrategyGroup.status.in_(statuses))
        .order_by(StrategyGroup.created_at, StrategyGroup.id)
    )).all()
    runtimes: list[GroupRuntime] = []
    refused: list[str] = []
    for group, account in rows:
        try:
            runtimes.append(GroupRuntime(group, account, validate_group(group),
                                         parse_spec(group.screener_json)))
        except GroupRefused as e:
            logger.error("strategy group refused: %s", e)
            refused.append(str(e))
    return runtimes, refused


async def get_group(db: AsyncSession, group_id) -> StrategyGroup | None:
    return await db.get(StrategyGroup, group_id)


async def _require_group(db: AsyncSession, group_id) -> StrategyGroup:
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="strategy group not found")
    return group


_HALTED = "HALT sentinel present (/kill) — /resume first"
_IN_FLIGHT = "signal_cycle is running — activate after it finishes"


def normalize_params(key: str, params_json) -> dict:
    cfg = get_spec(key).build(params_json)
    return {"strategy": asdict(cfg.strategy), "funnel": asdict(cfg.funnel),
            "exits": asdict(cfg.exits)}


async def platform_block_reason(db: AsyncSession) -> str | None:
    """Why no group may be activated right now: the /kill sentinel, or a
    Telegram /pause or /kill still recorded on a managed group's account."""
    if os.path.exists(cycles.HALT_SENTINEL):
        return _HALTED
    ids = (await db.execute(
        select(StrategyGroup.sim_account_id)
        .where(StrategyGroup.status.in_(MANAGED_STATUSES)))).scalars().all()
    if not ids:
        return None
    states = (await db.execute(
        select(SafetyState).where(SafetyState.scope.in_([str(i) for i in ids]))
    )).scalars().all()
    today = cycles.now_et().date()
    if any(s.reason == "manual /pause" and s.paused_until is not None
           and s.paused_until >= today for s in states):
        return "platform paused via Telegram /pause — /resume first"
    if any(s.reason == "manual /kill" and s.halted for s in states):
        return _HALTED
    return None


async def signal_cycle_in_flight(db: AsyncSession) -> bool:
    row = (await db.execute(
        select(HeartbeatRecord).where(HeartbeatRecord.name == "signal_cycle_started")
    )).scalar_one_or_none()
    return (row is not None and (row.meta or {}).get("done") is False
            and row.last_beat_at > datetime.now(timezone.utc) - cycles.STARTED_DEAD_AFTER)


async def create_group(db: AsyncSession, *, user_id, name: str, strategy_key: str,
                       params_json, screener_json, trade_settings_json, notes,
                       starting_capital: float, status: str) -> StrategyGroup:
    """Create a group and its dedicated system sim account. Every input is
    validated first (GroupInvalid / GroupConflict); the stored group then goes
    through validate_group, the same gate load_groups applies to the cycles."""
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise GroupInvalid("name: must be 1-100 characters")
    if any(ord(ch) < 0x20 for ch in name):
        raise GroupInvalid("name: must not contain control characters")
    try:
        params = normalize_params(strategy_key, params_json)
    except StrategyParamsError as e:
        raise GroupInvalid(str(e)) from None
    try:
        parse_spec(screener_json)
    except ScreenerSpecError as e:
        raise GroupInvalid(str(e)) from None
    if trade_settings_json != {}:
        raise GroupInvalid("trade_settings_json: not supported yet")
    if status not in ("active", "paused"):
        raise GroupInvalid("status: must be active or paused")
    if not 500 <= starting_capital <= 1_000_000:
        raise GroupInvalid("starting_capital: must be between 500 and 1000000")
    if status == "active":
        reason = await platform_block_reason(db)
        if reason is not None:
            raise GroupConflict(reason)
        if await signal_cycle_in_flight(db):
            raise GroupConflict(_IN_FLIGHT)
    taken = (await db.execute(
        select(StrategyGroup.id).where(StrategyGroup.name == name).limit(1)
    )).scalar_one_or_none()
    if taken is not None:
        raise GroupConflict("name already exists")

    gid = uuid4()
    acct = SimAccount(user_id=user_id, name=f"grp:{gid}", is_system=True,
                      starting_capital=_dec(starting_capital), cash=_dec(starting_capital))
    db.add(acct)
    await db.flush()
    group = StrategyGroup(id=gid, name=name, market="us", strategy_key=strategy_key,
                          params_json=params, screener_json=screener_json,
                          trade_settings_json={}, risk_json={}, timeframe="1d",
                          status=status, sim_account_id=acct.id, account_is_system=True,
                          notes=notes)
    db.add(group)
    await db.flush()
    await db.refresh(acct)
    await db.refresh(group)
    try:
        validate_group(group)
    except GroupRefused as e:
        raise GroupInvalid(str(e)) from None
    return group


async def set_status(db: AsyncSession, group: StrategyGroup, new: str) -> StrategyGroup:
    """Status transitions. Archiving needs a paused group with a flat book,
    outside RTH (+10 min): an entry cycle that loaded the group while it was
    still active could otherwise commit a fill onto an archived group.
    Activation is refused while the platform is paused/killed or a
    signal_cycle is running."""
    old = group.status
    if new == old:
        return group
    if old == "archived":
        raise GroupConflict("archived is terminal")
    if new == "archived":
        if old == "active":
            raise GroupConflict("pause the group first")
        now = cycles.now_et()
        if cycles.in_rth(now) or cycles.in_rth(now - timedelta(minutes=10)):
            raise GroupConflict("archive outside market hours (RTH + 10 min)")
        n = len(await SimLedgerService.get_open_positions(db, group.sim_account_id))
        if n:
            raise GroupConflict(f"archive requires a flat book: {n} open position(s)")
        group.status = "archived"
        group.archived_at = datetime.now(timezone.utc)
    elif new == "active":
        reason = await platform_block_reason(db)
        if reason is not None:
            raise GroupConflict(reason)
        if await signal_cycle_in_flight(db):
            raise GroupConflict(_IN_FLIGHT)
        group.status = "active"
    else:
        group.status = "paused"
    await db.flush()
    return group


async def clone_group(db: AsyncSession, src: StrategyGroup, *, user_id, name: str,
                      params_json=None, screener_json=None, notes=None,
                      starting_capital=None, status: str = "paused",
                      archive_source: bool = False) -> StrategyGroup:
    override = params_json or {}
    unknown = sorted(set(override) - set(src.params_json))
    if unknown:
        raise GroupInvalid(f"params_json: unknown section(s): {', '.join(unknown)}")
    bad = sorted(s for s, v in override.items() if not isinstance(v, dict))
    if bad:
        raise GroupInvalid(f"params_json: section(s) must be objects: {', '.join(bad)}")
    merged = {s: {**src.params_json[s], **override.get(s, {})} for s in src.params_json}
    if starting_capital is None:
        src_acct = await db.get(SimAccount, src.sim_account_id)
        starting_capital = float(src_acct.starting_capital)
    group = await create_group(
        db, user_id=user_id, name=name, strategy_key=src.strategy_key,
        params_json=merged,
        screener_json=screener_json if screener_json is not None else src.screener_json,
        trade_settings_json={}, notes=notes, starting_capital=starting_capital,
        status=status)
    if archive_source:
        await set_status(db, src, "archived")
    return group
