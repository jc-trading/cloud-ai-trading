"""P5b ``GET /intraday/watch`` — shape, the subscribed / rejected flags read from
the market_stream heartbeat, the session parameter, the admin gate and the 503
when the registry is unreachable. No DB: the session factory and the resolver
are fakes."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import app.models_registry  # noqa: F401
from quant.data import registry as qregistry
from quant.data.registry import WatchEntry

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import UserRole
from app.modules.intraday.router import router

URL = "/api/v1/intraday/watch"
WATCHED = date(2026, 10, 26)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, beat):
        self.beat = beat

    async def execute(self, _stmt):
        return _FakeResult(self.beat)


def _beat(meta, age_s=5):
    return SimpleNamespace(name="market_stream", meta=meta,
                           last_beat_at=datetime.now(timezone.utc) - timedelta(seconds=age_s))


@pytest_asyncio.fixture
async def api(monkeypatch):
    state = SimpleNamespace(beat=None, calls=[], error=None,
                            user=SimpleNamespace(id="u", role=UserRole.SUPER_ADMIN,
                                                 is_active=True),
                            kept=[WatchEntry("AAA", "held_1m", ("orb",), "open position"),
                                  WatchEntry("BBB", "shortlist_1m", ("orb",), "rank 1")],
                            dropped=[WatchEntry("CCC", "configured", (),
                                                "market_stream_symbols priority 10")])

    def fake_resolve(source, *, session=None, cap=None):
        state.calls.append({"source": source, "session": session, "cap": cap})
        if state.error is not None:
            raise state.error
        return state.kept, state.dropped

    monkeypatch.setattr(qregistry, "resolve_watch", fake_resolve)
    monkeypatch.setattr(qregistry, "watch_session", lambda now=None: WATCHED)

    async def _db():
        yield _FakeSession(state.beat)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: state.user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        yield SimpleNamespace(client=client, state=state)


@pytest.mark.asyncio
async def test_a1_shape_tiers_and_stream_flags(api):
    api.state.beat = _beat({"subscribed": ["AAA"], "rejected": ["BBB"], "cap_effective": 29,
                            "connected": True, "period_key": "2026-10-26",
                            "stream_errors": [{"code": 405, "msg": "x", "at": "t"}]})
    r = await api.client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["session"], body["cap"], body["cap_effective"]) == ("2026-10-26", 29, 29)
    assert body["entries"] == [
        {"symbol": "AAA", "tier": "held_1m", "groups": ["orb"], "reason": "open position",
         "subscribed": True, "rejected": False},
        {"symbol": "BBB", "tier": "shortlist_1m", "groups": ["orb"], "reason": "rank 1",
         "subscribed": False, "rejected": True}]
    assert body["dropped"] == [{"symbol": "CCC", "tier": "configured", "groups": [],
                                "reason": "market_stream_symbols priority 10"}]
    stream = body["stream"]
    assert (stream["connected"], stream["period_key"]) == (True, "2026-10-26")
    assert stream["errors"] == [{"code": 405, "msg": "x", "at": "t"}]
    assert 4 <= stream["age_s"] < 60
    assert api.state.calls[0]["source"] is qregistry
    assert (api.state.calls[0]["session"], api.state.calls[0]["cap"]) == (WATCHED, 29)


@pytest.mark.asyncio
async def test_a2_malformed_session_is_422(api):
    r = await api.client.get(URL, params={"session": "2026-13-01"})
    assert r.status_code == 422
    assert api.state.calls == []


@pytest.mark.asyncio
async def test_a3_session_is_passed_through(api):
    r = await api.client.get(URL, params={"session": "2026-10-30"})
    assert r.status_code == 200, r.text
    assert r.json()["session"] == "2026-10-30"
    assert api.state.calls[0]["session"] == date(2026, 10, 30)


@pytest.mark.asyncio
async def test_a4_no_heartbeat(api):
    r = await api.client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["cap"], body["cap_effective"]) == (30, 30)
    assert [e["subscribed"] for e in body["entries"]] == [None, None]
    assert [e["rejected"] for e in body["entries"]] == [False, False]
    assert body["stream"] == {"age_s": None, "connected": None, "period_key": None,
                              "errors": []}
    assert api.state.calls[0]["cap"] == 30


@pytest.mark.asyncio
async def test_a5_unreachable_registry_is_503(api):
    api.state.error = RuntimeError("cannot reach PostgreSQL for the bar file registry: x")
    r = await api.client.get(URL)
    assert r.status_code == 503
    assert r.json()["detail"] == "cannot reach PostgreSQL for the bar file registry: x"


@pytest.mark.asyncio
async def test_a6_non_admin_is_403(api):
    api.state.user.role = UserRole.BASIC
    r = await api.client.get(URL)
    assert r.status_code == 403
    assert api.state.calls == []
