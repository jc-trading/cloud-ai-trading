"""Strategy group resolution for the cycles, Telegram and (Batch 2) the API.

``load_groups`` is THE way the trading chain finds its accounts: a group's
account is resolved only through ``strategy_groups.sim_account_id``. A group
whose settings P1 cannot honour is refused loudly rather than run with part of
its configuration silently ignored.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quant.backtest.calibration import max_p, score_threshold
from quant.engine.screener import ScreenerSpec, ScreenerSpecError, parse_spec
from quant.engine.strategies import (REGISTRY, StrategyParamsError, V1Config, check_bounds,
                                     get_spec)
from quant.engine.trade_rules import (TradeSettings, TradeSettingsError, normalize,
                                      parse_trade_settings)

from app.modules.backtests.models import BacktestRun
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
    trade: TradeSettings | None = None
    score_floor: float | None = None


def validate_group(group: StrategyGroup) -> V1Config:
    """The engine config for a group, or GroupRefused naming why it cannot run:
    only the US daily v1 chain exists, the screener spec and trade settings must
    parse, and risk overrides are not implemented yet."""
    def refuse(reason: str) -> GroupRefused:
        return GroupRefused(f"{group.name}: {reason}")

    if group.market != "us":
        raise refuse(f"market {group.market!r} is not supported")
    expected = REGISTRY[group.strategy_key].timeframe if group.strategy_key in REGISTRY else "1d"
    if group.timeframe != expected:
        raise refuse(f"timeframe {group.timeframe!r} is not supported")
    try:
        screener = parse_spec(group.screener_json)
    except ScreenerSpecError as e:
        raise refuse(f"screener_json invalid: {e}") from None
    if group.timeframe == "1m" and screener is None:
        raise refuse("screener_json: 1m groups need screener rules")
    try:
        parse_trade_settings(group.trade_settings_json, timeframe=group.timeframe)
    except TradeSettingsError as e:
        raise refuse(str(e)) from None
    if group.risk_json:
        raise refuse("risk_json is not supported yet and must be empty")
    try:
        cfg = get_spec(group.strategy_key).build(group.params_json)
    except StrategyParamsError as e:
        raise refuse(str(e)) from None
    problems = check_bounds(group.strategy_key, cfg)
    if problems:
        raise refuse(f"params out of bounds: {'; '.join(problems)}")
    return cfg


def calibration_problem(run) -> str | None:
    if run is None:
        return "not found"
    if run.status != "succeeded":
        return f"status {run.status}"
    if run.kind != "walk_forward":
        return f"kind {run.kind}"
    status = (run.calibration_json or {}).get("status")
    if status != "ok":
        return f"calibration {status}"
    return None


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
            cfg = validate_group(group)
            # 1m groups belong to the intraday engine (P5c); the daily cycles never see them
            if group.timeframe != "1d":
                continue
            trade = parse_trade_settings(group.trade_settings_json, timeframe=group.timeframe)
            floor = None
            if trade is not None and trade.entry_prob_min is not None:
                run = await db.get(BacktestRun, UUID(trade.calibration_run_id))
                problem = calibration_problem(run)
                if problem is not None:
                    raise GroupRefused(f"{group.name}: calibration run "
                                       f"{trade.calibration_run_id} unusable: {problem}")
                floor = score_threshold(run.calibration_json, trade.entry_prob_min)
                if floor is None:
                    floor = math.inf
            runtimes.append(GroupRuntime(group, account, cfg, parse_spec(group.screener_json),
                                         trade=trade, score_floor=floor))
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
    return {f.name: asdict(getattr(cfg, f.name)) for f in fields(cfg)}


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
        timeframe = get_spec(strategy_key).timeframe
        params = normalize_params(strategy_key, params_json)
    except StrategyParamsError as e:
        raise GroupInvalid(str(e)) from None
    try:
        screener = parse_spec(screener_json)
    except ScreenerSpecError as e:
        raise GroupInvalid(str(e)) from None
    if timeframe == "1m" and screener is None:
        raise GroupInvalid("screener_json: 1m groups need screener rules")
    try:
        trade = parse_trade_settings(trade_settings_json, timeframe=timeframe)
    except TradeSettingsError as e:
        raise GroupInvalid(str(e)) from None
    trade_json = normalize(trade_settings_json, timeframe=timeframe)
    if status not in ("active", "paused"):
        raise GroupInvalid("status: must be active or paused")
    if not 500 <= starting_capital <= 1_000_000:
        raise GroupInvalid("starting_capital: must be between 500 and 1000000")
    if trade is not None and trade.entry_prob_min is not None:
        await _check_calibration(db, trade, StrategyGroup(
            strategy_key=strategy_key, params_json=params, screener_json=screener_json,
            trade_settings_json=trade_json, timeframe=timeframe, market="us"))
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
                          trade_settings_json=trade_json, risk_json={}, timeframe=timeframe,
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


async def _check_calibration(db: AsyncSession, trade: TradeSettings,
                             core_group: StrategyGroup) -> None:
    from app.modules.backtests.service import config_core, config_hash

    rid = trade.calibration_run_id
    p = trade.entry_prob_min
    run = await db.get(BacktestRun, UUID(rid))
    if run is None:
        raise GroupInvalid(f"trade_settings_json.calibration_run_id: run {rid} not found")
    problem = calibration_problem(run)
    if problem is not None:
        raise GroupConflict(f"calibration run {rid} unusable: {problem}")
    if (run.config_snapshot or {}).get("calibration_ref") is not None:
        raise GroupConflict(f"calibration run {rid} was itself filtered by entry_prob_min")
    if config_hash(config_core(core_group)) != run.config_hash:
        raise GroupConflict("calibration run was produced by a different config")
    if score_threshold(run.calibration_json, p) is None:
        raise GroupInvalid(f"trade_settings_json.entry_prob_min: no calibrated bucket reaches "
                           f"p={p} (max {round(max_p(run.calibration_json), 4)})")


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
                      trade_settings_json=None, starting_capital=None, status: str = "paused",
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
        trade_settings_json=(trade_settings_json if trade_settings_json is not None
                             else src.trade_settings_json),
        notes=notes, starting_capital=starting_capital,
        status=status)
    if archive_source:
        await set_status(db, src, "archived")
    return group
