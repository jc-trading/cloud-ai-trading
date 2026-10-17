"""P2 GET /strategy-groups/{group_id}/screen: 404 / 409 (legacy, invalid spec,
no complete panel) / 422 (not a session) / 200. Screener groups are seeded
archived inside the request's rolled-back session; the panel and the screen
itself are patched, so no market data is read."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from quant.data import panel as qpanel
from quant.data import screen as qscreen
from quant.engine import screener
from quant.engine.strategies import V1_COMPOSITE_KEY

from app.modules.simledger.models import SimAccount, SimPosition
from app.modules.simledger.service import _dec
from app.modules.strategy_groups.models import StrategyGroup
from tests.test_strategy_groups_api import ctx  # noqa: F401  (fixture)

SESSION = date(2026, 10, 16)
SPEC = {"rules": [{"field": "price", "op": ">=", "value": 10},
                  {"field": "adv_usd", "window": 20, "op": ">=", "value": 1e6}],
        "include": ["INC"], "exclude": ["EXCL"]}


def _seed_group(ctx, screener_json, held=()):
    tag = uuid4().hex[:8]
    acct_id, group_id = uuid4(), uuid4()

    async def seed(session):
        session.add(SimAccount(id=acct_id, user_id=ctx.g1.user_id, name=f"zzscreen-{tag}",
                               is_system=True, starting_capital=_dec(1000),
                               cash=_dec(1000)))
        await session.flush()
        session.add(StrategyGroup(id=group_id, name=f"zzscreen {tag}",
                                  strategy_key=V1_COMPOSITE_KEY, params_json={},
                                  screener_json=screener_json, status="archived",
                                  archived_at=datetime.now(timezone.utc),
                                  sim_account_id=acct_id))
        for sym in held:
            session.add(SimPosition(account_id=acct_id, symbol=sym, shares=_dec(1),
                                    avg_cost=_dec(10), stop=_dec(9), r_unit=_dec(1),
                                    high_water=_dec(10), entry_date=SESSION,
                                    status="open"))
        await session.flush()

    ctx.state["seed"] = seed
    return SimpleNamespace(id=group_id, name=f"zzscreen {tag}")


@pytest.fixture
def panel_on(monkeypatch):
    calls = []

    def fake_screen(spec, as_of, *, held=()):
        calls.append((as_of, tuple(held)))
        features = pd.DataFrame({"price": [20.0, 30.0, 5.0, 40.0],
                                 "adv_usd:20": [2e6, 5e5, 9e6, 3e6]},
                                index=["AAA", "BBB", "CCC", "EXCL"])
        return screener.apply(spec, features, held, as_of=as_of)

    monkeypatch.setattr(qpanel, "latest_complete", lambda: SESSION)
    monkeypatch.setattr(qpanel, "is_complete", lambda d: d == SESSION)
    monkeypatch.setattr(qscreen, "run_screen", fake_screen)
    return calls


def _get(ctx, gid, **params):
    return ctx.client.get(f"/api/v1/strategy-groups/{gid}/screen", params=params)


def test_unknown_group_is_404(ctx, panel_on):
    assert _get(ctx, uuid4()).status_code == 404


def test_legacy_group_is_409(ctx, panel_on):
    r = _get(ctx, ctx.g1.id)
    assert r.status_code == 409
    assert r.json()["detail"] == "group uses the legacy v1 pool (screener_json is empty)"


def test_invalid_spec_is_409(ctx, panel_on):
    g = _seed_group(ctx, {"rules": [{"field": "pe", "op": ">", "value": 1}]})
    r = _get(ctx, g.id)
    assert r.status_code == 409
    assert r.json()["detail"] == ("screener_json invalid: screener_json.rules[0].field: "
                                  "unknown field 'pe'")


def test_incomplete_panel_is_409(ctx, panel_on):
    g = _seed_group(ctx, SPEC)
    r = _get(ctx, g.id, date="2026-10-15")
    assert r.status_code == 409
    assert r.json()["detail"] == "no complete panel for 2026-10-15"
    assert panel_on == []


def test_non_session_is_422(ctx, panel_on):
    g = _seed_group(ctx, SPEC)
    r = _get(ctx, g.id, date="2026-10-17")                    # a Saturday
    assert r.status_code == 422
    assert r.json()["detail"] == "2026-10-17 is not a trading day"


def test_screen_defaults_to_the_latest_complete_session(ctx, panel_on):
    g = _seed_group(ctx, SPEC, held=("HELD", "EXCL"))
    r = _get(ctx, g.id)
    assert r.status_code == 200
    body = r.json()
    assert panel_on == [(SESSION, ("EXCL", "HELD"))]
    assert body["group_id"] == str(g.id) and body["group_name"] == g.name
    assert body["as_of"] == "2026-10-16" and body["evaluated"] == 4
    assert body["asset_types"] == ["stock", "adr", "etf"]
    assert [(x["field"], x["window"], x["passed"], x["passed_cumulative"])
            for x in body["rules"]] == [("price", None, 3, 3), ("adv_usd", 20, 3, 2)]
    cumulative = [x["passed_cumulative"] for x in body["rules"]]
    assert cumulative == sorted(cumulative, reverse=True)
    assert body["screened"] == 2                                # AAA, EXCL
    assert body["include_added"] == ["INC"]
    assert body["excluded_removed"] == ["EXCL"]
    assert body["held_added"] == ["HELD"]
    assert body["pool"] == ["AAA", "HELD", "INC"]
    assert body["pool_size"] == len(body["pool"])
    assert body["pool_cap"] == 1500 and body["over_cap"] is False
    assert body["include_ignored"] == []


def test_over_cap_pool_is_flagged(ctx, panel_on, monkeypatch):
    from quant import config as qconfig

    monkeypatch.setattr(qconfig, "SCREEN_POOL_MAX", 2)
    g = _seed_group(ctx, SPEC, held=("HELD",))
    body = _get(ctx, g.id).json()
    assert body["pool_size"] == 3 and body["pool_cap"] == 2 and body["over_cap"] is True
