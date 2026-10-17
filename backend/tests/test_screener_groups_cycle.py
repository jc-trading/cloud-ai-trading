"""P2 screener groups in signal_cycle: a legacy-only install never touches the
screener path; a screener group scans its pool (previous session's screen +
held − exclude, no watchlist / ETF whitelist) next to the legacy group; an
unavailable screen, a failed pool sync or an over-cap pool fail closed for
that group only."""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from quant import config as qconfig
from quant.engine import screener

from app.modules.simledger import cycles
from app.modules.simledger.service import SimLedgerService
from app.modules.strategy_groups.service import GroupRuntime, validate_group
from app.tasks import quant_tasks
from tests._groups import group_for, groups_loader
from tests.test_strategy_groups_tasks import (  # noqa: F401  (fixtures)
    _acct, _beats, notes, signal_world)

AS_OF = date(2026, 7, 29)                 # previous session of 2026-07-30
P1_KEYS = {"session", "seq", "recs", "explained", "closed", "synced", "failed",
           "scanned", "stale", "unadjusted", "exits_unadjusted"}
SPEC = {"rules": [{"field": "price", "op": ">=", "value": 10}], "exclude": ["EXCL"]}


def _screened(name="screened", spec=SPEC):
    acct = _acct()
    group = group_for(acct, name=name, screener_json=spec)
    return GroupRuntime(group, acct, validate_group(group), screener.parse_spec(spec))


def _legacy(name="v1 对照"):
    acct = _acct()
    group = group_for(acct, name=name)
    return GroupRuntime(group, acct, validate_group(group))


def _recorder(touched, name):
    """Records the call instead of raising: signal_cycle's broad except around
    the screen would swallow an exception and hide the call."""
    def record(*a, **kw):
        touched.append(name)
        raise AssertionError(f"{name} reached")
    return record


@pytest.fixture
def world(signal_world):
    from quant.data import corporate_actions as qactions
    from quant.data import fetch as qfetch
    from quant.data import screen as qscreen

    mp = signal_world.monkeypatch
    built = []
    synced = []
    screens = []
    held_by_account = {}

    def fake_build(symbols, session_date, **kw):
        built.append(list(symbols))
        return cycles.RecommendationBatch(
            rows=[{"symbol": symbols[0] if symbols else "AAA",
                   "trade_date": date(2026, 7, 31), "shortlist_rank": 1}],
            scanned=len(symbols))

    def fake_sync(symbols, *a, **kw):
        synced.append(list(symbols))
        return len(symbols), []

    def fake_screen(spec, as_of, *, held=()):
        screens.append((as_of, set(held)))
        features = pd.DataFrame({"price": [20.0, 30.0, 1.0, 40.0, 11.0, 12.0, 50.0]},
                                index=["AAA", "PX1", "PX2", "PX3", "PX4", "PX5", "EXCL"])
        return screener.apply(spec, features, held, as_of=as_of)

    async def positions(db, account_id):
        return [SimpleNamespace(symbol=s) for s in held_by_account.get(account_id, ())]

    async def marks(positions, today, **kw):
        return SimpleNamespace(marks={}, stale_close=[], raw_or_missing=[], last_close={})

    async def watchlist(db, account):
        return {"WATCH"}

    mp.setattr(cycles, "build_recommendations", fake_build)
    mp.setattr(qfetch, "sync_daily_many", fake_sync)
    mp.setattr(qscreen, "run_screen", fake_screen)
    mp.setattr(qactions, "sync_recent", lambda syms, **kw: 0)
    mp.setattr(SimLedgerService, "get_open_positions", staticmethod(positions))
    mp.setattr(SimLedgerService, "equity", staticmethod(lambda *a, **kw: 2000.0))
    mp.setattr(cycles, "closing_marks", marks)
    mp.setattr(quant_tasks, "_system_watchlist", watchlist)
    return SimpleNamespace(mp=mp, built=built, synced=synced, screens=screens,
                           held=held_by_account, calls=signal_world.calls,
                           factory=signal_world.factory)


def _beat(world):
    return _beats(world.factory, "signal_cycle")[0].meta


