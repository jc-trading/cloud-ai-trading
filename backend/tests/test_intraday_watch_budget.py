"""P5b watch budget through the API against real PostgreSQL (rolled back): the
active 1m groups' ``rank.watch_max`` may sum to at most the stream cap minus
the reserved slots (30 - 2 = 28). Creating, cloning or activating a 1m group
past that is 409; paused / archived 1m groups and 1d groups never count."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, update

import app.models_registry  # noqa: F401
from quant.engine.strategies import M1_ORB_KEY, V1_COMPOSITE_KEY

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.router import router
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/strategy-groups"
SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}


@pytest_asyncio.fixture
async def api(pg_session_rollback, monkeypatch, tmp_path):
    db = pg_session_rollback
    await db.execute(update(StrategyGroup).where(
        StrategyGroup.status == "active", StrategyGroup.timeframe == "1m"
    ).values(status="paused"))
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


def _orb(watch_max, status="active"):
    return {"name": f"zz-p5b-{uuid4().hex[:8]}", "strategy_key": M1_ORB_KEY,
            "screener_json": SCREENER, "params_json": {"rank": {"watch_max": watch_max}},
            "status": status}


async def _create(api, body):
    r = await api.client.post(URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _fill(api):
    for n in (10, 10, 8):
        await _create(api, _orb(n))


@pytest.mark.asyncio
async def test_b1_budget_fills_to_exactly_28(api):
    for n in (10, 10, 8):
        r = await api.client.post(URL, json=_orb(n))
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_b2_one_more_active_slot_is_409(api):
    await _fill(api)
    r = await api.client.post(URL, json=_orb(1))
    assert r.status_code == 409
    assert r.json()["detail"] == "watch budget exceeded: 29 > 28"


@pytest.mark.asyncio
async def test_b3_over_budget_paused_group_is_created(api):
    await _fill(api)
    r = await api.client.post(URL, json=_orb(5, status="paused"))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_b4_activating_past_the_budget_is_409(api):
    await _fill(api)
    paused = await _create(api, _orb(5, status="paused"))
    r = await api.client.patch(f"{URL}/{paused['id']}/status", json={"status": "active"})
    assert r.status_code == 409
    assert r.json()["detail"] == "watch budget exceeded: 33 > 28"
    group = (await api.db.execute(
        select(StrategyGroup).where(StrategyGroup.id == paused["id"]))).scalar_one()
    assert group.status == "paused"


@pytest.mark.asyncio
async def test_b4_reactivating_within_the_budget_is_allowed(api):
    first = await _create(api, _orb(28))
    r = await api.client.patch(f"{URL}/{first['id']}/status", json={"status": "paused"})
    assert r.status_code == 200, r.text
    r = await api.client.patch(f"{URL}/{first['id']}/status", json={"status": "active"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_b5_active_clone_past_the_budget_is_409(api):
    await _fill(api)
    src = await _create(api, _orb(4, status="paused"))
    r = await api.client.post(f"{URL}/{src['id']}/clone",
                              json={"name": f"zz-p5b-{uuid4().hex[:8]}", "status": "active"})
    assert r.status_code == 409
    assert r.json()["detail"] == "watch budget exceeded: 32 > 28"


@pytest.mark.asyncio
async def test_b6_daily_group_ignores_a_full_budget(api):
    await _fill(api)
    r = await api.client.post(URL, json={"name": f"zz-p5b-{uuid4().hex[:8]}",
                                         "strategy_key": V1_COMPOSITE_KEY,
                                         "status": "active"})
    assert r.status_code == 201, r.text
    assert r.json()["timeframe"] == "1d"


@pytest.mark.asyncio
async def test_b7_paused_and_archived_1m_groups_do_not_count(api):
    await _create(api, _orb(20, status="paused"))
    archived = await _create(api, _orb(20, status="paused"))
    r = await api.client.patch(f"{URL}/{archived['id']}/status", json={"status": "archived"})
    assert r.status_code == 200, r.text
    r = await api.client.post(URL, json=_orb(28))
    assert r.status_code == 201, r.text
