"""signal_cycle catch-up (2026-09-21) — the decision is a pure function, so it
is tested as a table plus a slot-by-slot replay of the 2026-09-18 incident, and
the task around it is tested for the three things that can only fail in wiring:
the attempt claim, the session guard, and the absolute expiry."""

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, delete, pool
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.modules.simledger import cycles
from app.modules.simledger.models import HeartbeatRecord
from app.tasks import quant_tasks

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 18)
SCHEDULED = datetime(2026, 9, 18, 21, 30, tzinfo=timezone.utc)


def _utc(day, hh, mm=0, ss=0):
    return datetime(2026, 9, day, hh, mm, ss, tzinfo=timezone.utc)


def _decide(now, *, finished=None, started=None, started_at=None, retry=None,
            session=SESSION):
    return cycles.signal_cycle_retry_decision(
        now_utc=now, now_et=now.astimezone(ET), session_date=session,
        scheduled_at=SCHEDULED, finished_meta=finished, started_meta=started,
        started_at=started_at, retry_meta=retry)


def _meta(seq=0, **kw):
    return {"session": str(SESSION), "seq": seq, **kw}


_LANDED_CLEAN = _meta(recs=7, scanned=106, failed=0, stale=0)
_LANDED_SYNC_FAILURE = _meta(recs=0, scanned=106, failed=507, stale=0,
                             fail_closed="bar sync failures")
_LANDED_EMPTY_FUNNEL = _meta(recs=0, scanned=106, failed=0, stale=1,
                             fail_closed="0 recommendations published from 106 "
                                         "scanned (1 stale-bar, 0 unadjusted)")
_LANDED_STALE_BARS = _meta(recs=0, scanned=100, failed=0, stale=40,
                           fail_closed="0 recommendations published from 100 scanned")


# --- the decision table -----------------------------------------------------

def test_a_clean_landing_is_never_retried():
    d = _decide(_utc(19, 0, 0), finished=_LANDED_CLEAN)
    assert d.dispatch is False and "good data" in d.reason


def test_a_data_failure_is_retried():
    d = _decide(_utc(19, 0, 0), finished=_LANDED_SYNC_FAILURE)
    assert (d.dispatch, d.seq) == (True, 1)
    assert "507 symbols failed bar sync" in d.reason


def test_stale_bars_past_the_threshold_are_a_data_failure():
    d = _decide(_utc(19, 0, 0), finished=_LANDED_STALE_BARS)
    assert (d.dispatch, d.seq) == (True, 1)


def test_an_empty_funnel_is_an_answer_not_a_fault():
    """0 recommendations with every bar read fine is tonight's real answer.
    Six catch-ups would sync the whole index again to reach the same one."""
    d = _decide(_utc(19, 0, 0), finished=_LANDED_EMPTY_FUNNEL)
    assert d.dispatch is False


def test_a_cycle_that_never_ran_is_retried_once_its_message_has_expired():
    """The beat publishes signal_cycle with expires=3300, so a message still
    unstarted at 55 minutes will never run at all."""
    assert _decide(_utc(18, 22, 30)).dispatch is False          # 60 min queued
    late = _decide(_utc(18, 23, 0))                             # 90 min queued
    assert (late.dispatch, late.seq) == (True, 1)
    assert "never started" in late.reason


def test_a_started_attempt_is_left_alone_for_longer_than_it_has_ever_run():
    """09-18's body ran 121 minutes with celery's time limit never firing, so
    'started two hours ago' is not evidence of death."""
    started_at = _utc(18, 21, 30, 1)
    for slot in (_utc(18, 22, 30), _utc(18, 23, 30), _utc(19, 0, 0)):
        alive = _decide(slot, started=_meta(0), started_at=started_at)
        assert alive.dispatch is False and "still running" in alive.reason
    dead = _decide(_utc(19, 0, 30), started=_meta(0), started_at=started_at)
    assert (dead.dispatch, dead.seq) == (True, 1)


