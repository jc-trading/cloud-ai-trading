"""Which symbols the system currently cares about, for ranking news items."""

from sqlalchemy import and_, func, select

from app.modules.simledger.models import Recommendation, SimAccount, SimPosition
from app.modules.strategy_groups.models import (
    MANAGED_STATUSES, SIGNAL_STATUSES, StrategyGroup
)
from app.tasks.quant_tasks import _system_watchlist

TIER_HELD, TIER_SHORTLIST, TIER_POOL = 0, 1, 2


async def relevance_map(db) -> dict[str, tuple[int, str]]:
    """symbol -> (tier, label); a symbol reached by several paths keeps its
    lowest tier. held = open lot in a managed group's account · shortlist = an
    active group's latest shortlist · pool = any managed group's latest
    recommendation rows, or its account owner's stock watchlist."""
    out: dict[str, tuple[int, str]] = {}

    def add(symbol: str, tier: int, label: str) -> None:
        symbol = symbol.upper()
        if symbol not in out or tier < out[symbol][0]:
            out[symbol] = (tier, label)

    groups = (await db.execute(
        select(StrategyGroup.id, StrategyGroup.status, StrategyGroup.sim_account_id)
        .where(StrategyGroup.status.in_(MANAGED_STATUSES))
    )).all()
    if not groups:
        return out
    account_ids = [g.sim_account_id for g in groups]
    signal_groups = {g.id for g in groups if g.status in SIGNAL_STATUSES}

    for symbol in (await db.execute(
        select(SimPosition.symbol)
        .where(SimPosition.status == "open", SimPosition.account_id.in_(account_ids))
    )).scalars():
        add(symbol, TIER_HELD, "held")

    latest = (
        select(Recommendation.group_id, func.max(Recommendation.trade_date).label("d"))
        .where(Recommendation.group_id.in_([g.id for g in groups]))
        .group_by(Recommendation.group_id)
        .subquery()
    )
    for rec in (await db.execute(
        select(Recommendation.group_id, Recommendation.symbol, Recommendation.shortlist_rank)
        .join(latest, and_(Recommendation.group_id == latest.c.group_id,
                           Recommendation.trade_date == latest.c.d))
    )).all():
        if rec.group_id in signal_groups and rec.shortlist_rank is not None:
            add(rec.symbol, TIER_SHORTLIST, "shortlist")
        else:
            add(rec.symbol, TIER_POOL, "pool")

    for account in (await db.execute(
        select(SimAccount).where(SimAccount.id.in_(account_ids))
    )).scalars():
        for symbol in await _system_watchlist(db, account):
            add(symbol, TIER_POOL, "watchlist")
    return out
