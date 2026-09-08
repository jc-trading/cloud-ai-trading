"""GET/POST/DELETE /api/v1/market/stream-symbols — the runtime subscription set.

Runs against the live PostgreSQL of the compose stack (the same one alembic and
market-stream read) because the point of the table is that it is operational
config, not a fixture. Every row it touches carries the ZZTEST prefix and is
deleted afterwards; skipped when no PG is reachable.
"""

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import UserRole
from app.modules.market.router import router as market_router

_PREFIX = "ZZTEST"
_URL = "/api/v1/market/stream-symbols"


def _sync_engine():
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        pytest.skip("DATABASE_URL_SYNC not set — needs the compose stack")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, poolclass=pool.NullPool)


def _cleanup():
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM market_stream_symbols WHERE symbol LIKE :p"),
                         {"p": f"{_PREFIX}%"})
    finally:
        engine.dispose()


@pytest.fixture()
def ctx():
    """TestClient + the role the overridden current user plays for this request."""
    engine = _sync_engine()
    try:
        engine.connect().close()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    engine.dispose()

    async_url = os.environ["DATABASE_URL"]
    state = {}

    async def _db():
        if "engine" not in state:
            state["engine"] = create_async_engine(async_url, poolclass=pool.NullPool)
        async with AsyncSession(state["engine"], expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    user = SimpleNamespace(role=UserRole.SUPER_ADMIN, is_active=True)
    api = FastAPI()
    api.include_router(market_router, prefix="/api/v1")
    api.dependency_overrides[get_db] = _db
    api.dependency_overrides[get_current_user] = lambda: user

    _cleanup()
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, user=user)
    _cleanup()


class TestRbac:
    @pytest.mark.parametrize("role", [UserRole.BASIC, UserRole.PREMIUM])
    def test_non_admin_is_refused(self, ctx, role):
        ctx.user.role = role
        assert ctx.client.get(_URL).status_code == 403
        assert ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A"}).status_code == 403
        assert ctx.client.delete(f"{_URL}/{_PREFIX}A").status_code == 403

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPER_ADMIN])
    def test_admin_is_allowed(self, ctx, role):
        ctx.user.role = role
        assert ctx.client.get(_URL).status_code == 200

    def test_anonymous_is_refused(self, ctx):
        ctx.client.app.dependency_overrides.pop(get_current_user)
        try:
            assert ctx.client.get(_URL).status_code == 422
        finally:
            ctx.client.app.dependency_overrides[get_current_user] = lambda: ctx.user


class TestCrud:
    def test_upsert_then_list(self, ctx):
        r = ctx.client.post(_URL, json={"symbol": f"{_PREFIX.lower()}a",
                                        "priority": 10, "note": "seed"})
        assert r.status_code == 201
        assert r.json() == {**r.json(), "symbol": f"{_PREFIX}A", "priority": 10,
                            "enabled": True, "note": "seed"}

        rows = ctx.client.get(_URL).json()
        mine = [x for x in rows if x["symbol"].startswith(_PREFIX)]
        assert [x["symbol"] for x in mine] == [f"{_PREFIX}A"]

    def test_upsert_updates_in_place(self, ctx):
        ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A", "priority": 10, "note": "seed"})
        r = ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A", "priority": 5, "note": "bumped"})
        assert r.status_code == 201
        assert (r.json()["priority"], r.json()["note"]) == (5, "bumped")

        mine = [x for x in ctx.client.get(_URL).json() if x["symbol"].startswith(_PREFIX)]
        assert len(mine) == 1

    def test_delete_disables_and_post_re_enables(self, ctx):
        ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A", "priority": 10})
        r = ctx.client.delete(f"{_URL}/{_PREFIX.lower()}a")
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        mine = [x for x in ctx.client.get(_URL).json() if x["symbol"] == f"{_PREFIX}A"]
        assert mine[0]["enabled"] is False

        assert ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A",
                                           "priority": 10}).json()["enabled"] is True

    def test_delete_unknown_symbol_is_404(self, ctx):
        assert ctx.client.delete(f"{_URL}/{_PREFIX}ZZZ").status_code == 404

    def test_priority_orders_the_list(self, ctx):
        ctx.client.post(_URL, json={"symbol": f"{_PREFIX}B", "priority": 50})
        ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A", "priority": 10})
        mine = [x["symbol"] for x in ctx.client.get(_URL).json()
                if x["symbol"].startswith(_PREFIX)]
        assert mine == [f"{_PREFIX}A", f"{_PREFIX}B"]

    def test_invalid_body_is_rejected(self, ctx):
        assert ctx.client.post(_URL, json={"priority": 10}).status_code == 422
        assert ctx.client.post(_URL, json={"symbol": f"{_PREFIX}A",
                                           "priority": -1}).status_code == 422