def test_a_claim_handed_back_without_landing_is_dead_on_the_spot():
    """The body's finally releases the mutex; if no completion heartbeat came
    with it, the attempt is over and it failed. Sitting out STARTED_DEAD_AFTER
    on first-hand evidence of death would cost the night 4 of its 6 attempts:
    a 21:32 crash would wait until 00:00 UTC for seq1."""
    started_at = _utc(18, 21, 30, 1)
    crashed = _decide(_utc(18, 22, 0), started={**_meta(0), "done": True},
                      started_at=started_at)
    assert (crashed.dispatch, crashed.seq) == (True, 1)
    assert "released its claim" in crashed.reason
    # the control: same instant, claim still held -> still running
    alive = _decide(_utc(18, 22, 0), started={**_meta(0), "done": False},
                    started_at=started_at)
    assert alive.dispatch is False and "still running" in alive.reason


def test_a_landed_attempt_is_judged_on_its_data_not_on_the_claim():
    """done is also true after a clean run — the completion heartbeat wins."""
    d = _decide(_utc(19, 0, 0), finished=_LANDED_CLEAN,
                started={**_meta(0), "done": True}, started_at=_utc(18, 21, 30))
    assert d.dispatch is False and "good data" in d.reason


def test_positions_left_without_this_sessions_close_are_a_data_failure():
    """The recommendation scan and the held-position marks are independent: one
    lot on a vendor lag costs the night its snapshot while failed/stale stay at
    zero. Without this the point is lost for good — nothing else re-runs."""
    for key in ("marks_raw_or_missing", "marks_stale_close"):
        meta = _meta(0, recs=7, scanned=106, failed=0, stale=0,
                     fail_closed="equity untrusted — 1 position(s)", **{key: ["CRWD"]})
        d = _decide(_utc(19, 0, 0), finished=meta)
        assert (d.dispatch, d.seq) == (True, 1), key
        assert "without this session's close" in d.reason


def test_an_unlanded_retry_is_in_flight_whatever_the_clock_says():
    """The in-flight test is a STATE comparison: attempt 1 was dispatched and
    the newest completion heartbeat is still attempt 0's."""
    d = _decide(_utc(19, 0, 20), finished=_LANDED_SYNC_FAILURE,
                retry={"session": str(SESSION), "seq": 1,
                       "dispatched_at": _utc(19, 0, 0).isoformat()})
    assert d.dispatch is False and "not started yet" in d.reason


def test_a_landed_retry_that_failed_again_steps_to_the_next_seq():
    d = _decide(_utc(19, 1, 0), finished=_meta(1, scanned=106, failed=507,
                                               fail_closed="bar sync failures"),
                retry={"session": str(SESSION), "seq": 1,
                       "dispatched_at": _utc(19, 0, 0).isoformat()})
    assert (d.dispatch, d.seq) == (True, 2)


def test_attempts_are_capped():
    d = _decide(_utc(19, 3, 0), finished=_meta(6, scanned=106, failed=507,
                                               fail_closed="bar sync failures"),
                retry={"session": str(SESSION), "seq": 6,
                       "dispatched_at": _utc(19, 2, 0).isoformat()})
    assert d.dispatch is False and d.exhausted is True
    assert "exhausted after 6 attempts" in d.reason


def test_a_stale_retry_record_from_another_session_is_ignored():
    d = _decide(_utc(19, 0, 0), finished=_LANDED_SYNC_FAILURE,
                retry={"session": "2026-09-17", "seq": 4,
                       "dispatched_at": _utc(18, 2, 0).isoformat()})
    assert (d.dispatch, d.seq) == (True, 1)


def test_nothing_is_dispatched_before_the_scheduled_run():
    assert _decide(_utc(18, 21, 0)).dispatch is False


