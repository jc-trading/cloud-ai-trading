"""P3a strategy-group lifecycle API against real PostgreSQL (rolled back):
create / clone / status / registry / runs, their validation (422), conflicts
(409) and the activation and archive gates. The app runs in the test's own
event loop through httpx's ASGI transport, so every request shares the one
rolled-back session and no row is ever committed."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select

import app.models_registry  # noqa: F401
from quant.engine import trade_rules
from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, SafetyState, SimAccount, SimPosition
from app.modules.strategy_groups.models import StrategyGroup, StrategyGroupRun
from app.modules.strategy_groups.router import router
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/strategy-groups"


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
        yield SimpleNamespace(client=client, db=db, user=user, monkeypatch=monkeypatch,
                              tmp_path=tmp_path)


def _body(**over):
    return {"name": f"zz-p3a-{uuid4().hex[:8]}", "strategy_key": V1_COMPOSITE_KEY, **over}


async def _create(api, **over):
    r = await api.client.post(URL, json=_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


async def _group(api, gid) -> StrategyGroup:
    return (await api.db.execute(
        select(StrategyGroup).where(StrategyGroup.id == gid))).scalar_one_or_none()


async def _patch(api, gid, status):
    return await api.client.patch(f"{URL}/{gid}/status", json={"status": status})


# --- create ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_books_a_system_account_and_full_params(api):
    out = await _create(api, params_json={"strategy": {"ma_fast": 7}}, starting_capital=5000)
    group = await _group(api, out["id"])
    acct = await api.db.get(SimAccount, group.sim_account_id)
    assert acct.is_system is True
    assert acct.entry_mode == "open_once"
    assert float(acct.cash) == float(acct.starting_capital) == 5000.0
    assert acct.name == f"grp:{group.id}"
    assert acct.user_id == api.user.id
    expected = get_spec(V1_COMPOSITE_KEY).default_params()
    expected["strategy"]["ma_fast"] = 7
    assert group.params_json == expected
    assert group.status == "paused" and out["status"] == "paused"
    assert group.trade_settings_json == {} and group.risk_json == {}


@pytest.mark.asyncio
async def test_create_response_reads_server_defaults(api):
    out = await _create(api)
    assert out["account"]["entry_mode"] == "open_once"
    assert out["created_at"] is not None
    assert out["account"]["open_positions"] == 0
    assert out["params_schema"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("over, fragment", [
    ({"strategy_key": "cat.v9.nope"}, "unknown strategy_key"),
    ({"params_json": {"strategy": {"ma_fast": True}}}, "expected int"),
    ({"params_json": {"strategy": {"ma_fast": 1}}}, "params out of bounds"),
    ({"params_json": {"strategy": {"ma_fast": 30, "ma_slow": 20}}}, "must be <"),
    ({"screener_json": {"rules": "x"}}, "screener_json"),
    ({"trade_settings_json": {"entry_prob_min": 0.6}}, "trade_settings_json"),
    ({"name": "   "}, "name"),
    ({"starting_capital": 100}, "starting_capital"),
])
async def test_create_rejects_invalid_input_with_422(api, over, fragment):
    r = await api.client.post(URL, json=_body(**over))
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_refuses_archived_status_with_422(api):
    r = await api.client.post(URL, json=_body(status="archived"))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_rejected_input_leaves_no_rows(api):
    before = (await api.db.execute(select(func.count()).select_from(SimAccount))).scalar()
    r = await api.client.post(URL, json=_body(params_json={"strategy": {"ma_fast": 1}}))
    assert r.status_code == 422
    after = (await api.db.execute(select(func.count()).select_from(SimAccount))).scalar()
    assert after == before


@pytest.mark.asyncio
async def test_duplicate_name_is_409(api):
    out = await _create(api)
    r = await api.client.post(URL, json=_body(name=out["name"]))
    assert r.status_code == 409
    assert r.json()["detail"] == "name already exists"


async def _seed_platform_state(api, **fields):
    acct_id = (await api.db.execute(select(StrategyGroup.sim_account_id).where(
        StrategyGroup.status == "active").limit(1))).scalar_one()
    state = (await api.db.execute(select(SafetyState).where(
        SafetyState.scope == str(acct_id)))).scalar_one_or_none()
    if state is None:
        state = SafetyState(scope=str(acct_id), halted=False)
        api.db.add(state)
    for k, v in fields.items():
        setattr(state, k, v)
    await api.db.commit()


@pytest.mark.asyncio
async def test_telegram_pause_blocks_an_active_create_but_not_a_paused_one(api):
    await _seed_platform_state(api, reason="manual /pause",
                               paused_until=date.today() + timedelta(days=30))
    r = await api.client.post(URL, json=_body(status="active"))
    assert r.status_code == 409
    assert "/pause" in r.json()["detail"]
    await _create(api, status="paused")


@pytest.mark.asyncio
async def test_telegram_kill_blocks_an_active_create(api):
    await _seed_platform_state(api, reason="manual /kill", halted=True)
    r = await api.client.post(URL, json=_body(status="active"))
    assert r.status_code == 409
    assert "HALT" in r.json()["detail"]


@pytest.mark.asyncio
async def test_halt_sentinel_blocks_an_active_create(api):
    sentinel = api.tmp_path / "HALT"
    sentinel.write_text("killed")
    r = await api.client.post(URL, json=_body(status="active"))
    assert r.status_code == 409
    assert "HALT sentinel" in r.json()["detail"]


async def _signal_cycle_running(api):
    row = (await api.db.execute(select(HeartbeatRecord).where(
        HeartbeatRecord.name == "signal_cycle_started"))).scalar_one_or_none()
    if row is None:
        row = HeartbeatRecord(name="signal_cycle_started")
        api.db.add(row)
    row.meta = {"session": "2026-10-19", "seq": 0, "done": False}
    row.last_beat_at = datetime.now(timezone.utc)
    await api.db.commit()


@pytest.mark.asyncio
async def test_activation_waits_for_a_running_signal_cycle(api):
    paused = await _create(api)
    await _signal_cycle_running(api)
    r = await api.client.post(URL, json=_body(status="active"))
    assert r.status_code == 409
    assert "signal_cycle is running" in r.json()["detail"]
    r = await _patch(api, paused["id"], "active")
    assert r.status_code == 409
    assert "signal_cycle is running" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_active_when_nothing_blocks(api):
    out = await _create(api, status="active")
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_non_admin_cannot_write(api):
    api.user.role = UserRole.BASIC
    assert (await api.client.post(URL, json=_body())).status_code == 403
    gid = (await api.db.execute(select(StrategyGroup.id).limit(1))).scalar_one()
    assert (await _patch(api, gid, "paused")).status_code == 403
    assert (await api.client.post(f"{URL}/{gid}/clone",
                                  json={"name": "zz-x"})).status_code == 403
    assert (await api.client.get(f"{URL}/registry")).status_code == 200


# --- status machine -------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_status_is_a_no_op(api):
    out = await _create(api)
    r = await _patch(api, out["id"], "paused")
    assert r.status_code == 200 and r.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_active_to_paused_and_back(api):
    out = await _create(api, status="active")
    r = await _patch(api, out["id"], "paused")
    assert r.status_code == 200 and r.json()["status"] == "paused"
    r = await _patch(api, out["id"], "active")
    assert r.status_code == 200 and r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_active_cannot_be_archived_directly(api):
    out = await _create(api, status="active")
    r = await _patch(api, out["id"], "archived")
    assert r.status_code == 409
    assert r.json()["detail"] == "pause the group first"


@pytest.mark.asyncio
async def test_paused_flat_group_archives_outside_rth(api):
    out = await _create(api)
    r = await _patch(api, out["id"], "archived")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "archived"
    assert r.json()["archived_at"] is not None


@pytest.mark.asyncio
async def test_archive_is_refused_in_rth_and_its_tail(api):
    out = await _create(api)
    api.monkeypatch.setattr(cycles, "in_rth", lambda *a, **kw: True)
    r = await _patch(api, out["id"], "archived")
    assert r.status_code == 409
    assert r.json()["detail"] == "archive outside market hours (RTH + 10 min)"


@pytest.mark.asyncio
async def test_archive_checks_the_ten_minutes_after_the_close(api):
    out = await _create(api)
    now = cycles.now_et()
    api.monkeypatch.setattr(cycles, "in_rth", lambda n=None, **kw: n is not None and n < now)
    r = await _patch(api, out["id"], "archived")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archived_is_terminal(api):
    out = await _create(api)
    assert (await _patch(api, out["id"], "archived")).status_code == 200
    for target in ("paused", "active"):
        r = await _patch(api, out["id"], target)
        assert r.status_code == 409
        assert r.json()["detail"] == "archived is terminal"


async def _open_position(api, account_id):
    api.db.add(SimPosition(account_id=account_id, symbol="ZZT1", shares=1, avg_cost=10,
                           stop=9, r_unit=1, high_water=10, entry_date=date(2026, 10, 16)))
    await api.db.commit()


@pytest.mark.asyncio
async def test_archive_requires_a_flat_book(api):
    out = await _create(api)
    await _open_position(api, out["account"]["id"])
    r = await _patch(api, out["id"], "archived")
    assert r.status_code == 409
    assert r.json()["detail"] == "archive requires a flat book: 1 open position(s)"


# --- clone -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clone_changes_only_the_overridden_fields(api):
    src = await _create(api, params_json={"funnel": {"min_confidence": 70}},
                        screener_json={"rules": [{"field": "price", "op": ">=", "value": 5}]},
                        starting_capital=3000)
    r = await api.client.post(f"{URL}/{src['id']}/clone", json={
        "name": "zz-p3a-clone", "params_json": {"exits": {"reversal_bars": 4}}})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["id"] != src["id"] and out["status"] == "paused"
    expected = {s: dict(v) for s, v in src["params_json"].items()}
    expected["exits"]["reversal_bars"] = 4
    assert out["params_json"] == expected
    assert out["screener_json"] == src["screener_json"]
    assert out["account"]["starting_capital"] == 3000.0
    assert out["account"]["id"] != src["account"]["id"]


@pytest.mark.asyncio
async def test_clone_rejects_an_unknown_section(api):
    src = await _create(api)
    r = await api.client.post(f"{URL}/{src['id']}/clone", json={
        "name": "zz-p3a-clone", "params_json": {"risk": {}}})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_clone_of_a_missing_group_is_404(api):
    r = await api.client.post(f"{URL}/{uuid4()}/clone", json={"name": "zz-p3a-clone"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_clone_archiving_a_source_with_positions_is_rolled_back(api):
    src = await _create(api)
    await _open_position(api, src["account"]["id"])
    r = await api.client.post(f"{URL}/{src['id']}/clone", json={
        "name": "zz-p3a-clone", "archive_source": True})
    assert r.status_code == 409
    assert "flat book" in r.json()["detail"]
    assert (await api.db.execute(select(StrategyGroup.id).where(
        StrategyGroup.name == "zz-p3a-clone"))).scalar_one_or_none() is None
    assert (await _group(api, src["id"])).status == "paused"


@pytest.mark.asyncio
async def test_clone_can_archive_a_flat_paused_source(api):
    src = await _create(api)
    r = await api.client.post(f"{URL}/{src['id']}/clone", json={
        "name": "zz-p3a-clone", "archive_source": True})
    assert r.status_code == 201, r.text
    assert (await _group(api, src["id"])).status == "archived"


# --- registry / runs -------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_is_not_swallowed_by_the_group_id_route(api):
    r = await api.client.get(f"{URL}/registry")
    assert r.status_code == 200
    body = r.json()
    strat = body["strategies"][0]
    assert strat["key"] == V1_COMPOSITE_KEY
    assert strat["params_schema"]["strategy"]["ma_fast"]["min"] == 2
    assert {"left": "strategy.ma_fast", "op": "<", "right": "strategy.ma_slow"} \
        in strat["cross_rules"]
    assert body["screener"]["asset_types"] == ["stock", "adr", "etf"]
    assert body["trade_settings"] == trade_rules.registry_entry() and body["trade_settings"]["available"] is True


@pytest.mark.asyncio
async def test_runs_lists_newest_first_and_filters_by_cycle(api):
    out = await _create(api)
    now = datetime.now(timezone.utc)
    for cycle, minutes in (("signal", 3), ("entry", 2), ("signal", 1)):
        api.db.add(StrategyGroupRun(group_id=out["id"], cycle=cycle, session_date=date.today(),
                                    outcome="ok", meta={"m": minutes},
                                    created_at=now - timedelta(minutes=minutes)))
    api.db.add(StrategyGroupRun(group_id=out["id"], cycle="signal", session_date=date.today(),
                                outcome="ok", created_at=now - timedelta(days=40)))
    await api.db.commit()
    rows = (await api.client.get(f"{URL}/{out['id']}/runs")).json()
    assert [r["meta"]["m"] for r in rows] == [1, 2, 3]
    rows = (await api.client.get(f"{URL}/{out['id']}/runs?cycle=entry")).json()
    assert [r["cycle"] for r in rows] == ["entry"]
    assert len((await api.client.get(f"{URL}/{out['id']}/runs?days=365")).json()) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["days=0", "days=366", "cycle=nightly"])
async def test_runs_rejects_bad_filters(api, query):
    out = await _create(api)
    assert (await api.client.get(f"{URL}/{out['id']}/runs?{query}")).status_code == 422


@pytest.mark.asyncio
async def test_runs_of_a_missing_group_is_404(api):
    assert (await api.client.get(f"{URL}/{uuid4()}/runs")).status_code == 404


# --- P3c trade settings ------------------------------------------------------------

CAL_OK = {"status": "ok", "blocks": [
    {"lo": 40.0, "hi": 60.0, "n": 30, "wins": 15, "p": 0.5},
    {"lo": 61.0, "hi": 90.0, "n": 30, "wins": 20, "p": 0.6667}]}


async def _cal_run(api, src_id, **over):
    from app.modules.backtests.models import BacktestRun
    from app.modules.backtests.service import config_core, config_hash

    src = await _group(api, src_id)
    fields = dict(group_id=src.id, kind="walk_forward", status="succeeded",
                  finished_at=datetime.now(timezone.utc), metrics_json={},
                  calibration_json=CAL_OK, config_hash=config_hash(config_core(src)),
                  config_snapshot={}, data_start=date(2020, 1, 1), data_end=date(2024, 1, 1),
                  universe_label="pit_sp500", survivorship_biased=False)
    fields.update(over)
    run = BacktestRun(id=uuid4(), **fields)
    api.db.add(run)
    await api.db.commit()
    return str(run.id)


def _prob(rid, p=0.55):
    return {"entry_prob_min": p, "calibration_run_id": rid}


async def _clone(api, src_id, **body):
    return await api.client.post(f"{URL}/{src_id}/clone",
                                 json={"name": f"zz-p3c-{uuid4().hex[:8]}", **body})


@pytest.mark.asyncio
async def test_create_stores_trade_settings_verbatim(api):
    ts = {"take_profit_pct": 0.05, "max_hold_days": 10, "holding_style": "long"}
    out = await _create(api, trade_settings_json=ts)
    assert (await _group(api, out["id"])).trade_settings_json == ts
    assert out["trade_settings_json"] == ts


@pytest.mark.asyncio
async def test_create_stores_default_swing_as_empty(api):
    out = await _create(api, trade_settings_json={"holding_style": "swing"})
    assert (await _group(api, out["id"])).trade_settings_json == {}


@pytest.mark.asyncio
async def test_create_rejects_scalping_with_the_exact_message(api):
    r = await api.client.post(URL, json=_body(trade_settings_json={"holding_style": "scalping"}))
    assert r.status_code == 422
    assert r.json()["detail"] == \
        "trade_settings_json.holding_style: scalping requires timeframe 1m (P5)"


@pytest.mark.asyncio
async def test_clone_without_trade_settings_inherits_the_source(api):
    src = await _create(api, trade_settings_json={"take_profit_pct": 0.05})
    r = await _clone(api, src["id"])
    assert r.status_code == 201, r.text
    assert (await _group(api, r.json()["id"])).trade_settings_json == {"take_profit_pct": 0.05}


@pytest.mark.asyncio
async def test_clone_with_a_usable_calibration_run(api):
    src = await _create(api)
    rid = await _cal_run(api, src["id"])
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid))
    assert r.status_code == 201, r.text
    assert (await _group(api, r.json()["id"])).trade_settings_json == _prob(rid)


@pytest.mark.asyncio
async def test_calibration_run_not_found_is_422(api):
    src = await _create(api)
    rid = str(uuid4())
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid))
    assert r.status_code == 422
    assert r.json()["detail"] == f"trade_settings_json.calibration_run_id: run {rid} not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("over, problem", [
    ({"status": "failed", "metrics_json": None}, "status failed"),
    ({"kind": "single"}, "kind single"),
    ({"calibration_json": {"status": "insufficient", "blocks": []}}, "calibration insufficient"),
])
async def test_unusable_calibration_run_is_409(api, over, problem):
    src = await _create(api)
    rid = await _cal_run(api, src["id"], **over)
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid))
    assert r.status_code == 409
    assert r.json()["detail"] == f"calibration run {rid} unusable: {problem}"


@pytest.mark.asyncio
async def test_a_filtered_calibration_run_is_409(api):
    src = await _create(api)
    rid = await _cal_run(api, src["id"], config_snapshot={"calibration_ref": {"run_id": "x"}})
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid))
    assert r.status_code == 409
    assert r.json()["detail"] == f"calibration run {rid} was itself filtered by entry_prob_min"


@pytest.mark.asyncio
async def test_a_different_config_is_409(api):
    src = await _create(api)
    rid = await _cal_run(api, src["id"])
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid),
                     params_json={"exits": {"reversal_bars": 4}})
    assert r.status_code == 409
    assert r.json()["detail"] == "calibration run was produced by a different config"


@pytest.mark.asyncio
async def test_a_probability_above_every_bucket_is_422(api):
    src = await _create(api)
    rid = await _cal_run(api, src["id"])
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid, p=0.9))
    assert r.status_code == 422
    assert r.json()["detail"] == \
        "trade_settings_json.entry_prob_min: no calibrated bucket reaches p=0.9 (max 0.6667)"


@pytest.mark.asyncio
async def test_load_groups_refuses_a_group_whose_calibration_turns_unusable(api):
    from sqlalchemy import text

    from app.modules.strategy_groups.models import MANAGED_STATUSES
    from app.modules.strategy_groups.service import load_groups

    src = await _create(api)
    rid = await _cal_run(api, src["id"])
    r = await _clone(api, src["id"], trade_settings_json=_prob(rid))
    assert r.status_code == 201, r.text
    name = r.json()["name"]
    runtimes, refused = await load_groups(api.db, MANAGED_STATUSES)
    mine = [rt for rt in runtimes if rt.group.name == name]
    assert len(mine) == 1 and mine[0].score_floor == 61.0
    await api.db.execute(text(
        "UPDATE backtest_runs SET calibration_json = "
        "jsonb_set(calibration_json, '{status}', '\"insufficient\"') WHERE id = :id"),
        {"id": rid})
    await api.db.commit()
    runtimes, refused = await load_groups(api.db, MANAGED_STATUSES)
    assert name not in [rt.group.name for rt in runtimes]
    assert any(x.startswith(f"{name}: ") and "calibration insufficient" in x for x in refused)
