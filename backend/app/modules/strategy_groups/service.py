"""Strategy group resolution for the cycles, Telegram and (Batch 2) the API.

``load_groups`` is THE way the trading chain finds its accounts: a group's
account is resolved only through ``strategy_groups.sim_account_id``. A group
whose settings P1 cannot honour is refused loudly rather than run with part of
its configuration silently ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quant.engine.strategies import StrategyParamsError, V1Config, get_spec

from app.modules.simledger.models import SimAccount
from app.modules.strategy_groups.models import StrategyGroup

logger = logging.getLogger(__name__)


class GroupRefused(Exception):
    pass


@dataclass(frozen=True)
class GroupRuntime:
    group: StrategyGroup
    account: SimAccount
    config: V1Config


def validate_group(group: StrategyGroup) -> V1Config:
    """The engine config for a group, or GroupRefused naming why P1 cannot run
    it: only the US daily v1 chain exists, and screener / trade settings / risk
    overrides are not implemented yet."""
    def refuse(reason: str) -> GroupRefused:
        return GroupRefused(f"{group.name}: {reason}")

    if group.market != "us":
        raise refuse(f"market {group.market!r} is not supported")
    if group.timeframe != "1d":
        raise refuse(f"timeframe {group.timeframe!r} is not supported")
    for field in ("screener_json", "trade_settings_json", "risk_json"):
        if getattr(group, field):
            raise refuse(f"{field} is not supported yet and must be empty")
    try:
        return get_spec(group.strategy_key).build(group.params_json)
    except StrategyParamsError as e:
        raise refuse(str(e)) from None


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
            runtimes.append(GroupRuntime(group, account, validate_group(group)))
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