def test_nothing_is_dispatched_once_the_et_date_is_about_to_roll():
    # 04:30 UTC = 00:30 ET the next day: signal_cycle would anchor on 09-19
    assert _decide(_utc(19, 4, 30)).dispatch is False


def test_nothing_is_dispatched_on_a_non_session():
    d = _decide(_utc(19, 0, 0), session=date(2026, 9, 19))   # Saturday
    assert d.dispatch is False and d.reason == "not a session"


# --- the 2026-09-18 incident, slot by slot ----------------------------------

def test_the_2026_09_18_replay():
    """Every 30-min slot from the first one after the scheduled run to the last
    before the ET date rolls, on what the celery log actually shows: the body
    entered ForkPoolWorker-2 at 21:30:01 and ran there for 121 minutes (no
    TimeLimitExceeded, no SIGKILL), landing at 23:31:23 with 507/106 symbols
    failing bar sync. So `started` exists from the very first slot — the catch-up
    must sit on its hands until the cycle lands, or it dispatches a second body
    alongside a live one."""
    started_at = _utc(18, 21, 30, 1)
    landed_at = _utc(18, 23, 31, 23)
    retry = None
    seen = []
    for slot in [_utc(18, 22, 0), _utc(18, 22, 30), _utc(18, 23, 0),
                 _utc(18, 23, 30), _utc(19, 0, 0), _utc(19, 0, 30),
                 _utc(19, 1, 0), _utc(19, 1, 30), _utc(19, 2, 0),
                 _utc(19, 2, 30), _utc(19, 3, 0), _utc(19, 3, 30)]:
        finished = _LANDED_SYNC_FAILURE if slot >= landed_at else None
        d = _decide(slot, finished=finished, started=_meta(0),
                    started_at=started_at, retry=retry)
        seen.append((slot.strftime("%H:%M"), d.dispatch, d.seq))
        if d.dispatch:
            retry = {"session": str(SESSION), "seq": d.seq,
                     "dispatched_at": slot.isoformat()}
    assert seen == [
        ("22:00", False, None),      # body running 30 min
        ("22:30", False, None),      # 60 min — the old 45-min rule fired HERE
        ("23:00", False, None),      # 90 min
        ("23:30", False, None),      # 120 min, one minute before it landed
        ("00:00", True, 1),          # landed, 507 failed bar syncs -> catch up
        ("00:30", False, None),      # seq1 dispatched, not started
        ("01:00", False, None),      # 60 min queued
        ("01:30", True, 2),          # 90 min: past the message's own expiry
        ("02:00", False, None),
        ("02:30", False, None),
        ("03:00", True, 3),
        ("03:30", False, None),
    ]
    assert [row for row in seen if row[1]] == [("00:00", True, 1),
                                               ("01:30", True, 2),
                                               ("03:00", True, 3)]


# --- the task around it -----------------------------------------------------

class _Row:
    def __init__(self, name, meta=None, last_beat_at=None):
        self.name = name
        self.meta = meta
        self.last_beat_at = last_beat_at


