"""P2 nightly universe sync: session choice, the flock, the disk guard, the
per-night fresh cap, the completeness rule, and --init resuming stage by stage."""

import copy
import fcntl
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quant import config
from quant.data import universe_sync as us

AFTER_CLOSE = datetime(2026, 10, 16, 22, 15, tzinfo=timezone.utc)   # Fri 18:15 ET


def test_last_completed_session():
    assert us.last_completed_session(AFTER_CLOSE) == date(2026, 10, 16)
    assert us.last_completed_session(
        datetime(2026, 10, 16, 20, 19, tzinfo=timezone.utc)) == date(2026, 10, 15)
    assert us.last_completed_session(
        datetime(2026, 10, 17, 2, 15, tzinfo=timezone.utc)) == date(2026, 10, 16)
    assert us.last_completed_session(
        datetime(2026, 10, 19, 10, 15, tzinfo=timezone.utc)) == date(2026, 10, 16)


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "LOCK_PATH", tmp_path / "_locks" / "universe_sync.lock")
    monkeypatch.setattr(config, "ACTIONS_DB", tmp_path / "manifest.db")
    calls = SimpleNamespace(beats=[], backfill=[], many=[], panel=[], edgar=[],
                            sectors=0, universe=[], recent=[])
    codes = [f"C{i:03d}" for i in range(20)]
    stored = {"C000", "C001", "C002", "C003", "C004"}          # 15 fresh codes

    monkeypatch.setattr(us, "_free_gb", lambda: 500.0)
    monkeypatch.setattr(us.panel, "is_complete", lambda s: False)
    monkeypatch.setattr(us.securities, "refresh", lambda today: {"active": 20})
    monkeypatch.setattr(us.securities, "eligible_codes", lambda *a: list(codes))
    monkeypatch.setattr(us.securities, "ciks_to_check", lambda s, n: [11, 22])
    monkeypatch.setattr(us.corporate_actions, "load_synced_at",
                        lambda c: None if c in ("C010", "C011") else date(2026, 10, 15))
    monkeypatch.setattr(us.corporate_actions, "sync_universe",
                        lambda syms, **kw: calls.universe.append(list(syms)))
    monkeypatch.setattr(us.corporate_actions, "sync_recent",
                        lambda syms, **kw: calls.recent.append(list(syms)))
    monkeypatch.setattr(us.registry, "max_last_ts",
                        lambda c, tf: AFTER_CLOSE if c in stored else None)
    # copied at call time, like the real beat that serializes immediately
    monkeypatch.setattr(us.registry, "beat",
                        lambda name, meta: calls.beats.append(copy.deepcopy(meta)))

    no_tape = set()

    def backfill(batch, **kw):
        calls.backfill.append((list(batch), kw))
        empty = [c for c in batch if c in no_tape]
        stored.update(c for c in batch if c not in no_tape)
        return SimpleNamespace(stored=len(batch) - len(empty), empty=empty)

    monkeypatch.setattr(us.backfill, "run_daily_backfill", backfill)
    monkeypatch.setattr(us.fetch, "sync_daily_many",
                        lambda syms, now=None: calls.many.append(list(syms))
                        or (len(syms), []))
    monkeypatch.setattr(us.panel, "update",
                        lambda s, c, complete: calls.panel.append((s, complete)) or {})
    monkeypatch.setattr(us.edgar, "refresh_ciks",
                        lambda ciks, s: calls.edgar.append(list(ciks)) or {"ciks": 2})
    monkeypatch.setattr(us, "_screener_groups", lambda: [])
    return SimpleNamespace(calls=calls, codes=codes, stored=stored, no_tape=no_tape,
                           monkeypatch=monkeypatch)


def test_full_pass(world):
    report = us.run(now=AFTER_CLOSE, fresh_cap=10)
    c = world.calls
    assert report.session == date(2026, 10, 16) and report.complete is False
    assert c.universe == [["C010", "C011"]]                 # never synced: full window
    assert len(c.recent[0]) == 18
    assert c.backfill[0][0] == [f"C{i:03d}" for i in range(5, 15)]   # cap 10 of 15
    assert c.backfill[0][1]["batch_size"] == 50 and c.backfill[0][1]["pause_s"] == 3.0
    assert c.many == [["C000", "C001", "C002", "C003", "C004"]]   # current ones only
    assert c.edgar == [[11, 22]]
    beat = c.beats[0]
    assert beat["bars"] == {"fresh": 15, "fresh_stored": 10, "fresh_pending": 5,
                            "dormant": 0, "current": 5, "synced": 5, "failed": 0}
    # 5 pending > 5% of 20 eligible -> incomplete, screen stays fail-closed
    assert beat["complete"] is False and c.panel == [(date(2026, 10, 16), False)]
    assert "5 new listings pending" in beat["fail_closed"]


