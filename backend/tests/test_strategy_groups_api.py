"""Strategy-group API (P1 Batch 2): /strategy-groups, and the required
``group_id`` on /sim/recommendations, /sim/group-account and /nightwatch/log.

There is no default group, so a missing group_id is a 422 and an unknown one a
404. Runs against real PostgreSQL: group #1 is only read. Every request runs in
a session whose commit is a flush and which is rolled back afterwards, so the
practice account /sim/account may create and the archived second group the
isolation test seeds never reach the database.
"""

import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.models_registry  # noqa: F401
from quant.engine.strategies import V1_COMPOSITE_KEY

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import UserRole
from app.modules.nightwatch.router import router as nightwatch_router
from app.modules.simledger import router as sim_router_mod
from app.modules.simledger.models import Recommendation, SafetyState, SimAccount
from app.modules.simledger.service import _dec
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.router import router as groups_router
from tests._groups import group_for

GROUP_ENDPOINTS = ["/api/v1/sim/recommendations", "/api/v1/sim/group-account",
                   "/api/v1/nightwatch/log"]


def _no_pg(reason):
    if os.environ.get("CAT_NO_PG") == "1":
        pytest.skip(reason)
    pytest.fail(f"{reason} — run `make test-backend` (it exports the URL from "
                ".env), or set CAT_NO_PG=1 to acknowledge the gap")


def _group_one():
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url or not os.environ.get("DATABASE_URL"):
        _no_pg("DATABASE_URL_SYNC / DATABASE_URL not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT g.id, g.name, g.sim_account_id, a.user_id "
                "FROM strategy_groups g JOIN sim_accounts a ON a.id = g.sim_account_id "
                "ORDER BY g.created_at, g.id LIMIT 1")).first()
    except Exception as exc:
        _no_pg(f"PostgreSQL unreachable: {exc}")
    finally:
        engine.dispose()
    if row is None:
        pytest.fail("no strategy group in the database — migration 019 not applied?")
    return SimpleNamespace(id=row[0], name=row[1], account_id=row[2], user_id=row[3])


@pytest.fixture()
def ctx():
    g1 = _group_one()
    state = {"seed": None}

    async def _db():
        if "engine" not in state:
            state["engine"] = create_async_engine(os.environ["DATABASE_URL"],
                                                  poolclass=pool.NullPool)
        async with AsyncSession(state["engine"], expire_on_commit=False) as session:
            session.commit = session.flush
            try:
                if state["seed"] is not None:
                    await state["seed"](session)
                yield session
            finally:
                await session.rollback()

    user = SimpleNamespace(id=g1.user_id, role=UserRole.BASIC, is_active=True)
    api = FastAPI()
    for r in (groups_router, sim_router_mod.router, nightwatch_router):
        api.include_router(r, prefix="/api/v1")
    api.dependency_overrides[get_db] = _db
    api.dependency_overrides[get_current_user] = lambda: user

    with TestClient(api) as client:
        yield SimpleNamespace(client=client, g1=g1, state=state)


class TestNoDefaultGroup:
    @pytest.mark.parametrize("url", GROUP_ENDPOINTS)
    def test_missing_group_id_is_422(self, ctx, url):
        r = ctx.client.get(url)
        assert r.status_code == 422
        assert any(e["loc"][-1] == "group_id" for e in r.json()["detail"])

    @pytest.mark.parametrize("url", GROUP_ENDPOINTS + ["/api/v1/strategy-groups/{id}"])
    def test_unknown_group_is_404(self, ctx, url):
        gid = uuid4()
        r = ctx.client.get(url.format(id=gid), params={"group_id": str(gid)})
        assert r.status_code == 404

    def test_malformed_group_id_is_422(self, ctx):
        assert ctx.client.get(GROUP_ENDPOINTS[0],
                              params={"group_id": "not-a-uuid"}).status_code == 422
        assert ctx.client.get("/api/v1/strategy-groups/not-a-uuid").status_code == 422