class _Db:
    """Serves the retry task's three heartbeat reads, in the order it makes
    them: signal_cycle, signal_cycle_started, signal_cycle_retry."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        v = self._rows.pop(0) if self._rows else None

        class _R:
            def scalar_one_or_none(self):
                return v
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _factory(db):
    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False
    return lambda: _Ctx()


def _wire(monkeypatch, db, *, now=_utc(19, 0, 0)):
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _factory(db))
    monkeypatch.setattr(quant_tasks, "_now_et", lambda: now.astimezone(ET))
    sent = []

    class _Stub:
        def apply_async(self, **kw):
            sent.append(kw)

    monkeypatch.setattr(quant_tasks, "signal_cycle", _Stub())
    notes = []

    async def fake_notify(msg):
        notes.append(msg)
        return True

    monkeypatch.setattr(quant_tasks, "_notify", fake_notify)
    return sent, notes


def test_retry_task_dispatches_and_records_the_attempt(monkeypatch):
    db = _Db([_Row("signal_cycle", _LANDED_SYNC_FAILURE), None, None])
    sent, _ = _wire(monkeypatch, db)

    assert quant_tasks.signal_cycle_retry() == "dispatched: seq=1"
    assert len(sent) == 1
    assert sent[0]["kwargs"] == {"expect_session": "2026-09-18", "retry_seq": 1}
    assert sent[0]["expires"].astimezone(ET).date() == SESSION
    row = [o for o in db.added if isinstance(o, HeartbeatRecord)][0]
    assert row.name == "signal_cycle_retry"
    assert row.meta["session"] == "2026-09-18" and row.meta["seq"] == 1
    # recorded BEFORE the dispatch, or the next slot sends a second copy
    assert db.commits == 1


def test_retry_task_does_nothing_when_the_cycle_landed(monkeypatch):
    db = _Db([_Row("signal_cycle", _LANDED_CLEAN), None, None])
    sent, notes = _wire(monkeypatch, db)

    assert quant_tasks.signal_cycle_retry().startswith("skipped:")
    assert sent == [] and notes == [] and db.added == []


def test_retry_task_alerts_once_when_the_attempts_run_out(monkeypatch):
    finished = _Row("signal_cycle", dict(_meta(6, scanned=106, failed=507,
                                               fail_closed="bar sync failures")))
    retry = _Row("signal_cycle_retry",
                 {"session": str(SESSION), "seq": 6,
                  "dispatched_at": _utc(19, 2, 0).isoformat()},
                 last_beat_at=_utc(19, 2, 0))
    db = _Db([finished, None, retry])
    sent, notes = _wire(monkeypatch, db, now=_utc(19, 3, 0))

    assert quant_tasks.signal_cycle_retry().startswith("skipped: exhausted")
    assert sent == []
    assert len(notes) == 1 and "retry exhausted for 2026-09-18" in notes[0]
    assert "bar sync failures" in finished.meta["fail_closed"]
    assert "retry exhausted" in finished.meta["fail_closed"]
    assert retry.meta["exhausted_notified"] == "2026-09-18"

    # the next slot must not alert again
    db2 = _Db([finished, None, retry])
    sent2, notes2 = _wire(monkeypatch, db2, now=_utc(19, 3, 30))
    quant_tasks.signal_cycle_retry()
    assert sent2 == [] and notes2 == []


def test_retry_expiry_is_absolute_and_lands_before_et_midnight():
    """A relative expires would kill a backlogged catch-up that is still worth
    running; the deadline that matters is ET midnight, when signal_cycle's own
    session anchor moves."""
    for session, utc_hour in ((date(2026, 9, 18), 3),      # EDT
                              (date(2026, 12, 18), 4)):    # EST
        exp = quant_tasks._retry_expires(session)
        assert exp.hour == utc_hour and exp.minute == 45
        et = exp.astimezone(ET)
        assert et.date() == session and (et.hour, et.minute) == (23, 45)


# --- signal_cycle's own guards ----------------------------------------------

class _ClaimDb:
    def __init__(self, row):
        self._row = row
        self.stmt = None
        self.commits = 0

    async def execute(self, stmt):
        self.stmt = stmt
        row = self._row

        class _R:
            def first(self):
                return row
        return _R()

    async def commit(self):
        self.commits += 1


def test_claim_is_a_conditional_upsert_on_the_heartbeat_name(monkeypatch):
    db = _ClaimDb(("id",))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _factory(db))
    assert asyncio.run(quant_tasks._claim_signal_cycle("2026-09-18", 0)) is True
    sql = str(db.stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (name) DO UPDATE" in sql
    assert "IS DISTINCT FROM" in sql and "RETURNING" in sql
    assert db.commits == 1


# --- the mutex itself, against real PostgreSQL ------------------------------
# The whole invariant lives in one ON CONFLICT ... WHERE clause, so a fake DB
# can only check the SQL text. These run on the compose stack's PG inside a
# transaction that is rolled back (the pattern from test_market_data_files),
# and are skipped when no PG is reachable.

def _no_pg(reason):
    """A silent skip here is worse than a red test: the concurrency invariant
    lives entirely in SQL, so a runner without PG would report green having
    verified none of it. Opt out explicitly with CAT_NO_PG=1."""
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
    sess.execute(delete(HeartbeatRecord).where(
        HeartbeatRecord.name == "signal_cycle_started"))
    try:
        yield sess
    finally:
        sess.close()
        if trans.is_active:
            trans.rollback()          # the real table is left untouched
        conn.close()
        engine.dispose()


def _claim(pg, seq, *, at, session=str(SESSION)):
    granted = pg.execute(quant_tasks.claim_signal_cycle_stmt(
        session, seq, now=at)).first() is not None
    pg.flush()
    return granted


def _release(pg, seq, *, session=str(SESSION)):
    pg.execute(quant_tasks.release_signal_cycle_stmt(session, seq))
    pg.flush()


def test_the_first_attempt_takes_the_mutex(pg):
    assert _claim(pg, 0, at=_utc(18, 21, 30, 1)) is True


def test_a_redelivered_message_cannot_re_enter_a_running_body(pg):
    """2026-09-18 23:00:54: the broker redelivered the same task UUID while the
    body was still running. celery's revoke list is worker memory; this is a
    row."""
    assert _claim(pg, 0, at=_utc(18, 21, 30, 1)) is True
    assert _claim(pg, 0, at=_utc(18, 23, 0, 54)) is False


def test_a_catch_up_cannot_start_a_second_body_alongside_a_running_one(pg):
    """THE concurrency guard. On 09-18 the body had been running 60 minutes at
    the 22:30 slot with no way to know it was alive. Even if the dispatcher gets
    that wrong, seq1 must not take the mutex away from seq0."""
    assert _claim(pg, 0, at=_utc(18, 21, 30, 1)) is True
    assert _claim(pg, 1, at=_utc(18, 22, 30)) is False        # 60 min in
    assert _claim(pg, 1, at=_utc(18, 23, 30)) is False        # 120 min in


def test_the_next_attempt_takes_the_mutex_once_the_body_released_it(pg):
    assert _claim(pg, 0, at=_utc(18, 21, 30, 1)) is True
    _release(pg, 0)
    assert _claim(pg, 1, at=_utc(19, 0, 0)) is True


def test_a_finished_attempt_never_runs_again(pg):
    """Released is not 'free for anyone': celery redelivers on a lost ack, and
    re-running seq0 would republish a session's recommendations twice."""
    assert _claim(pg, 0, at=_utc(18, 21, 30, 1)) is True
    _release(pg, 0)
    assert _claim(pg, 0, at=_utc(19, 0, 0)) is False          # same attempt
    assert _claim(pg, 1, at=_utc(19, 0, 0)) is True
    _release(pg, 1)
    assert _claim(pg, 0, at=_utc(19, 1, 0)) is False          # older attempt


