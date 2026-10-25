"""P5b-2 intraday watch rows against the rehearsal PostgreSQL (migration 026):
``replace_watch_rows`` swaps one group's rows for a session, readers only see
active 1m groups, the table's constraints hold, and the group listings filter
by status and timeframe. Every row is written on one non-autocommit connection
installed as the registry's and rolled back at the end."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from quant.data import registry
from quant.engine.strategies import M1_ORB_KEY, V1_COMPOSITE_KEY, get_spec

SESSION = date(2026, 10, 26)
OTHER_SESSION = date(2026, 10, 27)
SCREENER = {"rules": [{"field": "market_cap", "op": ">=", "value": 100000000}]}

# name -> (status, timeframe, created_at); a_late and a_tie share created_at so
# the id breaks the tie
GROUPS = {
    "a_late": ("active", "1m", datetime(2026, 1, 3, tzinfo=timezone.utc)),
    "a_early": ("active", "1m", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    "a_tie": ("active", "1m", datetime(2026, 1, 3, tzinfo=timezone.utc)),
    "paused_1m": ("paused", "1m", datetime(2026, 1, 2, tzinfo=timezone.utc)),
    "active_1d": ("active", "1d", datetime(2026, 1, 2, tzinfo=timezone.utc)),
    "archived_1m": ("archived", "1m", datetime(2026, 1, 2, tzinfo=timezone.utc)),
}


def _seed(conn) -> SimpleNamespace:
    from psycopg.types.json import Jsonb

    tag = uuid4().hex[:8]
    user_id = conn.execute("SELECT id FROM users ORDER BY created_at, id LIMIT 1").fetchone()
    if user_id is None:
        pytest.skip("no user to own the test accounts")
    ids, names = {}, {}
    for key, (status, timeframe, created_at) in GROUPS.items():
        account_id, group_id = uuid4(), uuid4()
        name = f"ZZ-p5b2-{key}-{tag}"
        strategy_key = M1_ORB_KEY if timeframe == "1m" else V1_COMPOSITE_KEY
        conn.execute(
            "INSERT INTO sim_accounts (id, user_id, name, is_system, created_at, updated_at) "
            "VALUES (%s, %s, %s, true, %s, %s)",
            (account_id, user_id[0], name, created_at, created_at))
        conn.execute(
            "INSERT INTO strategy_groups (id, name, strategy_key, params_json, screener_json, "
            "timeframe, status, sim_account_id, account_is_system, created_at, archived_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)",
            (group_id, name, strategy_key, Jsonb(get_spec(strategy_key).default_params()),
             Jsonb(SCREENER if timeframe == "1m" else {}), timeframe, status, account_id,
             created_at, created_at if status == "archived" else None))
        ids[key], names[key] = group_id, name
    return SimpleNamespace(conn=conn, ids=ids, names=names)


@pytest.fixture()
def pg(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(registry._dsn(), connect_timeout=registry.CONNECT_TIMEOUT_S)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    monkeypatch.setattr(registry, "_conn", conn)
    try:
        yield _seed(conn)
    finally:
        conn.rollback()
        conn.close()


def _mine(pg, rows):
    ours = set(pg.ids.values())
    return [r for r in rows if r.group_id in ours]


def _stored(pg, group_id, session=SESSION) -> list[tuple]:
    return pg.conn.execute(
        "SELECT symbol, rank, score, reason FROM intraday_watch "
        "WHERE group_id = %s AND session_date = %s ORDER BY rank",
        (group_id, session)).fetchall()


def test_q1_rows_come_back_by_rank_with_the_group_name(pg):
    gid = pg.ids["a_early"]
    n = registry.replace_watch_rows(SESSION, gid, [
        ("CCC", 3, 0.3, "rank 3"), ("aaa", 1, 0.123456, "rank 1"), ("BBB", 2, None, "rank 2")])
    assert n == 3
    rows = _mine(pg, registry.intraday_watch_rows(SESSION))
    assert [(r.symbol, r.rank, r.reason, r.group_name) for r in rows] == [
        ("AAA", 1, "rank 1", pg.names["a_early"]),
        ("BBB", 2, "rank 2", pg.names["a_early"]),
        ("CCC", 3, "rank 3", pg.names["a_early"]),
    ]
    assert [r.group_id for r in rows] == [gid] * 3


def test_q2_a_second_replace_keeps_only_the_new_rows(pg):
    gid, other = pg.ids["a_early"], pg.ids["a_late"]
    registry.replace_watch_rows(SESSION, gid, [
        ("AAA", 1, 0.3, "r1"), ("BBB", 2, 0.2, "r2"), ("CCC", 3, 0.1, "r3")])
    registry.replace_watch_rows(SESSION, other, [("AAA", 1, 0.5, "other")])
    registry.replace_watch_rows(OTHER_SESSION, gid, [("ZZZ", 1, 0.5, "next day")])
    assert registry.replace_watch_rows(SESSION, gid, [
        ("BBB", 1, 0.9, "new 1"), ("DDD", 2, 0.8, "new 2")]) == 2
    assert [(s, r, reason) for s, r, _, reason in _stored(pg, gid)] == [
        ("BBB", 1, "new 1"), ("DDD", 2, "new 2")]
    assert [s for s, *_ in _stored(pg, other)] == ["AAA"]
    assert [s for s, *_ in _stored(pg, gid, OTHER_SESSION)] == ["ZZZ"]


def test_q3_paused_and_daily_group_rows_are_not_returned(pg):
    for key in ("paused_1m", "active_1d", "a_early"):
        registry.replace_watch_rows(SESSION, pg.ids[key], [(f"S{key[:3].upper()}", 1, 0.1, key)])
    rows = _mine(pg, registry.intraday_watch_rows(SESSION))
    assert [(r.group_name, r.symbol) for r in rows] == [(pg.names["a_early"], "SA_E")]
    assert len(_stored(pg, pg.ids["paused_1m"])) == 1
    assert len(_stored(pg, pg.ids["active_1d"])) == 1


def test_q4_constraints_reject_bad_rows(pg):
    from psycopg import errors

    insert = ("INSERT INTO intraday_watch (id, session_date, group_id, symbol, rank, reason) "
              "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'x')")
    gid = pg.ids["a_early"]
    with pytest.raises(errors.CheckViolation):
        with pg.conn.transaction():
            pg.conn.execute(insert, (SESSION, gid, "AAA", 0))
    with pytest.raises(errors.UniqueViolation):
        with pg.conn.transaction():
            pg.conn.execute(insert, (SESSION, gid, "AAA", 1))
            pg.conn.execute(insert, (SESSION, gid, "AAA", 2))
    with pytest.raises(errors.ForeignKeyViolation):
        with pg.conn.transaction():
            pg.conn.execute(insert, (SESSION, uuid4(), "AAA", 1))
    assert _stored(pg, gid) == []


def test_q5_active_1m_groups_only_active_1m_by_created_at_then_id(pg):
    ours = set(pg.ids.values())
    got = [g for g in registry.active_1m_groups() if g.group_id in ours]
    tied = sorted([pg.ids["a_late"], pg.ids["a_tie"]])
    assert [g.group_id for g in got] == [pg.ids["a_early"], *tied]
    first = got[0]
    assert (first.name, first.strategy_key) == (pg.names["a_early"], M1_ORB_KEY)
    assert first.params == get_spec(M1_ORB_KEY).default_params()
    assert first.screener == SCREENER


def test_q6_managed_groups_include_paused_not_archived(pg):
    ours = {v: k for k, v in pg.ids.items()}
    got = {ours[g.group_id] for g in registry.managed_groups() if g.group_id in ours}
    assert got == {"a_late", "a_early", "a_tie", "paused_1m", "active_1d"}


def test_q7_an_empty_replace_clears_the_rows_without_error(pg):
    gid = pg.ids["a_early"]
    registry.replace_watch_rows(SESSION, gid, [("AAA", 1, 0.1, "r1"), ("BBB", 2, 0.1, "r2")])
    assert registry.replace_watch_rows(SESSION, gid, []) == 0
    assert _stored(pg, gid) == []
    assert _mine(pg, registry.intraday_watch_rows(SESSION)) == []


def test_q8_a_failed_replace_keeps_the_old_rows(pg):
    """No test-side transaction around the failing call: only replace_watch_rows'
    own ``transaction()`` keeps its DELETE from surviving the failed INSERT and
    the connection usable for the read that follows."""
    from psycopg import errors

    gid = pg.ids["a_early"]
    registry.replace_watch_rows(SESSION, gid, [
        ("AAA", 1, 0.3, "r1"), ("BBB", 2, 0.2, "r2"), ("CCC", 3, 0.1, "r3")])
    with pytest.raises(errors.CheckViolation):
        registry.replace_watch_rows(SESSION, gid, [("DDD", 1, 0.9, "n1"), ("EEE", 0, 0.8, "n0")])
    assert [(s, r) for s, r, *_ in _stored(pg, gid)] == [("AAA", 1), ("BBB", 2), ("CCC", 3)]
