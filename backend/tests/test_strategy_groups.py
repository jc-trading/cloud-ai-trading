"""P1 strategy groups: registry parity, migration literal, validate_group, the
group filter on every recommendation read/write, and the DB constraints
(against real PostgreSQL inside a rolled-back transaction)."""

import asyncio
import importlib.util
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, pool, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models_registry  # noqa: F401
from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec
from quant.engine.strategy import StrategyParams

from app.modules.llm import explain as explain_mod
from app.modules.simledger import cycles
from app.modules.simledger.models import Recommendation, SimAccount
from app.modules.simledger.service import _dec
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.service import GroupRefused, load_groups, validate_group
from tests._groups import group_for

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = "019_strategy_groups.py"


def _acct():
    return SimAccount(id=uuid4(), user_id=uuid4(), name="system", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(2000))


# --- registry == the constants the live chain ran on before groups ----------

def test_v1_registry_defaults_are_the_deployed_constants():
    defaults = get_spec(V1_COMPOSITE_KEY).defaults
    assert defaults.funnel == cycles.RECOMMENDED_FUNNEL
    assert defaults.exits == cycles.RECOMMENDED_EXITS
    assert defaults.strategy == StrategyParams()


def _migration_path() -> Path:
    for candidate in (BACKEND / "migrations" / "versions" / MIGRATION,
                      BACKEND.parent / "runtime" / "p1_replay" / MIGRATION,
                      BACKEND / "runtime" / "p1_replay" / MIGRATION):
        if candidate.exists():
            return candidate
    pytest.fail(f"{MIGRATION} not found in migrations/versions or runtime/p1_replay")


def test_migration_literal_matches_the_registry_as_a_string():
    """Compared as JSON TEXT on purpose: 70 vs 70.0 is equal as a number but a
    different params_json, and the oos badge compares the text."""
    spec_ = importlib.util.spec_from_file_location("m019", _migration_path())
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    assert json.dumps(module._V1_PARAMS, sort_keys=True) == json.dumps(
        get_spec(V1_COMPOSITE_KEY).default_params(), sort_keys=True)
    assert module._V1_KEY == V1_COMPOSITE_KEY


# --- validate_group ----------------------------------------------------------

def test_validate_group_accepts_group_one():
    cfg = validate_group(group_for(_acct()))
    assert cfg == get_spec(V1_COMPOSITE_KEY).defaults


@pytest.mark.parametrize("overrides, reason", [
    ({"market": "hk"}, "market"),
    ({"timeframe": "1m"}, "timeframe"),
    ({"screener_json": {"market_cap": 1e8}}, "screener_json invalid"),
    ({"trade_settings_json": {"entry": "limit"}}, "trade_settings_json"),
    ({"risk_json": {"halt_pct": 0.1}}, "risk_json"),
    ({"strategy_key": "cat.v2.nope"}, "unknown strategy_key"),
    ({"params_json": {"strategy": {"ma_fast": 5.5}}}, "expected int"),
])
def test_validate_group_refuses_what_p1_cannot_honour(overrides, reason):
    group = group_for(_acct(), name="g2", **overrides)
    with pytest.raises(GroupRefused) as e:
        validate_group(group)
    assert str(e.value).startswith("g2: ")
    assert reason in str(e.value)


JIACONG_SCREENER = {"rules": [
    {"field": "market_cap", "op": ">=", "value": 100000000},
    {"field": "adv_usd", "window": 20, "op": ">=", "value": 50000000},
    {"field": "ret_std", "window": 5, "op": ">=", "value": 0.035},
    {"field": "close_vs_ema", "window": 21, "op": ">", "value": 0},
]}


def test_a_valid_screener_is_accepted_and_carried_on_the_runtime():
    acct = _acct()
    group = group_for(acct, name="screened", screener_json=JIACONG_SCREENER)
    assert validate_group(group) == get_spec(V1_COMPOSITE_KEY).defaults

    class _RowsDb:
        async def execute(self, stmt):
            class _R:
                def all(self):
                    return [(group, acct)]
            return _R()

    runtimes, refused = asyncio.run(load_groups(_RowsDb(), ("active",)))
    assert refused == []
    assert runtimes[0].screener is not None
    assert [r.key for r in runtimes[0].screener.rules] == [
        "market_cap", "adv_usd:20", "ret_std:5", "close_vs_ema:21"]


