"""P5a-2 1m strategy groups through the API against real PostgreSQL (rolled
back): the timeframe comes from the strategy spec, a 1m group needs screener
rules and 1m trade settings, the registry lists both timeframes, a clone stays
1m, and load_groups never hands a 1m group to the daily cycles."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

import app.models_registry  # noqa: F401
from quant.engine import trade_rules
from quant.engine.strategies import M1_ORB_KEY, V1_COMPOSITE_KEY, get_spec

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord
from app.modules.strategy_groups.models import MANAGED_STATUSES, StrategyGroup
from app.modules.strategy_groups.router import router
from app.modules.strategy_groups.service import load_groups
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/strategy-groups"
SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}


@pytest_asyncio.fixture
async def api(pg_session_rollback, monkeypatch, tmp_path):
    db = pg_session_rollback
    monkeypatch.setattr(cycles, "HALT_SENTINEL", str(tmp_path / "HALT"))
    monkeypatch.setattr(cycles, "in_rth", lambda *a, **kw: False)
    started = (await db.execute(select(HeartbeatRecord).where(
        HeartbeatRecord.name == "signal_cycle_started"))).scalar_one_or_none()
    if started is not None:
        started.meta = {"session": "2026-10-16", "seq": 0, "done": True}
        await db.commit()
    admin_id = (await db.execute(select(User.id).where(
        User.role == UserRole.SUPER_ADMIN).limit(1))).scalar_one()
    user = SimpleNamespace(id=admin_id, role=UserRole.SUPER_ADMIN, is_active=True)

    async def _db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        yield SimpleNamespace(client=client, db=db, user=user)


def _body(**over):
    return {"name": f"zz-p5a2-{uuid4().hex[:8]}", "strategy_key": M1_ORB_KEY,
            "screener_json": SCREENER, **over}


async def _group(api, gid) -> StrategyGroup:
    return (await api.db.execute(
        select(StrategyGroup).where(StrategyGroup.id == gid))).scalar_one()


@pytest.mark.asyncio
async def test_orb_with_a_screener_is_created_as_a_1m_group(api):
    r = await api.client.post(URL, json=_body())
    assert r.status_code == 201, r.text
    assert r.json()["timeframe"] == "1m"
    group = await _group(api, r.json()["id"])
    assert group.timeframe == "1m" and group.screener_json == SCREENER
    assert group.params_json == get_spec(M1_ORB_KEY).default_params()
    assert set(group.params_json) == {"signal", "rank"}


@pytest.mark.asyncio
async def test_orb_params_override_is_merged_onto_the_defaults(api):
    r = await api.client.post(URL, json=_body(params_json={"signal": {"range_minutes": 30}}))
    assert r.status_code == 201, r.text
    params = (await _group(api, r.json()["id"])).params_json
    assert params["signal"]["range_minutes"] == 30
    assert params["rank"] == get_spec(M1_ORB_KEY).default_params()["rank"]


@pytest.mark.asyncio
async def test_a_1m_group_without_screener_rules_is_422(api):
    r = await api.client.post(URL, json=_body(screener_json={}))
    assert r.status_code == 422
    assert r.json()["detail"] == "screener_json: 1m groups need screener rules"


@pytest.mark.asyncio
async def test_a_1m_group_refuses_swing(api):
    r = await api.client.post(URL, json=_body(trade_settings_json={"holding_style": "swing"}))
    assert r.status_code == 422
    assert r.json()["detail"] == \
        "trade_settings_json.holding_style: 1m groups support scalping only"


@pytest.mark.asyncio
async def test_a_1m_group_refuses_daily_only_keys(api):
    r = await api.client.post(URL, json=_body(trade_settings_json={"max_hold_days": 5}))
    assert r.status_code == 422
    assert r.json()["detail"] == "trade_settings_json: unknown key 'max_hold_days'"


@pytest.mark.asyncio
async def test_a_1m_group_stores_normalized_1m_trade_settings(api):
    r = await api.client.post(URL, json=_body(trade_settings_json={
        "stop_pct": 0.01, "entry_ttl_minutes": 3, "holding_style": "scalping"}))
    assert r.status_code == 201, r.text
    assert (await _group(api, r.json()["id"])).trade_settings_json == {"stop_pct": 0.01}


@pytest.mark.asyncio
async def test_registry_lists_both_timeframes(api):
    body = (await api.client.get(f"{URL}/registry")).json()
    assert body["timeframes"] == ["1d", "1m"]
    assert [(s["key"], s["timeframe"]) for s in body["strategies"]] == [
        (V1_COMPOSITE_KEY, "1d"), (M1_ORB_KEY, "1m")]
    assert body["trade_settings"]["keys_1m"] == trade_rules.registry_entry()["keys_1m"]


@pytest.mark.asyncio
async def test_a_clone_of_a_1m_group_stays_1m(api):
    src = (await api.client.post(URL, json=_body())).json()
    r = await api.client.post(f"{URL}/{src['id']}/clone",
                              json={"name": f"zz-p5a2-{uuid4().hex[:8]}",
                                    "params_json": {"rank": {"watch_max": 5}}})
    assert r.status_code == 201, r.text
    clone = await _group(api, r.json()["id"])
    assert clone.timeframe == "1m" and clone.screener_json == SCREENER
    assert clone.params_json["rank"]["watch_max"] == 5
    assert clone.params_json["signal"] == get_spec(M1_ORB_KEY).default_params()["signal"]


@pytest.mark.asyncio
async def test_a_v1_group_is_still_daily(api):
    r = await api.client.post(URL, json=_body(strategy_key=V1_COMPOSITE_KEY, screener_json={}))
    assert r.status_code == 201, r.text
    group = await _group(api, r.json()["id"])
    assert group.timeframe == "1d"
    assert set(group.params_json) == {"strategy", "funnel", "exits"}


@pytest.mark.asyncio
async def test_load_groups_skips_a_stored_1m_group(api):
    _, refused_before = await load_groups(api.db, MANAGED_STATUSES)
    r = await api.client.post(URL, json=_body())
    assert r.status_code == 201, r.text
    name = r.json()["name"]
    runtimes, refused = await load_groups(api.db, MANAGED_STATUSES)
    assert name not in [rt.group.name for rt in runtimes]
    assert refused == refused_before
    assert not any(x.startswith(f"{name}: ") for x in refused)
