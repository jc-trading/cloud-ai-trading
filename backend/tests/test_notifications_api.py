"""/api/v1/notifications/* plus the billing split on /llm/log and /nightwatch/log.

Runs against real PostgreSQL (migration 021 applied). Every row it creates
carries the ZZTEST prefix and is deleted afterwards. No message is ever sent:
the Telegram env is cleared and test channels point at unset secret keys.
"""

import os
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import UserRole
from app.modules.llm.router import router as llm_router
from app.modules.nightwatch.router import router as nightwatch_router
from app.modules.notifications.models import EVENT_TYPES
from app.modules.notifications.router import router as notifications_router

_P = "ZZTEST"
_BASE = "/api/v1/notifications"


def _no_pg(reason):
    if os.environ.get("CAT_NO_PG") == "1":
        pytest.skip(reason)
    pytest.fail(f"{reason} — run `make test-backend` (it exports the URL from "
                ".env), or set CAT_NO_PG=1 to acknowledge the gap")


def _sync_engine():
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url or not os.environ.get("DATABASE_URL"):
        _no_pg("DATABASE_URL_SYNC / DATABASE_URL not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, poolclass=pool.NullPool)


def _sql(stmt, params=None):
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(text(stmt), params or {})
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _cleanup():
    _sql("DELETE FROM notification_log WHERE channel_name LIKE :p", {"p": f"{_P}%"})
    _sql("DELETE FROM notification_channels WHERE name LIKE :p", {"p": f"{_P}%"})
    _sql("DELETE FROM llm_calls WHERE context LIKE :p", {"p": f"{_P}%"})


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")


@pytest.fixture()
def ctx():
    try:
        _sql("SELECT 1 FROM notification_channels LIMIT 1")
    except pytest.fail.Exception:
        raise
    except Exception as exc:
        _no_pg(f"PostgreSQL with migration 021 unreachable: {exc}")

    state = {}

    async def _db():
        if "engine" not in state:
            state["engine"] = create_async_engine(os.environ["DATABASE_URL"],
                                                  poolclass=pool.NullPool)
        async with AsyncSession(state["engine"], expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    user = SimpleNamespace(id=None, role=UserRole.SUPER_ADMIN, is_active=True)
    api = FastAPI()
    for r in (notifications_router, llm_router, nightwatch_router):
        api.include_router(r, prefix="/api/v1")
    api.dependency_overrides[get_db] = _db
    api.dependency_overrides[get_current_user] = lambda: user

    _cleanup()
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, user=user)
    _cleanup()


def _tg(name="A", **over):
    body = {"type": "telegram", "name": f"{_P}{name}", "secret_env_key": "TELEGRAM_BOT_TOKEN_ZZ",
            "config_json": {"chat_id_env": "TELEGRAM_CHAT_ID"}}
    body.update(over)
    return body


def _smtp(name="M", **over):
    body = {"type": "smtp", "name": f"{_P}{name}", "secret_env_key": "SMTP_APP_PASSWORD_ZZ",
            "config_json": {"host": "smtp.example.com", "port": 587,
                            "username_env": "SMTP_USER_ZZ", "to": ["ops@example.com"]},
            "enabled": False}
    body.update(over)
    return body


def _create(ctx, body):
    r = ctx.client.post(f"{_BASE}/channels", json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestRbac:
    @pytest.mark.parametrize("role", [UserRole.BASIC, UserRole.PREMIUM])
    def test_non_admin_is_refused_everywhere(self, ctx, role):
        ctx.user.role = role
        c = ctx.client
        for r in (c.get(f"{_BASE}/catalog"), c.get(f"{_BASE}/channels"),
                  c.post(f"{_BASE}/channels", json=_tg()), c.get(f"{_BASE}/rules"),
                  c.post(f"{_BASE}/rules", json={}), c.get(f"{_BASE}/log"),
                  c.post(f"{_BASE}/channels/00000000-0000-0000-0000-000000000000/test")):
            assert r.status_code == 403
        assert _sql("SELECT count(*) FROM notification_channels WHERE name LIKE :p",
                    {"p": f"{_P}%"})[0][0] == 0

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPER_ADMIN])
    def test_admins_are_allowed(self, ctx, role):
        ctx.user.role = role
        assert ctx.client.get(f"{_BASE}/channels").status_code == 200


