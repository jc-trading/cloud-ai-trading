"""P5a-2 1m backtest requests against real PostgreSQL (rolled back): sessions
instead of years, the 2-year 1min horizon, the 1m snapshot (costs + run), and
the daily snapshot left exactly as it was."""

import json
from dataclasses import asdict
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

import app.models_registry  # noqa: F401
from quant import config as qconfig
from quant.backtest.intraday_costs import IntradayCostModel
from quant.engine.strategies import GRIDS, M1_ORB_KEY, V1_COMPOSITE_KEY

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.backtests import service
from app.modules.backtests.router import router
from app.modules.strategy_groups.service import create_group
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/backtests"
SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def _as_json(obj) -> dict:
    return json.loads(json.dumps(asdict(obj)))


@pytest_asyncio.fixture
async def api(pg_session_rollback, monkeypatch, tmp_path):
    db = pg_session_rollback
    admin_id = (await db.execute(select(User.id).where(
        User.role == UserRole.SUPER_ADMIN).limit(1))).scalar_one()
    user = SimpleNamespace(id=admin_id, role=UserRole.SUPER_ADMIN, is_active=True)
    monkeypatch.setattr(qconfig, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(service.cycles, "now_et", lambda: NOW)
    monkeypatch.setattr(service.qscreen, "earliest_start", lambda spec: date(2016, 1, 1))

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


async def _group(api, key=M1_ORB_KEY, screener=SCREENER) -> str:
    g = await create_group(api.db, user_id=api.user.id, name=f"zz-p5a2-{uuid4().hex[:8]}",
                           strategy_key=key, params_json={}, screener_json=screener,
                           trade_settings_json={}, notes=None, starting_capital=2000,
                           status="paused")
    await api.db.commit()
    return str(g.id)


async def _post(api, gid, **over):
    body = {"group_id": gid, "kind": "single", "start": "2026-10-16", "end": "2026-10-22",
            **over}
    return await api.client.post(URL, json=body)


async def _detail(api, gid, **over) -> str:
    r = await _post(api, gid, **over)
    assert r.status_code == 422, r.text
    return r.json()["detail"]


@pytest.mark.asyncio
async def test_a_1m_single_run_needs_five_sessions(api):
    gid = await _group(api)
    assert await _detail(api, gid, start="2026-10-19") == \
        "a 1m single run needs >= 5 sessions, got 4"
    r = await _post(api, gid)
    assert r.status_code == 201, r.text
    assert r.json()["config_snapshot"]["run"] == {
        "kind": "single", "is_sessions": None, "oos_sessions": None, "grid": None}


@pytest.mark.asyncio
async def test_a_1m_run_cannot_start_before_the_two_year_horizon(api):
    gid = await _group(api)
    assert await _detail(api, gid, start="2024-10-22") == "1m runs: start must be >= 2024-10-23"
    r = await _post(api, gid, start="2024-10-23")
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"is_years": 3}, {"oos_years": 1},
                                  {"kind": "walk_forward", "is_years": 1}])
async def test_a_1m_run_refuses_years(api, over):
    gid = await _group(api)
    assert await _detail(api, gid, **over) == \
        "is_years / oos_years do not apply to 1m groups — use is_sessions / oos_sessions"


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"is_sessions": 60}, {"oos_sessions": 20}])
async def test_a_1d_run_refuses_sessions(api, over):
    gid = await _group(api, key=V1_COMPOSITE_KEY, screener={})
    assert await _detail(api, gid, start="2025-01-02", end="2025-12-31", **over) == \
        "is_sessions / oos_sessions apply to 1m groups only"


@pytest.mark.asyncio
async def test_a_1m_walk_forward_defaults_to_60_by_20_sessions(api):
    gid = await _group(api)
    r = await _post(api, gid, kind="walk_forward", start="2026-01-02")
    assert r.status_code == 201, r.text
    assert r.json()["config_snapshot"]["run"] == {
        "kind": "walk_forward", "is_sessions": 60, "oos_sessions": 20,
        "grid": {k: list(v) for k, v in GRIDS[M1_ORB_KEY].items()}}


@pytest.mark.asyncio
async def test_a_1m_snapshot_carries_the_intraday_cost_model(api):
    gid = await _group(api)
    r = await _post(api, gid)
    assert r.status_code == 201, r.text
    snap = r.json()["config_snapshot"]
    assert snap["costs"] == _as_json(IntradayCostModel()) and snap["timeframe"] == "1m"


@pytest.mark.asyncio
@pytest.mark.parametrize("over, detail", [
    ({"is_sessions": 19}, "is_sessions must be 20-250"),
    ({"is_sessions": 251}, "is_sessions must be 20-250"),
    ({"oos_sessions": 9}, "oos_sessions must be 10-120"),
    ({"oos_sessions": 121}, "oos_sessions must be 10-120"),
    ({"start": "2026-08-03", "is_sessions": 60, "oos_sessions": 20},
     "no walk-forward window fits 2026-08-03..2026-10-22 (IS 60 + OOS 20 sessions)"),
    ({"grid": {"funnel.min_confidence": [70.0]}},
     "grid.funnel.min_confidence: unknown knob (only signal.*)"),
])
async def test_a_1m_walk_forward_validates_its_sessions_and_grid(api, over, detail):
    gid = await _group(api)
    body = {"kind": "walk_forward", "start": "2026-01-02", **over}
    assert await _detail(api, gid, **body) == detail


@pytest.mark.asyncio
async def test_a_1m_walk_forward_stores_a_custom_window_and_grid(api):
    gid = await _group(api)
    r = await _post(api, gid, kind="walk_forward", start="2026-01-02", is_sessions=20,
                    oos_sessions=10, grid={"signal.range_minutes": [30, 5]})
    assert r.status_code == 201, r.text
    assert r.json()["config_snapshot"]["run"] == {
        "kind": "walk_forward", "is_sessions": 20, "oos_sessions": 10,
        "grid": {"signal.range_minutes": [30, 5]}}


@pytest.mark.asyncio
async def test_a_1m_single_run_refuses_a_grid(api):
    gid = await _group(api)
    assert await _detail(api, gid, grid={"signal.range_minutes": [5]}) == \
        "grid applies to walk_forward runs only"


@pytest.mark.asyncio
async def test_a_1d_snapshot_is_unchanged(api):
    gid = await _group(api, key=V1_COMPOSITE_KEY, screener={})
    r = await _post(api, gid, start="2025-01-02", end="2025-12-31")
    assert r.status_code == 201, r.text
    snap = r.json()["config_snapshot"]
    assert snap["run"] == {"kind": "single", "is_years": 3, "oos_years": 1, "grid": None}
    assert snap["costs"] == _as_json(service.CostModel()) and "min_tick" not in snap["costs"]
    r = await _post(api, gid, kind="walk_forward", start="2018-01-02", end="2025-12-31",
                    oos_years=2)
    assert r.status_code == 201, r.text
    assert r.json()["config_snapshot"]["run"] == {
        "kind": "walk_forward", "is_years": 3, "oos_years": 2,
        "grid": {k: list(v) for k, v in GRIDS[V1_COMPOSITE_KEY].items()}}
