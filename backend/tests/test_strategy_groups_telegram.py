"""P1 strategy groups in the Telegram commands. The single-group /status must
stay byte-identical to the pre-group reply (the replay harness's S4 baseline);
every test that can reach /kill or /resume points the HALT sentinel at tmp_path
first — the real one lives in the live runtime directory."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import app.models_registry  # noqa: F401

from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord, Recommendation, SimAccount
from app.modules.simledger.service import SimLedgerService, _dec
from app.tasks import telegram_tasks
from tests._groups import groups_loader, runtime_for

# The replay harness's S4 /status (status_baseline.txt, old code, 2026-10-17).
BASELINE_STATUS = (
    "CAT 对照账户\n"
    "cash: $654.32\n"
    "positions:\n"
    "  KVUE: 34.55 @ 18.93 stop 18.27\n"
    "  KMI: 19.1 @ 34.23 stop 32.68\n"
    "entries: ✅ allowed\n"
    "next shortlist: KVUE, KMI, KDP, MDT, CSX, ABT, WMT, PG, OXY, PYPL\n"
    "heartbeats: entry_cycle 05:43, market_eod_correction 01:30, market_stream 23:12, "
    "position_cycle 05:43, signal_cycle 05:43, signal_cycle_retry 00:00, "
    "signal_cycle_started 05:43, telegram_updates 23:12, worker 23:12")

BEATS = {"entry_cycle": "05:43", "market_eod_correction": "01:30",
         "market_stream": "23:12", "position_cycle": "05:43", "signal_cycle": "05:43",
         "signal_cycle_retry": "00:00", "signal_cycle_started": "05:43",
         "telegram_updates": "23:12", "worker": "23:12"}


def _acct(cash):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="system-对照", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


def _pos(symbol, shares, avg_cost, stop):
    return SimpleNamespace(symbol=symbol, shares=_dec(shares), avg_cost=_dec(avg_cost),
                           stop=_dec(stop))


class _List(list):
    def all(self):
        return list(self)


class _Db:
    def __init__(self, shortlist_by_group):
        self.shortlist_by_group = shortlist_by_group

    async def execute(self, stmt):
        cols = stmt.column_descriptions
        entity = cols[0].get("entity") if cols else None
        rows = []
        if entity is Recommendation:
            sql = str(stmt.compile(dialect=postgresql.dialect(),
                                   compile_kwargs={"literal_binds": True}))
            rows = next((syms for gid, syms in self.shortlist_by_group.items()
                         if str(gid) in sql), [])
        elif entity is HeartbeatRecord:
            rows = [SimpleNamespace(name=name, last_beat_at=datetime.strptime(
                f"2026-10-17 {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc))
                for name, hhmm in BEATS.items()]

        class _R:
            def scalars(self):
                return _List(rows)
        return _R()


@pytest.fixture
def world(monkeypatch, tmp_path):
    sentinel = tmp_path / "HALT"
    monkeypatch.setattr(cycles, "HALT_SENTINEL", str(sentinel))
    monkeypatch.setitem(cycles.entries_blocked_reason.__kwdefaults__, "sentinel_path",
                        str(sentinel))
    states = {}

    async def fake_state(db, account, *, create=False):
        if create and account.id not in states:
            states[account.id] = SimpleNamespace(paused_until=None, halted=False,
                                                 halted_until=None, reason=None)
        return states.get(account.id)

    positions = {}

    async def fake_positions(db, account_id):
        return positions.get(account_id, [])

    monkeypatch.setattr(cycles, "get_safety_state", fake_state)
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    return SimpleNamespace(monkeypatch=monkeypatch, sentinel=sentinel, states=states,
                           positions=positions)


def _two_groups(world):
    a = runtime_for(_acct(654.32), name="v1 对照")
    b = runtime_for(_acct(2000), name="beta", status="paused")
    world.positions[a.account.id] = [_pos("KVUE", 34.546204, 18.93, 18.27)]
    world.monkeypatch.setattr(telegram_tasks, "load_groups", groups_loader(a, b))
    db = _Db({a.group.id: ["KVUE"], b.group.id: ["BBB"]})
    return a, b, db


def _run(db, text):
    return asyncio.run(telegram_tasks._handle_command(db, text))


def test_single_group_status_is_the_pre_group_reply(world):
    rt = runtime_for(_acct(654.32))
    world.positions[rt.account.id] = [_pos("KVUE", 34.546204, 18.93, 18.27),
                                      _pos("KMI", 19.101425, 34.23, 32.68)]
    world.monkeypatch.setattr(telegram_tasks, "load_groups", groups_loader(rt))
    db = _Db({rt.group.id: ["KVUE", "KMI", "KDP", "MDT", "CSX", "ABT", "WMT", "PG",
                            "OXY", "PYPL"]})
    assert _run(db, "/status") == BASELINE_STATUS


def test_status_lists_every_group_with_its_own_shortlist(world):
    _, _, db = _two_groups(world)
    out = _run(db, "/status")
    assert out.splitlines()[:7] == [
        "CAT", "[v1 对照] (active)", "cash: $654.32", "positions:",
        "  KVUE: 34.55 @ 18.93 stop 18.27", "entries: ✅ allowed",
        "next shortlist: KVUE"]
    assert "[beta] (paused)\ncash: $2,000.00\npositions:\n  (none)" in out
    assert "next shortlist: BBB" in out
    assert out.splitlines()[-1].startswith("heartbeats: entry_cycle 05:43")


def test_status_for_one_named_group(world):
    _, _, db = _two_groups(world)
    out = _run(db, "/status@cat_bot   v1 对照")
    assert out.splitlines()[0] == "CAT v1 对照"
    assert "next shortlist: KVUE" in out and "BBB" not in out


def test_status_for_an_unknown_group_names_the_real_ones(world):
    _, _, db = _two_groups(world)
    assert _run(db, "/status v1") == "unknown group: v1 — groups: v1 对照, beta"


def test_no_groups_answers_every_command(world):
    world.monkeypatch.setattr(telegram_tasks, "load_groups", groups_loader())
    for command in ("/status", "/pause", "/kill"):
        assert _run(_Db({}), command) == "no strategy groups"
    assert not world.sentinel.exists()


def test_pause_and_resume_act_on_every_managed_group(world):
    a, b, db = _two_groups(world)
    assert _run(db, "/pause") == "⏸ entries paused for 30 days — /resume to lift"
    for rt in (a, b):
        state = world.states[rt.account.id]
        assert state.paused_until == date.today() + timedelta(days=30)
        assert state.reason == "manual /pause"
    assert _run(db, "/resume") == "▶️ entries resumed (pause/halt cleared, sentinel removed)"
    assert all(s.paused_until is None and s.reason == "manual /resume"
               for s in world.states.values())


def test_kill_halts_every_group_and_writes_only_the_test_sentinel(world):
    a, b, db = _two_groups(world)
    assert _run(db, "/kill").startswith("🛑 KILLED")
    assert world.sentinel.exists()
    assert all(s.halted and s.reason == "manual /kill" for s in world.states.values())
    assert len(world.states) == 2
    assert "🚫 HALT sentinel present" in _run(db, "/status")
    _run(db, "/resume")
    assert not world.sentinel.exists()
    assert all(not s.halted for s in world.states.values())