def test_a_legacy_group_has_no_screener():
    acct = _acct()

    class _RowsDb:
        async def execute(self, stmt):
            class _R:
                def all(self):
                    return [(group_for(acct), acct)]
            return _R()

    runtimes, _ = asyncio.run(load_groups(_RowsDb(), ("active",)))
    assert runtimes[0].screener is None


# --- every recommendation read/write carries the group ----------------------

class _SqlDb:
    def __init__(self):
        self.executed = []
        self.added = []

    async def execute(self, stmt):
        self.executed.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))

        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return []
                return _S()

            def scalar_one_or_none(self):
                return None
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


GID = uuid4()


def _rec_row():
    return {"symbol": "AAA", "trade_date": date(2026, 10, 19), "direction": "up",
            "confidence": 70.0, "shortlist_rank": 1, "phase": "up",
            "phase_reason": "", "features": {}}


def test_store_recommendations_replaces_only_its_own_group():
    db = _SqlDb()
    assert asyncio.run(cycles.store_recommendations(db, [_rec_row()], group_id=GID)) == 1
    assert "recommendations.group_id" in db.executed[0]
    assert GID.hex in db.executed[0].replace("-", "")
    assert [r.group_id for r in db.added] == [GID]


def test_store_recommendations_empty_batch_deletes_nothing():
    db = _SqlDb()
    assert asyncio.run(cycles.store_recommendations(db, [], group_id=GID)) == 0
    assert db.executed == []


def test_shortlist_and_entries_read_only_their_group():
    db = _SqlDb()
    asyncio.run(cycles.shortlist_symbols(db, date(2026, 10, 19), group_id=GID))
    asyncio.run(cycles.run_entries(db, _acct(), date(2026, 10, 19), group_id=GID))
    assert len(db.executed) == 2
    assert all("recommendations.group_id" in sql for sql in db.executed)


def test_explanations_read_only_their_group():
    db = _SqlDb()
    asyncio.run(explain_mod.explain_recommendations(db, date(2026, 10, 19), group_id=GID))
    assert "recommendations.group_id" in db.executed[0]


# --- constraints, against real PostgreSQL (rolled back) ---------------------

def _no_pg(reason):
    if os.environ.get("CAT_NO_PG") == "1":
        pytest.skip(reason)
    pytest.fail(f"{reason} — run `make test-backend` (it exports the URL from "
                ".env), or set CAT_NO_PG=1 to acknowledge the gap")


