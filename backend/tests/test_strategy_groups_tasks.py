"""P1 strategy groups in the celery tasks: signal / entry / position cycles with
two groups, zero groups, a paused group and a refused group. The single-group
path is pinned by the pre-existing cycle tests (byte-identical meta, messages
and return strings)."""

import asyncio
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

import app.models_registry  # noqa: F401
from quant import config as qconfig
from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec

from app.modules.llm import explain as explain_mod
from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, Recommendation, SimAccount
from app.modules.simledger.service import SimLedgerService, _dec
from app.modules.strategy_groups import service as group_service
from app.modules.strategy_groups.models import StrategyGroupRun
from app.modules.strategy_groups.service import GroupRuntime, validate_group
from app.tasks import quant_tasks
from tests._groups import group_for, groups_loader

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 7, 30)
# constituents AAA/BBB + the ETF whitelist = what every group scans and syncs
N_SYMS = len({"AAA", "BBB"} | set(qconfig.ETF_WHITELIST))


def _acct(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name=f"sys-{uuid4().hex[:6]}",
                      is_system=True, starting_capital=_dec(2000), cash=_dec(cash))


def _runtime(name, status="active", **params):
    acct = _acct()
    spec = get_spec(V1_COMPOSITE_KEY)
    p = spec.default_params()
    for section, values in params.items():
        p[section] = {**p[section], **values}
    group = group_for(acct, name=name, status=status, params_json=p)
    return GroupRuntime(group, acct, validate_group(group))


class _List(list):
    def all(self):
        return list(self)


class _Db:
    """Answers the cycles' reads from per-group recommendation lists, choosing
    the list by the group id that appears in the compiled SQL — so a query that
    forgot its group filter sees nothing."""

    def __init__(self, recs_by_group=None):
        self.recs_by_group = recs_by_group or {}
        self.added = []
        self.committed = 0
        self.savepoints = 0
        self.rollbacks = 0

    def _group_recs(self, sql):
        for gid, recs in self.recs_by_group.items():
            if str(gid) in sql:
                return recs
        return []

    async def execute(self, stmt):
        try:
            sql = str(stmt.compile(dialect=postgresql.dialect(),
                                   compile_kwargs={"literal_binds": True}))
        except Exception:
            sql = str(stmt)
        cols = getattr(stmt, "column_descriptions", None) or []
        entity = cols[0].get("entity") if cols else None
        rows, one = [], None
        if entity is Recommendation:
            recs = self._group_recs(sql)
            name = cols[0].get("name")
            if name == "id":
                one = recs[0].id if recs else None
            elif name == "symbol":
                rows = [r.symbol for r in recs]
            else:
                rows = recs

        class _R:
            def scalars(self):
                return _List(rows)

            def scalar_one_or_none(self):
                return one

            def all(self):
                return []

            def first(self):
                return ("claimed",)
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed += 1

    def begin_nested(self):
        db = self

        class _Savepoint:
            async def __aenter__(self_inner):
                db.savepoints += 1

            async def __aexit__(self_inner, exc_type, *a):
                if exc_type is not None:
                    db.rollbacks += 1
                return False
        return _Savepoint()


class _PriorErrorDb(_Db):
    """Today already holds an error run row for every group and cycle."""

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", None) or []
        if cols and cols[0].get("entity") is StrategyGroupRun:
            return SimpleNamespace(scalar_one_or_none=lambda: uuid4())
        return await super().execute(stmt)


class _Factory:
    def __init__(self, db_fn=_Db):
        self.db_fn = db_fn
        self.dbs = []

    def __call__(self):
        db = self.db_fn()
        self.dbs.append(db)

        class _Ctx:
            async def __aenter__(self_inner):
                return db

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


def _beats(factory, name):
    return [o for db in factory.dbs for o in db.added
            if isinstance(o, HeartbeatRecord) and o.name == name]


@pytest.fixture
def notes(monkeypatch):
    out = []

    async def fake_notify(msg, *_a, **_kw):
        out.append(msg)
        return True

    monkeypatch.setattr(quant_tasks, "_notify", fake_notify)
    return out


# --- signal_cycle -------------------------------------------------------------