def test_catalog_lists_the_event_types_in_order(ctx):
    data = ctx.client.get(f"{_BASE}/catalog").json()
    assert [e["key"] for e in data["event_types"]] == list(EVENT_TYPES)
    assert data["severities"] == ["info", "warning", "critical"]
    assert data["channel_types"] == ["telegram", "smtp"]


class TestChannels:
    def test_create_list_patch_delete(self, ctx):
        tg = _create(ctx, _tg())
        assert tg["secret_present"] is False and tg["enabled"] is True
        assert tg["config_json"] == {"chat_id_env": "TELEGRAM_CHAT_ID"}
        sm = _create(ctx, _smtp())
        assert sm["config_json"]["to"] == ["ops@example.com"]

        names = [c["name"] for c in ctx.client.get(f"{_BASE}/channels").json()]
        assert {f"{_P}A", f"{_P}M"} <= set(names)

        r = ctx.client.patch(f"{_BASE}/channels/{tg['id']}",
                             json={"name": f"{_P}A2", "enabled": False,
                                   "config_json": {"chat_id": "-100123"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["name"], body["enabled"], body["config_json"]) == (
            f"{_P}A2", False, {"chat_id": "-100123"})
        assert body["updated_at"] > tg["updated_at"]

        assert ctx.client.patch(f"{_BASE}/channels/{tg['id']}",
                                json={"type": "smtp"}).status_code == 422
        assert ctx.client.delete(f"{_BASE}/channels/{tg['id']}").status_code == 204
        assert ctx.client.patch(f"{_BASE}/channels/{tg['id']}",
                                json={"enabled": True}).status_code == 404
        assert ctx.client.delete(f"{_BASE}/channels/{tg['id']}").status_code == 404
        assert ctx.client.post(f"{_BASE}/channels/{tg['id']}/test").status_code == 404

    def test_duplicate_name_is_409(self, ctx):
        _create(ctx, _tg())
        assert ctx.client.post(f"{_BASE}/channels", json=_tg()).status_code == 409

    @pytest.mark.parametrize("body", [
        _tg(secret_env_key="SECRET_KEY"),
        _tg(secret_env_key="ALPACA_API_SECRET"),
        _tg(secret_env_key="telegram_bot_token"),
        _tg(secret_env_key="TELEGRAM_BOT_TOKEN;rm"),
        _tg(secret_env_key="TELEGRAM_BOT_TOKEN\n"),
        _tg(secret_env_key="SMTP_APP_PASSWORD"),
        _smtp(secret_env_key="TELEGRAM_BOT_TOKEN"),
        _tg(config_json={"chat_id_env": "TELEGRAM_CHAT_ID", "url": "http://x"}),
        _tg(config_json={"chat_id": "12ab"}),
        _tg(config_json={"chat_id": 12345}),
        _tg(config_json={"chat_id": "1", "chat_id_env": "TELEGRAM_CHAT_ID"}),
        _tg(config_json={}),
        _tg(config_json={"chat_id_env": "DATABASE_URL"}),
        _tg(type="slack"),
        _tg(extra="x"),
        _smtp(config_json={"host": "smtp.example.com;rm", "port": 587,
                           "username_env": "SMTP_USER"}),
        _smtp(config_json={"host": "smtp.example.com", "port": 0,
                           "username_env": "SMTP_USER"}),
        _smtp(config_json={"host": "smtp.example.com", "port": 587,
                           "username_env": "SECRET_KEY"}),
        _smtp(config_json={"host": "smtp.example.com", "port": 587,
                           "username_env": "SMTP_USER", "to": ["not-an-email"]}),
        _smtp(config_json={"host": "smtp.example.com", "port": 587,
                           "username_env": "SMTP_USER",
                           "to": [f"a{i}@example.com" for i in range(11)]}),
        _smtp(config_json={"host": "smtp.example.com", "port": 587,
                           "username_env": "SMTP_USER", "password": "x"}),
    ])
    def test_invalid_channels_are_422(self, ctx, body):
        r = ctx.client.post(f"{_BASE}/channels", json=body)
        assert r.status_code == 422, r.text
        assert isinstance(r.json()["detail"], list)

    def test_patch_revalidates_against_the_existing_type(self, ctx):
        tg = _create(ctx, _tg())
        for patch in ({"secret_env_key": "SMTP_APP_PASSWORD"},
                      {"config_json": {"host": "smtp.example.com", "port": 25,
                                       "username_env": "SMTP_USER"}}):
            assert ctx.client.patch(f"{_BASE}/channels/{tg['id']}",
                                    json=patch).status_code == 422

    def test_secret_value_never_leaves_the_api(self, ctx, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "sentinel-p4-xyz")
        r = ctx.client.get(f"{_BASE}/channels")
        assert r.status_code == 200
        assert "sentinel-p4-xyz" not in r.text
        default = next(c for c in r.json() if c["name"] == "telegram-default")
        assert default["secret_present"] is True
        assert default["secret_env_key"] == "TELEGRAM_BOT_TOKEN"

    def test_test_endpoint_reports_and_logs_without_sending(self, ctx):
        tg = _create(ctx, _tg())
        r = ctx.client.post(f"{_BASE}/channels/{tg['id']}/test")
        assert r.status_code == 200
        assert r.json() == {"status": "failed", "attempts": 0,
                            "error": "TELEGRAM_BOT_TOKEN_ZZ not set"}
        log = ctx.client.get(f"{_BASE}/log", params={"limit": 500}).json()
        mine = [x for x in log if x["channel_name"] == f"{_P}A"]
        assert len(mine) == 1
        assert (mine[0]["event_type"], mine[0]["severity"], mine[0]["status"]) == (
            "test", "info", "failed")
        assert mine[0]["message_preview"].startswith("✅ CAT notification test — ZZTESTA")


class TestRules:
    def test_create_duplicate_patch_cascade(self, ctx):
        ch = _create(ctx, _tg())
        body = {"event_type": "stop_hit", "channel_id": ch["id"], "min_severity": "warning"}
        r = ctx.client.post(f"{_BASE}/rules", json=body)
        assert r.status_code == 201, r.text
        rule = r.json()
        assert (rule["channel_name"], rule["quiet_hours"], rule["enabled"]) == (
            f"{_P}A", None, True)
        assert ctx.client.post(f"{_BASE}/rules", json=body).status_code == 409

        qh = {"start": "23:00", "end": "07:00", "tz": "Asia/Kuala_Lumpur"}
        r = ctx.client.patch(f"{_BASE}/rules/{rule['id']}",
                             json={"quiet_hours": qh, "min_severity": "critical"})
        assert r.status_code == 200, r.text
        assert (r.json()["quiet_hours"], r.json()["min_severity"]) == (qh, "critical")
        r = ctx.client.patch(f"{_BASE}/rules/{rule['id']}", json={"enabled": False})
        assert r.json()["quiet_hours"] == qh and r.json()["enabled"] is False
        r = ctx.client.patch(f"{_BASE}/rules/{rule['id']}", json={"quiet_hours": None})
        assert r.json()["quiet_hours"] is None

        listed = [x for x in ctx.client.get(f"{_BASE}/rules").json() if x["id"] == rule["id"]]
        assert listed[0]["channel_name"] == f"{_P}A"

        assert ctx.client.delete(f"{_BASE}/channels/{ch['id']}").status_code == 204
        assert ctx.client.patch(f"{_BASE}/rules/{rule['id']}",
                                json={"enabled": True}).status_code == 404

    def test_rule_on_unknown_channel_is_404(self, ctx):
        r = ctx.client.post(f"{_BASE}/rules", json={
            "event_type": "stop_hit", "channel_id": "00000000-0000-0000-0000-000000000000"})
        assert r.status_code == 404

    @pytest.mark.parametrize("over", [
        {"event_type": "not_an_event"},
        {"min_severity": "loud"},
        {"quiet_hours": {"start": "23:00", "end": "07:00", "tz": "Mars/Base"}},
        {"quiet_hours": {"start": "07:00", "end": "07:00", "tz": "UTC"}},
        {"quiet_hours": {"start": "7:00", "end": "08:00", "tz": "UTC"}},
        {"quiet_hours": {"start": "25:00", "end": "08:00", "tz": "UTC"}},
        {"quiet_hours": {"start": "01:00", "end": "08:00"}},
        {"quiet_hours": {"start": "01:00", "end": "08:00", "tz": "UTC", "x": 1}},
        {"extra": True},
    ])
    def test_invalid_rules_are_422(self, ctx, over):
        ch = _create(ctx, _tg())
        body = {"event_type": "stop_hit", "channel_id": ch["id"], **over}
        assert ctx.client.post(f"{_BASE}/rules", json=body).status_code == 422


class TestLog:
    @pytest.mark.parametrize("limit", [0, 501])
    def test_limit_bounds(self, ctx, limit):
        assert ctx.client.get(f"{_BASE}/log", params={"limit": limit}).status_code == 422

    def test_newest_first(self, ctx):
        _sql("INSERT INTO notification_log (id, sent_at, channel_name, event_type, severity, "
             "status) VALUES (gen_random_uuid(), now() - interval '1 minute', :a, "
             "'stop_hit', 'warning', 'sent'), (gen_random_uuid(), now(), :b, 'stop_hit', "
             "'warning', 'failed')", {"a": f"{_P}old", "b": f"{_P}new"})
        log = ctx.client.get(f"{_BASE}/log", params={"limit": 500}).json()
        mine = [x["channel_name"] for x in log if x["channel_name"].startswith(_P)]
        assert mine == [f"{_P}new", f"{_P}old"]


def test_llm_and_nightwatch_split_api_from_subscription(ctx):
    group_id = _sql("SELECT id FROM strategy_groups ORDER BY created_at, id LIMIT 1")[0][0]
    et_today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()

    def snapshot():
        llm = ctx.client.get("/api/v1/llm/log", params={"limit": 2000}).json()
        nw = ctx.client.get("/api/v1/nightwatch/log", params={"group_id": str(group_id)}).json()
        night = next((n for n in nw["nights"] if n["date"] == et_today),
                     {"llm": {"calls": 0, "cost_usd": 0.0, "subscription_est_usd": 0.0}})
        return llm, night["llm"]

    llm0, nw0 = snapshot()
    _sql("INSERT INTO llm_calls (id, context, platform, model, cost_usd, billing) VALUES "
         "(gen_random_uuid(), :c, 'anthropic', 'zztest-model', 1.5, 'api'), "
         "(gen_random_uuid(), :c, 'claude-cli', 'zztest-model', 0.25, 'subscription')",
         {"c": f"{_P}billing"})
    llm1, nw1 = snapshot()

    def delta(key, part="all_time"):
        return round(llm1["summary"][part][key] - llm0["summary"][part][key], 8)

    assert delta("cost_usd") == 1.75
    assert delta("cost_api_usd") == 1.5
    assert delta("cost_subscription_est_usd") == 0.25
    assert delta("calls") == 2
    by_model = {(m["model"], m["billing"]): m for m in llm1["summary"]["by_model"]
                if m["model"] == "zztest-model"}
    assert set(by_model) == {("zztest-model", "api"), ("zztest-model", "subscription")}
    assert by_model[("zztest-model", "subscription")]["cost_usd"] == 0.25
    mine = sorted(i["billing"] for i in llm1["items"] if i["context"] == f"{_P}billing")
    assert mine == ["api", "subscription"]

    assert round(nw1["cost_usd"] - nw0["cost_usd"], 8) == 1.5
    assert round(nw1["subscription_est_usd"] - nw0["subscription_est_usd"], 8) == 0.25
    assert nw1["calls"] - nw0["calls"] == 2
