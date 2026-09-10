"""POST /api/v1/auth/register is closed unless ALLOW_REGISTER is on.

No DB: the 403 must be raised before AuthService touches a session, so the
disabled case is asserted standalone. The enabled case only proves the gate
lets the request through — it is stubbed at AuthService.
"""

from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.database import get_db
from app.modules.auth import router as auth_router_module
from app.modules.auth.router import router as auth_router

_URL = "/api/v1/auth/register"
_BODY = {"name": "ZZTest", "email": "zztest@example.com", "password": "sup3rsecret!"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_register_forbidden_when_disabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTER", "false")
    resp = client.post(_URL, json=_BODY)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Registration is disabled"


def test_register_passes_the_gate_when_enabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTER", "true")

    called = {}

    async def _fake_register(db, data):
        called["email"] = data.email
        raise RuntimeError("reached AuthService")

    monkeypatch.setattr(
        auth_router_module.AuthService, "register", staticmethod(_fake_register)
    )
    with pytest.raises(RuntimeError, match="reached AuthService"):
        client.post(_URL, json=_BODY)
    assert called["email"] == _BODY["email"]