@pytest.fixture
def signal_world(monkeypatch):
    from quant.data import corporate_actions as qactions
    from quant.data import fetch as qfetch
    from quant.data import sectors as qsectors
    from quant.data import universe as quniverse

    calls = SimpleNamespace(build=[], store=[], explain=[], exits=[], protections=[])
    factory = _Factory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 7, 30, 17, 30, tzinfo=ET))
    monkeypatch.setattr(quniverse, "constituents_on", lambda d: ["AAA", "BBB"])
    monkeypatch.setattr(quniverse, "top_liquid", lambda syms, **kw: list(syms))
    monkeypatch.setattr(qsectors, "load_sectors", lambda: {})
    monkeypatch.setattr(qactions, "sync_universe", lambda *a, **kw: 0)
    monkeypatch.setattr(qfetch, "sync_daily_many",
                        lambda symbols, *a, **kw: (len(symbols), []))

    def fake_build(symbols, session_date, **kw):
        calls.build.append(kw)
        return cycles.RecommendationBatch(
            rows=[{"symbol": "AAA", "trade_date": date(2026, 7, 31),
                   "shortlist_rank": 1}], scanned=len(symbols))

    async def fake_store(db, recs, *, group_id):
        calls.store.append(group_id)
        return len(recs)

    async def fake_explain(db, trade_date, *, group_id, top_n=10):
        calls.explain.append(group_id)
        return 1

    async def fake_exits(db, account, today, **kw):
        calls.exits.append((account.id, kw["exit_params"], kw["strategy_params"]))
        return cycles.ExitPass(closed=[f"X{len(calls.exits)}"])

    async def fake_drift(db, account, today, **kw):
        return []

    async def fake_positions(db, account_id):
        return []

    async def fake_protections(db, account, equity, today, **kw):
        calls.protections.append(account.id)

    async def fake_snapshot(*a, **kw):
        return None

    monkeypatch.setattr(cycles, "build_recommendations", fake_build)
    monkeypatch.setattr(cycles, "store_recommendations", fake_store)
    monkeypatch.setattr(explain_mod, "explain_recommendations", fake_explain)
    monkeypatch.setattr(cycles, "daily_exit_management", fake_exits)
    monkeypatch.setattr(cycles, "open_fill_drift", fake_drift)
    monkeypatch.setattr(cycles, "update_protections", fake_protections)
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "snapshot", staticmethod(fake_snapshot))
    return SimpleNamespace(calls=calls, factory=factory, monkeypatch=monkeypatch)


def test_two_groups_publish_with_their_own_params_and_group_ids(signal_world, notes):
    a = _runtime("alpha")
    b = _runtime("beta", strategy={"ma_fast": 7}, funnel={"min_confidence": 80.0},
                 exits={"reversal_bars": 4})
    p = _runtime("parked", status="paused")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b, p))

    out = quant_tasks.signal_cycle()
    calls = signal_world.calls

    assert [kw["strategy_params"] for kw in calls.build] == [a.config.strategy,
                                                             b.config.strategy]
    assert [kw["funnel_params"].min_confidence for kw in calls.build] == [65.0, 80.0]
    assert calls.store == [a.group.id, b.group.id]          # paused publishes nothing
    assert calls.explain == [a.group.id, b.group.id]
    assert [e[0] for e in calls.exits] == [a.account.id, b.account.id, p.account.id]
    assert calls.exits[1][1] == replace(a.config.exits, reversal_bars=4)
    assert calls.protections == [a.account.id, b.account.id, p.account.id]

    beat = _beats(signal_world.factory, "signal_cycle")[0]
    assert beat.meta["recs"] == 2 and beat.meta["scanned"] == 2 * N_SYMS
    assert beat.meta["closed"] == ["X1", "X2", "X3"]
    assert set(beat.meta["groups"]) == {"alpha", "beta", "parked"}
    assert beat.meta["groups"]["parked"]["recs"] == 0
    assert beat.meta["groups"]["beta"]["closed"] == ["X2"]
    assert "fail_closed" not in beat.meta
    assert "[alpha] 🔎 明日 shortlist: AAA" in notes
    assert "[parked] 📤 对照账户 daily exits: X3" in notes
    assert out.startswith("recs=2 closed=['X1', 'X2', 'X3']")