def test_a_body_that_never_released_frees_the_mutex_after_the_stale_window(pg):
    """The only escape hatch: a SIGKILLed body never runs its release. The
    window is longer than any runtime ever observed, so it costs one late catch-
    up rather than a double run."""
    started = _utc(18, 21, 30, 1)
    assert _claim(pg, 0, at=started) is True
    just_inside = started + quant_tasks.CLAIM_STALE_AFTER
    assert _claim(pg, 1, at=just_inside) is False
    assert _claim(pg, 1, at=just_inside + timedelta(minutes=1)) is True


def test_a_new_session_is_not_blocked_by_yesterdays_finished_attempt(pg):
    assert _claim(pg, 3, at=_utc(18, 21, 30, 1)) is True
    _release(pg, 3)
    assert _claim(pg, 0, at=_utc(19, 21, 30, 1),
                  session="2026-09-19") is True               # seq goes back to 0


def test_a_redelivered_attempt_does_not_run_twice(monkeypatch):
    """2026-09-18's broker redelivery of the same task UUID. celery's revoke
    list is worker memory; this claim is a row."""
    db = _ClaimDb(None)                       # the conflict WHERE matched nothing
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _factory(db))
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 9, 18, 17, 30, tzinfo=ET))
    assert quant_tasks.signal_cycle() == "skipped: duplicate attempt"