def test_complete_when_everything_landed(world):
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert report.complete is True and report.fail_closed is None
    assert world.calls.panel == [(date(2026, 10, 16), True)]


@pytest.mark.parametrize("bars, n, expected", [
    ({"failed": 20, "current": 100, "fresh_pending": 0}, 100, True),
    ({"failed": 21, "current": 100, "fresh_pending": 0}, 100, False),
    ({"failed": 0, "current": 100, "fresh_pending": 5}, 100, True),
    ({"failed": 0, "current": 100, "fresh_pending": 6}, 100, False),
])
def test_completeness_rule(bars, n, expected):
    assert us.is_complete(bars, n) is expected


def test_busy_lock_skips(world):
    us.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(us.LOCK_PATH, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = us.run(now=AFTER_CLOSE)
    assert report.skipped == "busy"
    assert world.calls.beats == [] and world.calls.many == []


def test_disk_guard_fails_closed_before_any_work(world):
    world.monkeypatch.setattr(us, "_free_gb", lambda: config.MIN_FREE_DISK_GB - 0.1)
    report = us.run(now=AFTER_CLOSE)
    assert report.fail_closed == "disk"
    assert world.calls.beats == [{"session": "2026-10-16", "fail_closed": "disk"}]
    assert world.calls.many == [] and world.calls.panel == []


def test_a_completed_session_is_a_no_op(world):
    world.monkeypatch.setattr(us.panel, "is_complete", lambda s: True)
    report = us.run(now=AFTER_CLOSE)
    assert report.skipped == "done" and world.calls.beats == []


def test_securities_failure_keeps_the_old_universe_and_is_loud(world):
    def broken(today):
        raise RuntimeError("symbol directory otherlisted: unexpected header")

    world.monkeypatch.setattr(us.securities, "refresh", broken)
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert "securities refresh failed" in report.fail_closed
    assert world.calls.many                                     # rest still ran


def test_init_stages_resume(world, capsys):
    synced = set()
    checked = set()
    world.monkeypatch.setattr(us.corporate_actions, "load_synced_at",
                              lambda c: date(2026, 10, 15) if c in synced else None)
    world.monkeypatch.setattr(us.corporate_actions, "sync_universe",
                              lambda syms, **kw: (world.calls.universe.append(list(syms)),
                                                  synced.update(syms)))

    def backfill(batch, **kw):
        fresh = [c for c in batch if c not in world.stored]
        world.calls.backfill.append(fresh)
        world.stored.update(fresh)
        return SimpleNamespace(total=len(batch), fresh=len(fresh), stale=0,
                               current=len(batch) - len(fresh), stored=len(fresh),
                               empty=[])

    world.monkeypatch.setattr(us.backfill, "run_daily_backfill", backfill)
    world.monkeypatch.setattr(us.securities, "unchecked_ciks",
                              lambda: [k for k in (1, 2, 3) if k not in checked])
    world.monkeypatch.setattr(us.edgar, "refresh_ciks",
                              lambda ciks, today, progress=None: (
                                  checked.update(ciks), {"ciks": len(ciks)})[1])
    years = []
    world.monkeypatch.setattr(us.panel, "write_years",
                              lambda codes, ys: years.append(list(ys)) or {"rows": 1})

    us.run_init(now=AFTER_CLOSE)
    us.run_init(now=AFTER_CLOSE)
    out = capsys.readouterr().out
    assert [ln.split()[1] for ln in out.splitlines() if ln.startswith("STAGE")] == \
        ["1", "2", "3", "4", "5"] * 2
    assert world.calls.universe == [world.codes]                # second run: nothing left
    assert world.calls.backfill == [world.codes[5:], []]
    assert '"ciks": 3' in out and '"ciks": 0' in out
    assert years[0] == list(range(2016, 2027))


def _night(day: int) -> datetime:
    return datetime(2026, 10, day, 22, 15, tzinfo=timezone.utc)


def _bars(world):
    return world.calls.beats[-1]["bars"]


def test_three_empty_sessions_make_a_code_dormant_and_out_of_completeness(world):
    no_tape = [f"C{i:03d}" for i in range(10, 20)]
    world.no_tape.update(no_tape)
    for day in (19, 20):
        assert us.run(now=_night(day)).complete is False
    us.run(now=datetime(2026, 10, 21, 2, 15, tzinfo=timezone.utc))   # same session 10-20
    assert us.load_fresh_empty(no_tape)["C010"] == (2, date(2026, 10, 20))
    report = us.run(now=_night(21))
    assert _bars(world)["dormant"] == 10 and _bars(world)["fresh_pending"] == 0
    assert report.complete is True                       # 10 dormant out of both sides
    world.calls.backfill.clear()
    us.run(now=_night(22))
    assert world.calls.backfill == []                    # dormant codes left the queue
    assert _bars(world)["dormant"] == 10


def test_dormant_codes_retry_every_seven_days_and_tape_clears_them(world):
    world.no_tape.update({"C010", "C011"})
    for day in (19, 20, 21):
        us.run(now=_night(day))
    world.calls.backfill.clear()
    us.run(now=_night(27))                               # 6 days after 10-21
    assert world.calls.backfill == []
    world.no_tape.discard("C010")                        # C010 starts trading
    us.run(now=_night(28))                               # 7 days: retried
    assert world.calls.backfill[0][0] == ["C010", "C011"]
    state = us.load_fresh_empty(["C010", "C011"])
    assert "C010" not in state
    assert state["C011"] == (4, date(2026, 10, 28))      # still dormant, next retry in 7d
    assert _bars(world)["dormant"] == 1


def test_a_non_empty_result_resets_the_counter(world):
    world.no_tape.add("C010")
    us.run(now=_night(19))
    us.run(now=_night(20))
    world.no_tape.discard("C010")
    world.monkeypatch.setattr(us.registry, "max_last_ts", lambda c, tf: None)
    us.run(now=_night(21))
    assert us.load_fresh_empty(["C010"]) == {}


def test_completeness_ignores_dormant_codes():
    bars = {"failed": 0, "current": 100, "fresh_pending": 5, "dormant": 900}
    assert us.is_complete(bars, 1100) is True            # 5 <= 5% of 200
    assert us.is_complete({**bars, "fresh_pending": 11}, 1100) is False


def test_ca_window_reaches_back_over_an_outage(world):
    world.monkeypatch.setattr(us.corporate_actions, "sync_recent",
                              lambda syms, days=None: world.calls.recent.append(days))
    us.run(now=_night(19))
    assert world.calls.recent[-1] == config.CA_RECENT_DAYS          # first night
    assert us.load_ca_last_success() == date(2026, 10, 19)
    us.record_ca_success(date(2026, 9, 4))                           # 45-day outage
    us.run(now=_night(20))
    assert world.calls.recent[-1] == 46 + 5
    assert world.calls.beats[-1]["actions"]["recent_days"] == 51
    us.run(now=_night(21))
    assert world.calls.recent[-1] == config.CA_RECENT_DAYS          # caught up


def test_a_failed_ca_sync_does_not_advance_the_watermark(world):
    def down(syms, days=None):
        raise RuntimeError("alpaca 503")

    world.monkeypatch.setattr(us.corporate_actions, "sync_recent", down)
    us.record_ca_success(date(2026, 9, 4))
    report = us.run(now=_night(19))
    assert "corporate action sync failed" in report.fail_closed
    assert us.load_ca_last_success() == date(2026, 9, 4)


def test_init_records_the_ca_watermark(world, capsys):
    world.monkeypatch.setattr(us.backfill, "run_daily_backfill",
                              lambda batch, **kw: SimpleNamespace(
                                  total=len(batch), fresh=0, stale=0, current=len(batch),
                                  stored=0, empty=[]))
    world.monkeypatch.setattr(us.securities, "unchecked_ciks", lambda: [])
    world.monkeypatch.setattr(us.edgar, "refresh_ciks",
                              lambda ciks, today, progress=None: {"ciks": 0})
    world.monkeypatch.setattr(us.panel, "write_years", lambda codes, ys: {"rows": 0})
    assert us.load_ca_last_success() is None
    us.run_init(now=AFTER_CLOSE)
    assert us.load_ca_last_success() == date(2026, 10, 16)


def test_empty_universe_fails_closed_without_touching_the_panel(world):
    world.monkeypatch.setattr(us.securities, "eligible_codes", lambda *a: [])
    report = us.run(now=AFTER_CLOSE)
    assert report.complete is False and report.fail_closed == "empty universe"
    assert world.calls.panel == [] and world.calls.many == [] and world.calls.backfill == []
    assert world.calls.beats[-1]["fail_closed"] == "empty universe"


class _TimeLimit(Exception):
    pass


@pytest.mark.parametrize("where", ["securities", "actions", "edgar", "sectors",
                                   "group_screen"])
def test_a_soft_time_limit_is_never_swallowed(world, where):
    from quant.data import screen as qscreen

    world.monkeypatch.setattr(us.edgar, "TIME_LIMIT_ERRORS", (_TimeLimit,))

    def limit(*a, **kw):
        raise _TimeLimit()

    world.monkeypatch.setattr(us, "_screener_groups", lambda: [
        ("screened", {"rules": [{"field": "price", "op": ">=", "value": 5}]})])
    target = {"securities": (us.securities, "refresh"),
              "actions": (us.corporate_actions, "sync_recent"),
              "edgar": (us.edgar, "refresh_ciks"),
              "sectors": (us, "_sync_pool_sectors"),
              "group_screen": (qscreen, "run_screen")}[where]
    world.monkeypatch.setattr(*target, limit)
    with pytest.raises(_TimeLimit):
        us.run(now=AFTER_CLOSE)
    assert world.calls.beats == []



class _Clock:
    """Advances by ``step`` seconds per reading, so the Nth stage check sees
    N x step elapsed."""

    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _hit_at(k: int) -> _Clock:
    """A clock whose k-th stage check (actions=1, bars=2, panel=3, edgar=4,
    sectors=5) is the first one at or past the deadline."""
    return _Clock(config.UNIVERSE_SYNC_DEADLINE_S / (k - 0.5))


def test_deadline_before_bars_leaves_the_session_open(world):
    world.monkeypatch.setattr(us, "_clock", _hit_at(2))
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert report.fail_closed == "deadline" and report.complete is False
    assert world.calls.beats[-1]["deadline_stage"] == "bars"
    assert world.calls.many == [] and world.calls.backfill == [] and world.calls.panel == []
    assert world.calls.recent                                  # actions did run


def test_deadline_before_edgar_keeps_the_panel_but_says_deadline(world):
    world.monkeypatch.setattr(us, "_clock", _hit_at(4))
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert world.calls.panel == [(date(2026, 10, 16), True)]   # existing rules
    assert world.calls.edgar == []
    assert report.fail_closed == "deadline"
    assert world.calls.beats[-1]["deadline_stage"] == "edgar"


def test_deadline_before_panel_does_not_mark_complete(world):
    world.monkeypatch.setattr(us, "_clock", _hit_at(3))
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert world.calls.many and world.calls.panel == []
    assert report.complete is False and world.calls.beats[-1]["complete"] is False
    assert world.calls.beats[-1]["deadline_stage"] == "panel"


def test_deadline_keeps_earlier_issues(world):
    world.monkeypatch.setattr(us, "_clock", _hit_at(2))

    def broken(today):
        raise RuntimeError("symdir header changed")

    world.monkeypatch.setattr(us.securities, "refresh", broken)
    report = us.run(now=AFTER_CLOSE)
    assert report.fail_closed.startswith("securities refresh failed")
    assert report.fail_closed.endswith("; deadline")


def test_no_deadline_by_default(world):
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert "deadline_stage" not in world.calls.beats[-1] and report.fail_closed is None


def test_deadline_before_sectors_skips_them(world):
    ran = []
    world.monkeypatch.setattr(us, "_sync_pool_sectors", lambda s: ran.append(s) or {})
    world.monkeypatch.setattr(us, "_clock", _hit_at(5))
    report = us.run(now=AFTER_CLOSE, fresh_cap=300)
    assert world.calls.edgar == [[11, 22]] and ran == []
    assert report.fail_closed == "deadline"
    assert world.calls.beats[-1]["deadline_stage"] == "sectors"


def test_the_deadline_is_ninety_minutes():
    assert config.UNIVERSE_SYNC_DEADLINE_S == 5400