def test_platform_min_confidence_only_tightens_a_group(signal_world, notes):
    signal_world.monkeypatch.setattr(
        quant_tasks, "effective_settings",
        lambda db: _async(SimpleNamespace(
            min_confidence=70.0, rejected=(), daily_loss_pause_pct=0.02,
            portfolio_drawdown_halt_pct=0.15)))
    a = _runtime("alpha")
    b = _runtime("beta", funnel={"min_confidence": 80.0})
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    quant_tasks.signal_cycle()
    assert [kw["funnel_params"].min_confidence
            for kw in signal_world.calls.build] == [70.0, 80.0]


async def _async(value):
    return value


def test_a_refused_group_is_loud_and_publishes_nothing(signal_world, notes):
    a = _runtime("alpha")
    signal_world.monkeypatch.setattr(
        quant_tasks, "load_groups",
        groups_loader(a, refused=["bad: timeframe '1m' is not supported"]))
    quant_tasks.signal_cycle()
    beat = _beats(signal_world.factory, "signal_cycle")[0]
    assert signal_world.calls.store == [a.group.id]
    assert beat.meta["groups_refused"] == ["bad: timeframe '1m' is not supported"]
    assert ("strategy group refused: bad: timeframe '1m' is not supported"
            in beat.meta["fail_closed"])
    assert any(n.startswith("⛔ signal_cycle fail-closed:") and "refused" in n
               for n in notes)


def test_signal_cycle_with_no_groups_still_lands(signal_world, notes):
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader())
    out = quant_tasks.signal_cycle()
    beat = _beats(signal_world.factory, "signal_cycle")[0]
    assert beat.meta == {"session": "2026-07-30", "seq": 0, "synced": N_SYMS,
                         "failed": 0, "recs": 0, "scanned": 0, "groups": 0}
    assert signal_world.calls.build == [] and signal_world.calls.exits == []
    assert out == f"no strategy groups: synced={N_SYMS}/{N_SYMS}"


def test_load_groups_refuses_without_raising(monkeypatch):
    ok_acct, bad_acct = _acct(), _acct()
    ok = group_for(ok_acct, name="ok")
    bad = group_for(bad_acct, name="bad", timeframe="1m")

    class _RowsDb:
        async def execute(self, stmt):
            return SimpleNamespace(all=lambda: [(ok, ok_acct), (bad, bad_acct)])

    runtimes, refused = asyncio.run(group_service.load_groups(_RowsDb(), ("active",)))
    assert [rt.group.name for rt in runtimes] == ["ok"]
    assert runtimes[0].account is ok_acct
    assert refused == ["bad: timeframe '1m' is not supported"]


# --- entry_cycle ----------------------------------------------------------------

def _rec(symbol, group_id):
    return Recommendation(id=uuid4(), group_id=group_id, symbol=symbol,
                          trade_date=SESSION, direction="up", confidence=_dec(70),
                          shortlist_rank=1, phase="up", phase_reason="",
                          features={"stop_distance": 2.0, "adv": 5e7, "price": 100.0})


@pytest.fixture
def entry_world(monkeypatch):
    from quant.data import fetch as qfetch

    keys = []

    async def fake_open(db, account, **kw):
        keys.append(kw["idempotency_key"])
        return object()

    async def no_positions(db, account_id, *a):
        return []

    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 7, 30, 9, 50, tzinfo=ET))
    monkeypatch.setattr(quant_tasks, "FinnhubClient", lambda *a, **kw: object())
    monkeypatch.setattr(qfetch, "session_open_prices",
                        lambda symbols, day, **kw: {s: 100.0 for s in symbols})
    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(no_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(no_positions))
    monkeypatch.setattr(cycles, "entries_blocked_reason",
                        lambda state, *, today, **kw: state)
    return SimpleNamespace(keys=keys, monkeypatch=monkeypatch)