def test_legacy_only_never_touches_the_screener_path(world, notes):
    from quant.data import corporate_actions as qactions
    from quant.data import panel as qpanel
    from quant.data import screen as qscreen

    touched = []
    world.mp.setattr(qscreen, "run_screen", _recorder(touched, "run_screen"))
    world.mp.setattr(qactions, "sync_recent", _recorder(touched, "sync_recent"))
    world.mp.setattr(qpanel, "read", _recorder(touched, "panel.read"))
    legacy = _legacy()
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(legacy))
    out = quant_tasks.signal_cycle()
    meta = _beat(world)
    assert touched == []
    assert set(meta) == P1_KEYS
    assert len(world.synced) == 1
    assert out.startswith("recs=1 ")


def test_screener_group_scans_its_pool_next_to_the_legacy_group(world, notes):
    legacy, scr = _legacy(), _screened()
    world.held[scr.account.id] = ["HELD", "EXCL"]
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(legacy, scr))
    quant_tasks.signal_cycle()

    assert world.screens == [(AS_OF, {"HELD", "EXCL"})]
    legacy_syms, screen_syms = world.built
    assert "WATCH" in legacy_syms and set(qconfig.ETF_WHITELIST) <= set(legacy_syms)
    assert screen_syms == ["AAA", "HELD", "PX1", "PX3", "PX4", "PX5"]   # pool ∪ held − exclude
    assert world.calls.store == [legacy.group.id, scr.group.id]
    # AAA (index) and HELD (held) are already in the legacy sync set
    assert world.synced[1] == ["PX1", "PX3", "PX4", "PX5"]
    meta = _beat(world)
    assert meta["pool_synced"] == 4 and meta["pool_failed"] == 0
    assert "screen" not in meta["groups"]["v1 对照"]
    assert meta["groups"]["screened"]["screen"] == {
        "as_of": "2026-07-29", "evaluated": 7, "screened": 6, "pool": 6,
        "include_ignored": [],
        "rules": [{"field": "price", "window": None, "passed": 6, "null": 0,
                   "passed_cumulative": 6}]}
    assert "fail_closed" not in meta


def test_single_screener_group_meta_is_top_level(world, notes):
    scr = _screened()
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(scr))
    quant_tasks.signal_cycle()
    meta = _beat(world)
    assert meta["screen"]["pool"] == 5 and "groups" not in meta


def test_incomplete_panel_fails_only_the_screener_group(world, notes):
    from quant.data import screen as qscreen

    def incomplete(spec, as_of, *, held=()):
        raise qscreen.PanelIncomplete(f"panel not complete for {as_of}")

    world.mp.setattr(qscreen, "run_screen", incomplete)
    legacy, scr = _legacy(), _screened()
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(legacy, scr))
    quant_tasks.signal_cycle()
    meta = _beat(world)
    assert world.calls.store == [legacy.group.id]
    assert len(world.synced) == 1                             # nothing to pool-sync
    assert ("[screened] screen: screen unavailable for 2026-07-29: panel not complete "
            "for 2026-07-29") in meta["fail_closed"]
    assert meta["groups"]["v1 对照"]["recs"] == 1
    assert world.calls.exits[-1][0] == scr.account.id        # exits still ran
    assert any("screen unavailable" in n for n in notes)


def test_pool_sync_failure_fails_closed_for_the_screener_group(world, notes):
    from quant.data import fetch as qfetch

    def flaky(symbols, *a, **kw):
        world.synced.append(list(symbols))
        if "PX1" in symbols:                                  # the pool set
            return len(symbols) - 1, ["PX3"]
        return len(symbols), []

    world.mp.setattr(qfetch, "sync_daily_many", flaky)
    legacy, scr = _legacy(), _screened()
    world.held[scr.account.id] = ["HELD"]
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(legacy, scr))
    quant_tasks.signal_cycle()
    meta = _beat(world)
    assert meta["pool_failed"] == 1 and meta["pool_synced"] == 3       # 1/4 > 20%
    assert world.calls.store == [legacy.group.id]
    assert "[screened] screen: pool bar sync failed for 1/4 symbols" in meta["fail_closed"]
    assert meta["failed"] == 0 and "fail_closed" not in meta["groups"]["v1 对照"]


