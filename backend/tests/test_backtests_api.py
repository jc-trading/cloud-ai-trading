"""P3b backtest API against real PostgreSQL (rolled back): queueing and its
validation (422), the active-run cap and terminal cancels (409), compare
(live and backtest share one metrics shape), route order, and the live
series built from the ledger."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, update

import app.models_registry  # noqa: F401
from quant import config as qconfig
from quant.backtest import metrics as qmetrics
from quant.engine.strategies import V1_COMPOSITE_KEY

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.backtests import service
from app.modules.backtests.models import BacktestRun
from app.modules.backtests.router import router
from app.modules.simledger.models import AccountSnapshot, SimFill, SimOrder, SimPosition
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.service import create_group
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/backtests"
SCREENER = {"rules": [{"field": "adv_usd", "window": 20, "op": ">=", "value": 50_000_000}]}


@pytest_asyncio.fixture
async def api(pg_session_rollback, monkeypatch, tmp_path):
    db = pg_session_rollback
    admin_id = (await db.execute(select(User.id).where(
        User.role == UserRole.SUPER_ADMIN).limit(1))).scalar_one()
    user = SimpleNamespace(id=admin_id, role=UserRole.SUPER_ADMIN, is_active=True)
    monkeypatch.setattr(qconfig, "DATA_ROOT", tmp_path)

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
        yield SimpleNamespace(client=client, db=db, user=user, tmp_path=tmp_path)


async def _group(api, **over) -> SimpleNamespace:
    """A committed paused group (each commit only releases a savepoint), as
    plain ids: a request that fails rolls the session back and expires ORM
    objects, so tests keep no ORM instance across requests."""
    g = await create_group(api.db, user_id=api.user.id, name=f"zz-p3b-{uuid4().hex[:8]}",
                           strategy_key=V1_COMPOSITE_KEY, params_json={},
                           screener_json=over.get("screener_json", {}),
                           trade_settings_json={}, notes=None, starting_capital=2000,
                           status="paused")
    await api.db.commit()
    return SimpleNamespace(id=g.id, sim_account_id=g.sim_account_id)


async def _orm_group(api, gid) -> StrategyGroup:
    return (await api.db.execute(select(StrategyGroup).where(StrategyGroup.id == gid))
            ).scalar_one()


def _single(group, **over):
    return {"group_id": str(group.id), "kind": "single", "start": "2024-01-02",
            "end": "2024-12-31", **over}


async def _post(api, body):
    return await api.client.post(URL, json=body)


FIXED_EQ = pd.Series([2000.0, 2010.0, 1995.0, 2030.0],
                     index=[date(2024, 1, d) for d in (2, 3, 4, 5)])
SELECTED_EQ = pd.Series([2000.0, 1980.0, 1990.0, 1970.0], index=FIXED_EQ.index)


async def _succeeded_run(api, group, kind="single") -> BacktestRun:
    """A succeeded run with artifacts under DATA_ROOT; a walk-forward run's
    selected curve and metrics differ from its fixed ones."""
    run_id = uuid4()
    d = api.tmp_path / "backtests" / str(run_id)
    d.mkdir(parents=True)
    fixed = qmetrics.payload(FIXED_EQ, [])
    if kind == "single":
        FIXED_EQ.rename("equity").rename_axis("date").to_csv(d / "equity.csv")
        metrics = {"summary": fixed}
    else:
        FIXED_EQ.rename("equity").rename_axis("date").to_csv(d / "equity_fixed.csv")
        SELECTED_EQ.rename("equity").rename_axis("date").to_csv(d / "equity_selected.csv")
        metrics = {"fixed": fixed, "selected": qmetrics.payload(SELECTED_EQ, [])}
    run = BacktestRun(id=run_id, group_id=group.id, kind=kind, status="succeeded",
                      config_snapshot={}, config_hash="x" * 64, data_start=date(2024, 1, 2),
                      data_end=date(2024, 1, 5), metrics_json=metrics,
                      artifact_path=f"backtests/{run_id}", universe_label="pit_sp500",
                      survivorship_biased=False, finished_at=datetime.now(timezone.utc))
    api.db.add(run)
    await api.db.commit()
    return SimpleNamespace(id=run_id)


async def _seed_ledger(api, group):
    """3 snapshots and one closed position: 2 buys + 1 sell."""
    acct_id = group.sim_account_id
    for i, eq in enumerate((2000, 2050, 2100)):
        api.db.add(AccountSnapshot(account_id=acct_id, snapshot_date=date(2026, 9, 1 + i),
                                   equity=Decimal(eq), cash=Decimal(eq), open_positions=0))
    pos = SimPosition(account_id=acct_id, symbol="AAA", status="closed", shares=Decimal(15),
                      avg_cost=Decimal("10.40"), stop=Decimal(9), r_unit=Decimal("1.5"),
                      high_water=Decimal(13), entry_date=date(2026, 9, 1),
                      closed_at=datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc),
                      close_reason="trailing")
    api.db.add(pos)
    await api.db.flush()
    for side, qty, price, comm in (("buy", 10, "10.00", "0.10"), ("buy", 5, "11.20", "0.05"),
                                   ("sell", 15, "12.00", "0.15")):
        order = SimOrder(account_id=acct_id, position_id=pos.id, symbol="AAA", side=side,
                         qty=Decimal(qty), status="filled", reason="entry",
                         idempotency_key=f"p3b-test-{uuid4()}")
        api.db.add(order)
        await api.db.flush()
        api.db.add(SimFill(order_id=order.id, price=Decimal(price), raw_price=Decimal(price),
                           qty=Decimal(qty), commission=Decimal(comm)))
    await api.db.commit()


# --- create ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_queues_a_run_with_a_four_key_platform_snapshot(api):
    g = await _group(api)
    r = await _post(api, _single(g))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "queued" and out["kind"] == "single"
    snap = out["config_snapshot"]
    assert set(snap["platform"]) == {"per_trade_risk_pct", "daily_loss_pause_pct",
                                     "portfolio_drawdown_halt_pct", "min_confidence"}
    assert snap["starting_capital"] == 2000.0 and type(snap["starting_capital"]) is float
    assert snap["run"] == {"kind": "single", "is_years": 3, "oos_years": 1, "grid": None}
    assert len(snap["engine_fingerprint"]) == 64
    core = service.config_core(await _orm_group(api, g.id))
    assert out["config_hash"] == service.config_hash(core)
    assert out["universe_label"] == "pit_sp500" and out["survivorship_biased"] is False
    row = (await api.db.execute(select(BacktestRun).where(
        BacktestRun.id == out["id"]))).scalar_one()
    assert row.created_by == api.user.id and row.metrics_json is None


@pytest.mark.asyncio
async def test_walk_forward_snapshot_stores_the_validated_grid(api):
    g = await _group(api)
    r = await _post(api, _single(g, kind="walk_forward", start="2018-01-02", end="2024-12-31",
                                 is_years=3, oos_years=2,
                                 grid={"exits.reversal_bars": [5, 2]}))
    assert r.status_code == 201, r.text
    assert r.json()["config_snapshot"]["run"] == {
        "kind": "walk_forward", "is_years": 3, "oos_years": 2,
        "grid": {"exits.reversal_bars": [5, 2]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("over, detail", [
    ({"start": "2015-12-31"}, "start must be >= 2016-01-01"),
    ({"end": (date.today() + timedelta(days=5)).isoformat()}, "last closed session"),
    ({"grid": {"exits.reversal_bars": [2]}}, "grid applies to walk_forward runs only"),
    ({"start": "2024-01-02", "end": "2024-03-01"}, "needs >= 120 sessions"),
    ({"kind": "walk_forward", "start": "2023-01-02", "end": "2024-12-31"},
     "no walk-forward window"),
    ({"kind": "walk_forward", "start": "2018-01-02", "grid": {"strategy.ma_fast": [5]}},
     "unknown knob"),
    ({"kind": "nope"}, "kind must be one of"),
    ({"kind": "walk_forward", "start": "2018-01-02", "is_years": 6}, "is_years must be 1-5"),
])
async def test_create_rejects_invalid_requests(api, over, detail):
    g = await _group(api)
    r = await _post(api, _single(g, **over))
    assert r.status_code == 422, r.text
    assert detail in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_rejects_non_empty_trade_settings(api):
    g = await _group(api)
    await api.db.execute(update(StrategyGroup).where(StrategyGroup.id == g.id)
                         .values(trade_settings_json={"take_profit_r": 3}))
    await api.db.commit()
    r = await _post(api, _single(g))
    assert r.status_code == 422
    assert r.json()["detail"] == "trade settings backtests arrive in P3c"


@pytest.mark.asyncio
async def test_screener_group_start_must_clear_panel_lookback(api, monkeypatch):
    g = await _group(api, screener_json=SCREENER)
    monkeypatch.setattr(service.qscreen, "earliest_start", lambda spec: None)
    r = await _post(api, _single(g, start="2024-01-02"))
    assert r.status_code == 422 and r.json()["detail"] == "no panel data"
    monkeypatch.setattr(service.qscreen, "earliest_start", lambda spec: date(2016, 8, 22))
    r = await _post(api, _single(g, start="2016-08-19", end="2017-06-30"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == \
        "start must be >= 2016-08-22 (panel history + 20-session lookback)"
    ok = await _post(api, _single(g, start="2024-01-02"))
    assert ok.status_code == 201, ok.text
    assert ok.json()["universe_label"] == "screener_current_universe"
    assert ok.json()["survivorship_biased"] is True


@pytest.mark.asyncio
async def test_unknown_group_is_404(api):
    r = await _post(api, {"group_id": str(uuid4()), "kind": "single",
                          "start": "2024-01-02", "end": "2024-12-31"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_fourth_active_run_is_409(api):
    g = await _group(api)
    active = (await api.db.execute(select(BacktestRun).where(
        BacktestRun.status.in_(("queued", "running"))))).scalars().all()
    for _ in range(service.BACKTEST_MAX_ACTIVE - len(active)):
        assert (await _post(api, _single(g))).status_code == 201
    r = await _post(api, _single(g))
    assert r.status_code == 409
    assert "max 3" in r.json()["detail"]


# --- read / cancel ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_list_and_404(api):
    g = await _group(api)
    rid = (await _post(api, _single(g))).json()["id"]
    assert (await api.client.get(f"{URL}/{rid}")).json()["id"] == rid
    listed = (await api.client.get(URL, params={"group_id": str(g.id)})).json()
    assert [x["id"] for x in listed] == [rid]
    assert (await api.client.get(f"{URL}/{uuid4()}")).status_code == 404
    assert (await api.client.get(URL, params={"status": "nope"})).status_code == 422
    assert (await api.client.get(f"{URL}/{rid}/equity")).status_code == 409


@pytest.mark.asyncio
async def test_cancel_queued_running_and_terminal(api):
    g = await _group(api)
    queued = (await _post(api, _single(g))).json()["id"]
    r = await api.client.post(f"{URL}/{queued}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled" and r.json()["finished_at"] is not None
    again = await api.client.post(f"{URL}/{queued}/cancel")
    assert again.status_code == 409

    running = (await _post(api, _single(g))).json()["id"]
    await api.db.execute(update(BacktestRun).where(BacktestRun.id == running)
                         .values(status="running", started_at=datetime.now(timezone.utc)))
    await api.db.commit()
    r = await api.client.post(f"{URL}/{running}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "running" and r.json()["cancel_requested"] is True

    done = await _succeeded_run(api, g)
    assert (await api.client.post(f"{URL}/{done.id}/cancel")).status_code == 409


# --- compare --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compare_live_and_backtest_share_one_metrics_shape(api):
    g = await _group(api)
    await _seed_ledger(api, g)
    single = await _succeeded_run(api, g)
    wf = await _succeeded_run(api, g, kind="walk_forward")
    r = await api.client.get(f"{URL}/compare", params={
        "group_ids": str(g.id), "run_ids": f"{single.id},{wf.id}"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["start_applies_to"] == "live"
    live, bt, bt_wf = out["items"]
    assert live["kind"] == "live" and bt["kind"] == bt_wf["kind"] == "backtest"
    assert live["metrics"] is not None
    assert set(live["metrics"]) == set(bt["metrics"]) == set(bt_wf["metrics"])
    assert bt["equity"]["dates"][0] == "2024-01-02" and bt["equity"]["values"][-1] == 2030.0
    assert live["equity"]["dates"] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert bt_wf["source"] == "backtest:walk_forward:fixed"
    assert bt_wf["equity"]["values"] == list(FIXED_EQ.values)
    assert bt_wf["metrics"] == qmetrics.payload(FIXED_EQ, [])
    assert bt_wf["metrics"]["final_equity"] != qmetrics.payload(SELECTED_EQ, [])["final_equity"]
    eq = (await api.client.get(f"{URL}/{wf.id}/equity")).json()
    assert set(eq) == {"fixed", "selected"}
    assert eq["fixed"]["values"] == list(FIXED_EQ.values)
    assert eq["selected"]["values"] == list(SELECTED_EQ.values)
    assert eq["selected"]["dates"] == eq["fixed"]["dates"] == ["2024-01-02", "2024-01-03",
                                                               "2024-01-04", "2024-01-05"]


@pytest.mark.asyncio
async def test_compare_live_without_snapshots_has_a_note(api):
    g = await _group(api)
    r = await api.client.get(f"{URL}/compare", params={"group_ids": str(g.id)})
    item = r.json()["items"][0]
    assert item["metrics"] is None and "note" in item


@pytest.mark.asyncio
async def test_compare_rejects_bad_counts_unknown_and_unfinished(api):
    g = await _group(api)
    nine = ",".join(str(uuid4()) for _ in range(9))
    assert (await api.client.get(f"{URL}/compare", params={"run_ids": nine})).status_code == 422
    assert (await api.client.get(f"{URL}/compare")).status_code == 422
    assert (await api.client.get(f"{URL}/compare",
                                 params={"run_ids": str(uuid4())})).status_code == 404
    queued = (await _post(api, _single(g))).json()["id"]
    r = await api.client.get(f"{URL}/compare", params={"run_ids": queued})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_compare_route_is_not_swallowed_by_run_id(api):
    r = await api.client.get(f"{URL}/compare", params={"group_ids": str(uuid4())})
    assert r.status_code == 404
    assert r.json()["detail"] == "strategy group not found"


# --- live series ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_series_joins_fills_through_orders(api):
    g = await _group(api)
    await _seed_ledger(api, g)
    g = await _orm_group(api, g.id)
    equity, trades = await service.live_series(api.db, g)
    assert list(equity.values) == [2000.0, 2050.0, 2100.0]
    (t,) = trades
    assert t.shares == 15.0
    assert t.exit_price == 12.0 and t.entry_price == 10.4
    assert t.pnl == pytest.approx(15 * 12.00 - (10 * 10.00 + 5 * 11.20) - (0.10 + 0.05 + 0.15))
    assert t.exit_date == date(2026, 9, 3)          # 01:00 UTC = previous ET evening
    assert t.exit_reason == "trailing" and t.r_unit == 1.5
    eq2, trades2 = await service.live_series(api.db, g, start=date(2026, 9, 2))
    assert list(eq2.index) == [date(2026, 9, 2), date(2026, 9, 3)] and len(trades2) == 1