def test_two_groups_book_into_their_own_accounts(entry_world, notes):
    a, b = _runtime("alpha"), _runtime("beta")
    recs = {a.group.id: [_rec("AAA", a.group.id)], b.group.id: [_rec("BBB", b.group.id)]}
    factory = _Factory(lambda: _Db(recs))
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def no_state(db, account, **kw):
        return None

    entry_world.monkeypatch.setattr(cycles, "get_safety_state", no_state)

    out = quant_tasks.entry_cycle()
    assert entry_world.keys == [f"entry:{a.account.id}:AAA:2026-07-30",
                                f"entry:{b.account.id}:BBB:2026-07-30"]
    beat = _beats(factory, "entry_cycle")[0]
    assert beat.meta == {"groups": {"alpha": {"booked": ["AAA"], "mode": "open_once"},
                                    "beta": {"booked": ["BBB"], "mode": "open_once"}}}
    assert out == "[alpha] booked: ['AAA']; [beta] booked: ['BBB']"
    assert notes == ["[alpha] 📈 对照账户 entries booked: AAA",
                     "[beta] 📈 对照账户 entries booked: BBB"]


def test_a_blocked_or_empty_group_does_not_stop_the_other(entry_world, notes):
    a, b, c = _runtime("alpha"), _runtime("beta"), _runtime("gamma")
    recs = {a.group.id: [_rec("AAA", a.group.id)], b.group.id: [_rec("BBB", b.group.id)]}
    factory = _Factory(lambda: _Db(recs))
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b, c))

    async def state_for(db, account, **kw):
        return "halted: drawdown" if account is a.account else None

    entry_world.monkeypatch.setattr(cycles, "get_safety_state", state_for)

    out = quant_tasks.entry_cycle()
    assert entry_world.keys == [f"entry:{b.account.id}:BBB:2026-07-30"]
    beat = _beats(factory, "entry_cycle")[0]
    assert beat.meta["groups"]["alpha"] == {"blocked": "halted: drawdown"}
    assert beat.meta["groups"]["gamma"] == {"fail_closed": "no recommendations"}
    assert out == ("[alpha] blocked: halted: drawdown; [beta] booked: ['BBB']; "
                   "[gamma] fail-closed: no recommendations")


def test_paused_groups_book_nothing(entry_world, notes):
    p = _runtime("parked", status="paused")
    factory = _Factory()
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(p))
    assert quant_tasks.entry_cycle() == "no active strategy groups"
    assert _beats(factory, "entry_cycle") == []
    assert entry_world.keys == []


def test_open_price_alert_flag_is_kept_per_group(monkeypatch):
    row = HeartbeatRecord(name="entry_cycle",
                          meta={"groups": {"alpha": {"booked": []}, "beta": {}}})

    class _RowDb:
        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: row)

        async def commit(self):
            pass

    asyncio.run(quant_tasks._stamp_open_price_alert(_RowDb(), SESSION, "beta"))
    assert row.meta == {"groups": {"alpha": {"booked": []},
                                   "beta": {"open_price_alert": "2026-07-30"}}}


# --- position_cycle ---------------------------------------------------------------

def test_two_groups_check_stops_on_both_accounts(monkeypatch, notes):
    a, b = _runtime("alpha"), _runtime("beta", status="paused")
    factory = _Factory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_in_rth", lambda *a, **kw: True)
    monkeypatch.setattr(quant_tasks, "FinnhubClient", lambda *a, **kw: object())
    monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    seen = []

    async def fake_stops(db, account, *, quote_fn):
        seen.append(account.id)
        return ["KDP"] if account is b.account else []

    monkeypatch.setattr(cycles, "check_stops", fake_stops)
    out = quant_tasks.position_cycle()
    assert seen == [a.account.id, b.account.id]
    beat = _beats(factory, "position_cycle")[0]
    assert beat.meta == {"closed": ["KDP"],
                         "groups": {"alpha": {"closed": []}, "beta": {"closed": ["KDP"]}}}
    assert notes == ["[beta] 🛑 对照账户 stop exit: KDP"]
    assert out == "[alpha] no breaches; [beta] closed: ['KDP']"


def test_position_cycle_with_no_groups_still_beats(monkeypatch, notes):
    factory = _Factory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_in_rth", lambda *a, **kw: True)
    monkeypatch.setattr(quant_tasks, "load_groups", groups_loader())
    assert quant_tasks.position_cycle() == "no strategy groups"
    assert [b.meta for b in _beats(factory, "position_cycle")] == [{"closed": []}]
    assert factory.dbs[0].committed == 1


# --- P3a: one group's crash is isolated (N>1 only) --------------------------------