@pytest.mark.parametrize("failing", ["constituents_on", "sync_daily_many",
                                    "load_sectors", "top_liquid"])
def test_a_crash_before_the_cycle_body_still_releases_the_mutex(monkeypatch,
                                                                failing):
    """Everything between taking the mutex and handing it back must be inside
    the try/finally. These four calls are the ones that blow up when DNS is
    down — the exact failure the mutex exists for — and a leaked claim silences
    every catch-up slot until the 150-minute stale window."""
    from quant.data import corporate_actions as qactions
    from quant.data import fetch as qfetch
    from quant.data import sectors as qsectors
    from quant.data import universe as quniverse

    calls = []

    async def fake_claim(session, seq):
        calls.append("claim")
        return True

    async def fake_release(session, seq):
        calls.append("release")

    def boom(*a, **kw):
        raise RuntimeError("DNS is down (2026-09-18 shape)")

    monkeypatch.setattr(quant_tasks, "_claim_signal_cycle", fake_claim)
    monkeypatch.setattr(quant_tasks, "_release_signal_cycle", fake_release)
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _factory(_Db([])))
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 9, 18, 17, 30, tzinfo=ET))
    monkeypatch.setattr(qactions, "sync_universe", lambda *a, **kw: 0)
    monkeypatch.setattr(quniverse, "constituents_on", lambda d: ["AAA"])
    monkeypatch.setattr(quniverse, "top_liquid", lambda syms, **kw: list(syms))
    monkeypatch.setattr(qfetch, "sync_daily_many", lambda syms, *a, **kw: (1, []))
    monkeypatch.setattr(qsectors, "load_sectors", lambda: {})
    monkeypatch.setattr({"constituents_on": quniverse, "top_liquid": quniverse,
                         "sync_daily_many": qfetch,
                         "load_sectors": qsectors}[failing], failing, boom)

    assert quant_tasks.signal_cycle() == "error"     # never raises out of celery
    assert calls == ["claim", "release"]


def test_a_message_that_outlived_its_session_is_refused(monkeypatch):
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 9, 18, 17, 30, tzinfo=ET))
    out = quant_tasks.signal_cycle(expect_session="2026-09-17", retry_seq=2)
    assert out == "skipped: dispatched for 2026-09-17, now 2026-09-18"


# 2026-09-18's measured body runtime: received 21:30:01, succeeded 23:31:23,
# with no TimeLimitExceeded and no SIGKILL anywhere in between.
OBSERVED_BODY_RUNTIME = timedelta(minutes=121)


def test_the_constants_are_tied_to_what_was_measured_not_to_celerys_promise():
    """celery's task_time_limit did NOT bound the body on 09-18, so it cannot be
    the yardstick. 'Started is dead' must clear the longest run ever observed,
    and 'never started' hangs off the beat message's own expiry — past that
    celery discards it and it can never run."""
    from tasks.celery_app import celery_app
    assert cycles.STARTED_DEAD_AFTER > OBSERVED_BODY_RUNTIME
    assert quant_tasks.CLAIM_STALE_AFTER is cycles.STARTED_DEAD_AFTER
    beat_expires = celery_app.conf.beat_schedule[
        "quant-signal-cycle"]["options"]["expires"]
    assert cycles.NOT_STARTED_DEAD_AFTER > timedelta(seconds=beat_expires)