@pytest.fixture()
def pg():
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        _no_pg("DATABASE_URL_SYNC not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        conn = engine.connect()
    except Exception as exc:
        engine.dispose()
        _no_pg(f"PostgreSQL unreachable: {exc}")
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        if trans.is_active:
            trans.rollback()          # the real tables are left untouched
        conn.close()
        engine.dispose()


def _system_account(pg) -> SimAccount:
    return pg.execute(select(SimAccount).where(SimAccount.is_system.is_(True))
                      .order_by(SimAccount.created_at).limit(1)).scalar_one()


def _archived_group(pg, account_id, name) -> StrategyGroup:
    """Inserted groups are ALWAYS archived: an active row in a real DB would be
    traded by the worker if the transaction ever leaked."""
    group = StrategyGroup(id=uuid4(), name=name, strategy_key=V1_COMPOSITE_KEY,
                          status="archived", archived_at=datetime.now(timezone.utc),
                          sim_account_id=account_id)
    pg.add(group)
    pg.flush()
    return group


def _second_system_account(pg, like: SimAccount) -> SimAccount:
    acct = SimAccount(id=uuid4(), user_id=like.user_id, name=f"test-{uuid4().hex[:8]}",
                      is_system=True)
    pg.add(acct)
    pg.flush()
    return acct


def _expect_violation(pg, constraint: str, fn):
    with pytest.raises(IntegrityError) as e:
        with pg.begin_nested():
            fn()
            pg.flush()
    assert constraint in str(e.value)


def test_group_cannot_point_at_a_practice_account(pg):
    practice = pg.execute(select(SimAccount).where(SimAccount.is_system.is_(False))
                          .limit(1)).scalar_one_or_none()
    if practice is None:
        practice = SimAccount(id=uuid4(), user_id=_system_account(pg).user_id,
                              name=f"practice-{uuid4().hex[:8]}", is_system=False)
        pg.add(practice)
        pg.flush()
    _expect_violation(pg, "fk_strategy_group_system_account",
                      lambda: _archived_group(pg, practice.id, "n9 practice"))


def test_group_account_cannot_be_flipped_to_practice(pg):
    acct = _second_system_account(pg, _system_account(pg))
    _archived_group(pg, acct.id, "n10 flip")
    _expect_violation(pg, "fk_strategy_group_system_account", lambda: pg.execute(
        text("UPDATE sim_accounts SET is_system = false WHERE id = :id"),
        {"id": acct.id}))


def test_group_account_cash_stays_writable(pg):
    acct = _second_system_account(pg, _system_account(pg))
    _archived_group(pg, acct.id, "n11 cash")
    pg.execute(text("UPDATE sim_accounts SET cash = cash WHERE id = :id"), {"id": acct.id})
    pg.flush()


def test_recommendation_without_group_is_rejected(pg):
    _expect_violation(pg, "group_id", lambda: pg.execute(text(
        "INSERT INTO recommendations (id, symbol, trade_date, direction, confidence, "
        "phase) VALUES (:id, 'ZZZT', '2030-01-02', 'up', 70, 'up')"), {"id": uuid4()}))


def test_recommendations_are_unique_per_group_not_globally(pg):
    acct = _second_system_account(pg, _system_account(pg))
    g1 = _archived_group(pg, acct.id, "n4 one")
    g2 = _archived_group(pg, _second_system_account(pg, acct).id, "n4 two")

    def rec(group):
        return Recommendation(id=uuid4(), group_id=group.id, symbol="ZZZT",
                              trade_date=date(2030, 1, 2), direction="up",
                              confidence=_dec(70), phase="up")

    pg.add_all([rec(g1), rec(g2)])
    pg.flush()                                  # same (symbol, date), two groups: OK
    _expect_violation(pg, "uq_recommendation_group_symbol_date",
                      lambda: pg.add(rec(g1)))


# --- P3c trade settings ----------------------------------------------------------

CAL_RID = "0b8a2f7e-5d1c-4c3e-9a77-2f1e6d4b9c01"
CAL_OK = {"status": "ok", "blocks": [
    {"lo": 40.0, "hi": 60.0, "n": 30, "wins": 15, "p": 0.5},
    {"lo": 61.0, "hi": 90.0, "n": 30, "wins": 20, "p": 0.6667}]}


class _TradeDb:
    def __init__(self, rows, runs=None):
        self.rows, self.runs, self.gets = rows, runs or {}, []

    async def execute(self, stmt):
        rows = self.rows

        class _R:
            def all(self):
                return rows
        return _R()

    async def get(self, model, key):
        self.gets.append((model.__name__, str(key)))
        return self.runs.get(str(key))


def _cal_run(**kw):
    from types import SimpleNamespace

    base = dict(status="succeeded", kind="walk_forward", calibration_json=CAL_OK)
    base.update(kw)
    return SimpleNamespace(**base)


def _load(group, acct, runs=None):
    db = _TradeDb([(group, acct)], runs)
    return asyncio.run(load_groups(db, ("active",))), db


def test_a_take_profit_group_passes_validate_group():
    group = group_for(_acct(), name="tp", trade_settings_json={"take_profit_pct": 0.05})
    assert validate_group(group) == get_spec(V1_COMPOSITE_KEY).defaults


def test_load_groups_carries_trade_settings_without_a_floor():
    acct = _acct()
    (runtimes, refused), db = _load(group_for(acct, name="tp", trade_settings_json={
        "take_profit_pct": 0.05}), acct)
    assert refused == [] and db.gets == []
    assert runtimes[0].trade.take_profit_pct == 0.05
    assert runtimes[0].score_floor is None


def test_default_swing_settings_load_as_none():
    acct = _acct()
    (runtimes, _), _ = _load(group_for(acct, trade_settings_json={"holding_style": "swing"}),
                             acct)
    assert runtimes[0].trade is None and runtimes[0].score_floor is None


def _prob_group(acct, p=0.55):
    return group_for(acct, name="prob", trade_settings_json={
        "entry_prob_min": p, "calibration_run_id": CAL_RID})


def test_missing_calibration_run_refuses_the_group():
    acct = _acct()
    (runtimes, refused), db = _load(_prob_group(acct), acct)
    assert runtimes == [] and len(refused) == 1
    assert "unusable: not found" in refused[0] and refused[0].startswith("prob: ")
    assert db.gets == [("BacktestRun", CAL_RID)]


def test_insufficient_calibration_refuses_the_group():
    acct = _acct()
    run = _cal_run(calibration_json={"status": "insufficient", "blocks": []})
    (runtimes, refused), _ = _load(_prob_group(acct), acct, {CAL_RID: run})
    assert runtimes == [] and "unusable: calibration insufficient" in refused[0]


def test_ok_calibration_sets_the_score_floor():
    from quant.backtest.calibration import score_threshold

    acct = _acct()
    (runtimes, refused), _ = _load(_prob_group(acct), acct, {CAL_RID: _cal_run()})
    assert refused == []
    assert runtimes[0].score_floor == score_threshold(CAL_OK, 0.55) == 61.0
    assert runtimes[0].trade.entry_prob_min == 0.55


def test_unreachable_probability_floors_at_infinity():
    import math

    acct = _acct()
    (runtimes, _), _ = _load(_prob_group(acct, p=0.9), acct, {CAL_RID: _cal_run()})
    assert runtimes[0].score_floor == math.inf


# --- P5a: 1m groups are validated by their spec and skipped by load_groups ---

ORB_KEY = "cat.m1.orb"


def _orb_group(acct, name="orb", **overrides):
    fields = dict(strategy_key=ORB_KEY, params_json=get_spec(ORB_KEY).default_params(),
                  timeframe="1m", screener_json=JIACONG_SCREENER)
    fields.update(overrides)
    return group_for(acct, name=name, **fields)


def test_orb_on_the_daily_timeframe_is_refused():
    with pytest.raises(GroupRefused) as e:
        validate_group(_orb_group(_acct(), timeframe="1d"))
    assert str(e.value) == "orb: timeframe '1d' is not supported"


def test_orb_on_1m_with_a_screener_validates():
    from quant.engine.intraday_strategies import ORBConfig

    cfg = validate_group(_orb_group(_acct()))
    assert isinstance(cfg, ORBConfig) and cfg == get_spec(ORB_KEY).defaults


def test_a_1m_group_needs_screener_rules():
    with pytest.raises(GroupRefused) as e:
        validate_group(_orb_group(_acct(), screener_json={}))
    assert str(e.value) == "orb: screener_json: 1m groups need screener rules"


def test_a_1m_group_takes_the_1m_trade_settings():
    with pytest.raises(GroupRefused) as e:
        validate_group(_orb_group(_acct(), trade_settings_json={"holding_style": "swing"}))
    assert str(e.value) == "orb: trade_settings_json.holding_style: 1m groups support scalping only"
    validate_group(_orb_group(_acct(), trade_settings_json={"stop_pct": 0.01}))


def test_load_groups_skips_valid_1m_groups_without_refusing_them():
    v1_acct, orb_acct = _acct(), _acct()
    db = _TradeDb([(group_for(v1_acct, name="v1"), v1_acct), (_orb_group(orb_acct), orb_acct)])
    runtimes, refused = asyncio.run(load_groups(db, ("active",)))
    assert [rt.group.name for rt in runtimes] == ["v1"] and refused == []


def test_v1_on_1m_is_still_refused_verbatim():
    acct = _acct()
    bad = group_for(acct, name="bad", timeframe="1m")
    with pytest.raises(GroupRefused) as e:
        validate_group(bad)
    assert str(e.value) == "bad: timeframe '1m' is not supported"
    runtimes, refused = asyncio.run(load_groups(_TradeDb([(bad, acct)]), ("active",)))
    assert runtimes == [] and refused == ["bad: timeframe '1m' is not supported"]


def test_an_invalid_1m_group_is_refused_by_load_groups():
    acct = _acct()
    db = _TradeDb([(_orb_group(acct, screener_json={}), acct)])
    runtimes, refused = asyncio.run(load_groups(db, ("active",)))
    assert runtimes == [] and refused == ["orb: screener_json: 1m groups need screener rules"]