def _runs(factory, cycle):
    return {r.group_id: r for db in factory.dbs for r in db.added
            if isinstance(r, StrategyGroupRun) and r.cycle == cycle}


def _signal_db(factory):
    return next(db for db in factory.dbs if _beats_in(db, "signal_cycle"))


def _beats_in(db, name):
    return [o for o in db.added if isinstance(o, HeartbeatRecord) and o.name == name]


def test_recs_crash_rolls_back_one_group_and_its_book_still_runs(signal_world, notes):
    a, b = _runtime("alpha"), _runtime("beta", strategy={"ma_fast": 7})
    calls = signal_world.calls
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def store(db, recs, *, group_id):
        if group_id == a.group.id:
            raise RuntimeError("boom")
        calls.store.append(group_id)
        return len(recs)

    signal_world.monkeypatch.setattr(cycles, "store_recommendations", store)
    out = quant_tasks.signal_cycle()

    assert calls.store == [b.group.id]
    assert [e[0] for e in calls.exits] == [a.account.id, b.account.id]
    beat = _beats(signal_world.factory, "signal_cycle")[0]
    assert beat.meta["groups_crashed"] == ["alpha:recs"]
    assert beat.meta["groups"]["alpha"]["error"] == "RuntimeError: boom"
    assert beat.meta["groups"]["alpha"]["recs"] == 0
    assert "[alpha] crashed in recs: RuntimeError" in beat.meta["fail_closed"]
    assert beat.meta["recs"] == 1
    assert cycles.signal_cycle_data_failure(beat.meta) == \
        "1 group step(s) crashed: alpha:recs"
    runs = _runs(signal_world.factory, "signal")
    assert runs[a.group.id].outcome == "error"
    assert runs[a.group.id].meta["error"] == "RuntimeError: boom"
    assert runs[b.group.id].outcome == "ok"
    assert runs[b.group.id].session_date == SESSION and runs[b.group.id].seq == 0
    db = _signal_db(signal_world.factory)
    assert (db.savepoints, db.rollbacks) == (4, 1)
    assert not any(n.startswith("[alpha] 🔎") for n in notes)
    assert "[beta] 🔎 明日 shortlist: AAA" in notes
    assert "[alpha] 📤 对照账户 daily exits: X1" in notes
    assert out.startswith("recs=1 ")


def test_book_crash_drops_that_groups_exit_report(signal_world, notes):
    a, b = _runtime("alpha"), _runtime("beta")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def protections(db, account, equity, today, **kw):
        if account is a.account:
            raise ValueError("bad mark")

    signal_world.monkeypatch.setattr(cycles, "update_protections", protections)
    quant_tasks.signal_cycle()

    beat = _beats(signal_world.factory, "signal_cycle")[0]
    assert beat.meta["groups_crashed"] == ["alpha:book"]
    assert beat.meta["groups"]["alpha"]["closed"] == []
    assert beat.meta["closed"] == ["X2"]
    assert signal_world.calls.store == [a.group.id, b.group.id]
    runs = _runs(signal_world.factory, "signal")
    assert (runs[a.group.id].outcome, runs[b.group.id].outcome) == ("error", "ok")
    assert not any("daily exits: X1" in n for n in notes)
    assert "[beta] 📤 对照账户 daily exits: X2" in notes
    assert "[alpha] 🔎 明日 shortlist: AAA" in notes


def test_paused_group_outcome_is_fail_closed_only_with_issues(signal_world, notes):
    a = _runtime("alpha")
    quiet, loud = _runtime("quiet", status="paused"), _runtime("loud", status="paused")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups",
                                     groups_loader(a, quiet, loud))

    async def exits(db, account, today, **kw):
        return cycles.ExitPass(unadjusted=["ZZZ"] if account is loud.account else [])

    signal_world.monkeypatch.setattr(cycles, "daily_exit_management", exits)
    quant_tasks.signal_cycle()
    runs = _runs(signal_world.factory, "signal")
    assert runs[a.group.id].outcome == "ok"
    assert runs[quiet.group.id].outcome == "skipped"
    assert runs[loud.group.id].outcome == "fail_closed"
    assert "groups_crashed" not in _beats(signal_world.factory, "signal_cycle")[0].meta