def test_over_cap_pool_fails_closed(world, notes):
    world.mp.setattr(qconfig, "SCREEN_POOL_MAX", 2)
    scr = _screened()
    world.held[scr.account.id] = ["HELD"]
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(_legacy(), scr))
    quant_tasks.signal_cycle()
    meta = _beat(world)
    assert len(world.calls.store) == 1
    assert "[screened] screen: pool 6 > max 2" in meta["fail_closed"]
    assert len(world.synced) == 1                             # over-cap pool not synced


def test_paused_screener_group_runs_no_screen(world, notes):
    from quant.data import screen as qscreen

    touched = []
    world.mp.setattr(qscreen, "run_screen", _recorder(touched, "run_screen"))
    acct = _acct()
    group = group_for(acct, name="parked", status="paused", screener_json=SPEC)
    parked = GroupRuntime(group, acct, validate_group(group), screener.parse_spec(SPEC))
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(_legacy(), parked))
    quant_tasks.signal_cycle()
    assert touched == []
    assert "fail_closed" not in _beat(world)


def test_ignored_include_codes_are_reported_in_the_group_meta(world, notes):
    from dataclasses import replace as dc_replace

    from quant.data import screen as qscreen

    inner = qscreen.run_screen

    def with_ignored(spec, as_of, *, held=()):
        return dc_replace(inner(spec, as_of, held=held), include_ignored=("DELIST",))

    world.mp.setattr(qscreen, "run_screen", with_ignored)
    scr = _screened()
    world.mp.setattr(quant_tasks, "load_groups", groups_loader(_legacy(), scr))
    quant_tasks.signal_cycle()
    assert _beat(world)["groups"]["screened"]["screen"]["include_ignored"] == ["DELIST"]


@pytest.fixture
def bulk(monkeypatch, notes):
    from quant.data import universe_sync as qsync

    from tests.test_strategy_groups_tasks import _Factory

    factory = _Factory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    return SimpleNamespace(factory=factory, qsync=qsync, mp=monkeypatch, notes=notes)


def test_universe_sync_crash_is_recorded_and_announced(bulk):
    def boom(**kw):
        raise OSError("disk I/O error")

    bulk.mp.setattr(bulk.qsync, "run", boom)
    assert quant_tasks.universe_sync() == "error"
    beat = _beats(bulk.factory, "universe_sync")[0].meta
    assert beat == {"error": "OSError: disk I/O error",
                    "fail_closed": "OSError: disk I/O error"}
    assert bulk.notes == ["⛔ universe_sync failed: OSError: disk I/O error"]


def test_universe_sync_fail_closed_report_is_announced(bulk):
    report = SimpleNamespace(fail_closed="disk", session=date(2026, 7, 29),
                             summary=lambda: "session=2026-07-29 complete=False")
    bulk.mp.setattr(bulk.qsync, "run", lambda **kw: report)
    assert quant_tasks.universe_sync().startswith("session=2026-07-29")
    assert bulk.notes == ["⛔ universe_sync fail-closed: disk (session 2026-07-29)"]


def test_pool_failures_count_as_a_data_failure():
    assert cycles.signal_cycle_data_failure(
        {"fail_closed": "x", "pool_failed": 3}) == "3 pool symbols failed bar sync"
    assert cycles.signal_cycle_data_failure({"fail_closed": "x", "pool_failed": 0}) is None


def test_universe_sync_soft_time_limit_is_recorded_and_announced(bulk):
    from celery.exceptions import SoftTimeLimitExceeded

    def slow(**kw):
        raise SoftTimeLimitExceeded()

    bulk.mp.setattr(bulk.qsync, "run", slow)
    assert quant_tasks.universe_sync() == "error"
    beat = _beats(bulk.factory, "universe_sync")[0].meta
    assert beat == {"error": "soft time limit", "fail_closed": "soft time limit"}
    assert bulk.notes == ["⛔ universe_sync failed: soft time limit"]