class TestStrategyGroups:
    def test_list_starts_with_group_one(self, ctx):
        rows = ctx.client.get("/api/v1/strategy-groups").json()
        assert rows[0]["id"] == str(ctx.g1.id)
        assert set(rows[0]) == {"id", "name", "market", "strategy_key", "timeframe",
                                "status", "sim_account_id", "created_at",
                                "archived_at", "notes"}

    def test_status_filter(self, ctx):
        active = ctx.client.get("/api/v1/strategy-groups", params={"status": "active"})
        assert active.status_code == 200
        assert {g["status"] for g in active.json()} <= {"active"}
        assert ctx.client.get("/api/v1/strategy-groups",
                              params={"status": "bogus"}).status_code == 422

    def test_detail_has_schema_and_account(self, ctx):
        r = ctx.client.get(f"/api/v1/strategy-groups/{ctx.g1.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["strategy_key"] == V1_COMPOSITE_KEY
        assert set(body["params_schema"]) == {"strategy", "funnel", "exits"}
        assert body["account"]["id"] == str(ctx.g1.account_id)
        assert set(body["account"]) == {"id", "name", "entry_mode", "starting_capital",
                                        "cash", "open_positions", "last_snapshot"}


class TestSimEndpoints:
    def test_account_has_only_mine(self, ctx):
        r = ctx.client.get("/api/v1/sim/account")
        assert r.status_code == 200
        assert set(r.json()) == {"mine"}

    def test_group_account_is_group_one_account(self, ctx):
        body = ctx.client.get("/api/v1/sim/group-account",
                              params={"group_id": str(ctx.g1.id)}).json()
        assert body["id"] == str(ctx.g1.account_id)
        assert (body["group_id"], body["group_name"]) == (str(ctx.g1.id), ctx.g1.name)

    def test_recommendations_carry_the_group(self, ctx):
        body = ctx.client.get("/api/v1/sim/recommendations",
                              params={"group_id": str(ctx.g1.id)}).json()
        assert (body["group_id"], body["group_name"]) == (str(ctx.g1.id), ctx.g1.name)
        assert body["oos_badge"] is not None

    def test_nightwatch_carries_the_group(self, ctx):
        body = ctx.client.get("/api/v1/nightwatch/log",
                              params={"group_id": str(ctx.g1.id)}).json()
        assert body["group"] == {"id": str(ctx.g1.id), "name": ctx.g1.name}


class TestSecondGroupIsolation:
    """An archived second group on its own system account, seeded inside the
    request's rolled-back session: every per-group read returns its data only."""

    def test_reads_only_the_seeded_group(self, ctx):
        today = date.today()
        tag = uuid4().hex[:8]
        acct = SimpleNamespace(id=uuid4(), name=f"zztest-{tag}")
        group = SimpleNamespace(id=uuid4(), name=f"zztest {tag}")

        async def seed(session):
            session.add(SimAccount(id=acct.id, user_id=ctx.g1.user_id, name=acct.name,
                                   is_system=True, starting_capital=_dec(1000),
                                   cash=_dec(1000)))
            await session.flush()
            session.add(StrategyGroup(id=group.id, name=group.name,
                                      strategy_key=V1_COMPOSITE_KEY, params_json={},
                                      status="archived",
                                      archived_at=datetime.now(timezone.utc),
                                      sim_account_id=acct.id))
            await session.flush()
            session.add(Recommendation(group_id=group.id, symbol="ZZTEST",
                                       trade_date=today, direction="up",
                                       confidence=_dec(50), phase="up",
                                       shortlist_rank=1))
            session.add(SafetyState(scope=str(acct.id), halted=True, reason="zztest"))
            await session.flush()

        ctx.state["seed"] = seed
        params = {"group_id": str(group.id)}

        feed = ctx.client.get("/api/v1/sim/recommendations", params=params).json()
        assert [i["symbol"] for i in feed["items"]] == ["ZZTEST"]
        assert feed["group_name"] == group.name
        assert feed["oos_badge"] is None

        acct_body = ctx.client.get("/api/v1/sim/group-account", params=params).json()
        assert acct_body["id"] == str(acct.id)
        assert acct_body["positions"] == [] and acct_body["equity_curve"] == []

        log = ctx.client.get("/api/v1/nightwatch/log", params=params).json()
        assert log["group"]["id"] == str(group.id)
        assert sum(n["signal"]["recommendations"] for n in log["nights"]) == 1
        assert [o for n in log["nights"] for o in n["orders"]] == []
        assert [x["symbol"] for n in log["nights"]
                for x in n["signal"]["shortlist"]] == ["ZZTEST"]
        assert all(n["equity"] is None for n in log["nights"])
        assert log["now"]["safety"]["halted"] is True
        assert log["now"]["safety"]["reason"] == "zztest"

        g1_log = ctx.client.get("/api/v1/nightwatch/log",
                                params={"group_id": str(ctx.g1.id)}).json()
        assert "ZZTEST" not in [x["symbol"] for n in g1_log["nights"]
                                for x in n["signal"]["shortlist"]]
        assert (g1_log["now"]["safety"] or {}).get("reason") != "zztest"

        g1_feed = ctx.client.get("/api/v1/sim/recommendations",
                                 params={"group_id": str(ctx.g1.id),
                                         "for_date": today.isoformat()}).json()
        assert "ZZTEST" not in [i["symbol"] for i in g1_feed["items"]]


class TestBadgeRule:
    """No DB: the badge belongs to the v1 chain at its deployed params only."""

    def _acct(self):
        return SimAccount(id=uuid4(), user_id=uuid4(), name="system", is_system=True)

    def test_v1_at_deployed_params_gets_the_badge(self):
        assert sim_router_mod._group_badge(group_for(self._acct())) is not None

    def test_v1_with_changed_params_gets_none(self):
        g = group_for(self._acct())
        g.params_json = {**g.params_json,
                         "funnel": {**g.params_json["funnel"], "min_confidence": 70.0}}
        assert sim_router_mod._group_badge(g) is None

    def test_int_vs_float_literal_counts_as_changed(self):
        g = group_for(self._acct())
        g.params_json = {**g.params_json,
                         "funnel": {**g.params_json["funnel"], "min_confidence": 65}}
        assert sim_router_mod._group_badge(g) is None

    def test_other_strategy_gets_none(self):
        assert sim_router_mod._group_badge(
            group_for(self._acct(), strategy_key="cat.other")) is None