def test_single_group_crash_is_not_caught(signal_world, notes):
    a = _runtime("alpha")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a))

    def build(symbols, session_date, **kw):
        raise RuntimeError("boom")

    signal_world.monkeypatch.setattr(cycles, "build_recommendations", build)
    assert quant_tasks.signal_cycle() == "error"
    added = [o for db in signal_world.factory.dbs for o in db.added]
    assert not any(isinstance(o, StrategyGroupRun) for o in added)
    assert _beats(signal_world.factory, "signal_cycle") == []
    assert all(db.savepoints == 0 for db in signal_world.factory.dbs)


def _entry_crash_world(entry_world, db_fn):
    a, b = _runtime("alpha"), _runtime("beta")
    recs = {a.group.id: [_rec("AAA", a.group.id)], b.group.id: [_rec("BBB", b.group.id)]}
    factory = _Factory(lambda: db_fn(recs))
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def state(db, account, **kw):
        if account is a.account:
            raise RuntimeError("db hiccup")
        return None

    entry_world.monkeypatch.setattr(cycles, "get_safety_state", state)
    return a, b, factory


def test_entry_crash_is_isolated_and_alerted_once(entry_world, notes):
    a, b, factory = _entry_crash_world(entry_world, _Db)
    out = quant_tasks.entry_cycle()
    assert entry_world.keys == [f"entry:{b.account.id}:BBB:2026-07-30"]
    beat = _beats(factory, "entry_cycle")[0]
    assert beat.meta["groups"]["alpha"] == {"error": "RuntimeError: db hiccup"}
    runs = _runs(factory, "entry")
    assert (runs[a.group.id].outcome, runs[b.group.id].outcome) == ("error", "ok")
    assert runs[b.group.id].meta == {"booked": ["BBB"], "mode": "open_once"}
    assert notes == ["[alpha] ⛔ entry_cycle crashed: RuntimeError — 该组今天不进场",
                     "[beta] 📈 对照账户 entries booked: BBB"]
    assert out == "[alpha] error: RuntimeError; [beta] booked: ['BBB']"
    assert (factory.dbs[0].savepoints, factory.dbs[0].rollbacks) == (2, 1)


def test_entry_crash_alert_is_deduped_per_day(entry_world, notes):
    _entry_crash_world(entry_world, _PriorErrorDb)
    quant_tasks.entry_cycle()
    assert not any("entry_cycle crashed" in n for n in notes)


def test_entry_skipped_groups_write_no_run_row(entry_world, notes):
    a, b = _runtime("alpha"), _runtime("beta")
    factory = _Factory()
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    entry_world.monkeypatch.setattr(quant_tasks, "_now_et",
                                    lambda: datetime(2026, 7, 30, 11, 30, tzinfo=ET))
    quant_tasks.entry_cycle()
    assert _runs(factory, "entry") == {}
    assert len(_beats(factory, "entry_cycle")) == 1


def _position_crash_world(monkeypatch, db_fn=_Db):
    a, b = _runtime("alpha"), _runtime("beta", status="paused")
    factory = _Factory(db_fn)
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_in_rth", lambda *a, **kw: True)
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 7, 30, 11, 0, tzinfo=ET))
    monkeypatch.setattr(quant_tasks, "FinnhubClient", lambda *a, **kw: object())
    monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def stops(db, account, *, quote_fn):
        if account is a.account:
            raise RuntimeError("quote feed")
        return ["KDP"]

    monkeypatch.setattr(cycles, "check_stops", stops)
    return a, b, factory


def test_position_crash_still_checks_the_other_group(monkeypatch, notes):
    a, b, factory = _position_crash_world(monkeypatch)
    out = quant_tasks.position_cycle()
    beat = _beats(factory, "position_cycle")[0]
    assert beat.meta == {"closed": ["KDP"], "groups_crashed": ["alpha"],
                         "groups": {"alpha": {"closed": [], "error": "RuntimeError: quote feed"},
                                    "beta": {"closed": ["KDP"]}}}
    runs = _runs(factory, "position")
    assert runs[a.group.id].outcome == "error"
    assert runs[a.group.id].meta == {"closed": [], "error": "RuntimeError: quote feed"}
    assert (runs[b.group.id].outcome, runs[b.group.id].meta) == ("ok", {"closed": ["KDP"]})
    assert runs[b.group.id].session_date == SESSION
    assert notes == ["[alpha] ⛔ position_cycle crashed: RuntimeError — 该组 stop 未检查",
                     "[beta] 🛑 对照账户 stop exit: KDP"]
    assert out == "[alpha] error: RuntimeError; [beta] closed: ['KDP']"
    assert factory.dbs[0].committed == 1
    assert (factory.dbs[0].savepoints, factory.dbs[0].rollbacks) == (2, 1)


