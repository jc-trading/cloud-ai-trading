"""Strategy-group fixtures for the cycle tests: an in-memory group #1 around a
test account, and a load_groups stand-in that returns it."""

from datetime import datetime, timezone
from uuid import uuid4

from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec

from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.service import GroupRuntime


def group_for(account, name="v1 对照", status="active", **overrides) -> StrategyGroup:
    spec = get_spec(V1_COMPOSITE_KEY)
    fields = dict(id=uuid4(), name=name, market="us", strategy_key=V1_COMPOSITE_KEY,
                  params_json=spec.default_params(), screener_json={},
                  trade_settings_json={}, risk_json={}, timeframe="1d",
                  status=status, sim_account_id=account.id, account_is_system=True,
                  created_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
                  archived_at=None, notes=None)
    fields.update(overrides)
    return StrategyGroup(**fields)


def runtime_for(account, name="v1 对照", status="active") -> GroupRuntime:
    group = group_for(account, name=name, status=status)
    return GroupRuntime(group, account, get_spec(group.strategy_key).build(group.params_json))


def groups_loader(*runtimes, refused=()):
    """A load_groups replacement honouring the status filter, like the real one."""
    async def load(db, statuses):
        return [rt for rt in runtimes if rt.group.status in statuses], list(refused)
    return load