def test_single_group_position_crash_is_not_caught(monkeypatch, notes):
    a, _, factory = _position_crash_world(monkeypatch)
    monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a))
    assert quant_tasks.position_cycle() == "error"
    added = [o for db in factory.dbs for o in db.added]
    assert not any(isinstance(o, StrategyGroupRun) for o in added)
    assert all(db.savepoints == 0 for db in factory.dbs)


def test_position_crash_alert_is_deduped_per_day(monkeypatch, notes):
    _position_crash_world(monkeypatch, _PriorErrorDb)
    quant_tasks.position_cycle()
    assert notes == ["[beta] 🛑 对照账户 stop exit: KDP"]


# --- P3c trade settings reach only their own group --------------------------------

def _with_trade(rt, **kw):
    from quant.engine.trade_rules import TradeSettings

    return replace(rt, trade=TradeSettings(take_profit_pct=0.05), **kw)


def test_score_floor_tightens_only_its_own_group(signal_world, notes):
    a = _with_trade(_runtime("alpha"), score_floor=80.0)
    b = _runtime("beta")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    quant_tasks.signal_cycle()
    assert [kw["funnel_params"].min_confidence
            for kw in signal_world.calls.build] == [65.0, 65.0]

    signal_world.calls.build.clear()
    from quant.engine.trade_rules import TradeSettings
    a = replace(a, trade=TradeSettings(entry_prob_min=0.55,
                                       calibration_run_id=str(uuid4())))
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    quant_tasks.signal_cycle()
    assert [kw["funnel_params"].min_confidence
            for kw in signal_world.calls.build] == [80.0, 65.0]


def test_exit_pass_gets_each_groups_own_trade_settings(signal_world, notes):
    a, b = _with_trade(_runtime("alpha")), _runtime("beta")
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))
    seen = []

    async def exits(db, account, today, **kw):
        seen.append((account.id, kw.get("trade_settings")))
        return cycles.ExitPass()

    signal_world.monkeypatch.setattr(cycles, "daily_exit_management", exits)
    quant_tasks.signal_cycle()
    assert seen == [(a.account.id, a.trade), (b.account.id, None)]


def test_entries_get_each_groups_own_trade_settings(entry_world, notes):
    a, b = _with_trade(_runtime("alpha")), _runtime("beta")
    recs = {a.group.id: [_rec("AAA", a.group.id)], b.group.id: [_rec("BBB", b.group.id)]}
    factory = _Factory(lambda: _Db(recs))
    entry_world.monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    entry_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a, b))

    async def no_state(db, account, **kw):
        return None

    entry_world.monkeypatch.setattr(cycles, "get_safety_state", no_state)
    real = cycles.run_entries
    seen = []

    async def spy(db, account, today, **kw):
        seen.append((account.id, kw.get("trade_settings")))
        return await real(db, account, today, **kw)

    entry_world.monkeypatch.setattr(cycles, "run_entries", spy)
    quant_tasks.entry_cycle()
    assert seen == [(a.account.id, a.trade), (b.account.id, None)]
    assert entry_world.keys == [f"entry:{a.account.id}:AAA:2026-07-30",
                                f"entry:{b.account.id}:BBB:2026-07-30"]


def test_unreachable_score_floor_still_runs_the_build(signal_world, notes):
    import math

    from quant.engine.trade_rules import TradeSettings

    a = replace(_runtime("alpha"), score_floor=math.inf,
                trade=TradeSettings(entry_prob_min=0.9, calibration_run_id=str(uuid4())))
    signal_world.monkeypatch.setattr(quant_tasks, "load_groups", groups_loader(a))
    quant_tasks.signal_cycle()
    assert [kw["funnel_params"].min_confidence
            for kw in signal_world.calls.build] == [math.inf]
