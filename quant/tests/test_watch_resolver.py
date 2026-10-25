"""P5b watch resolver — the five-tier subscription set (held 1m > 1m shortlist >
held 1d > configured > owners' watchlists > extra), the round-robin across 1m
groups, the cap, the watched session's roll at the extended close, and the
pre-migration fallback of ``intraday_watch_rows``. Fake registry except W16,
which reads the rehearsal PostgreSQL."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant.data import registry
from quant.data.registry import ManagedGroup, StreamSymbol, WatchRow

ET = "America/New_York"
SESSION = date(2026, 10, 26)
NOW = pd.Timestamp("2026-10-26 10:00", tz=ET).tz_convert("UTC").to_pydatetime()


def _et(text: str):
    return pd.Timestamp(text, tz=ET).tz_convert("UTC").to_pydatetime()


def _group(name, timeframe="1d", status="active", account=None, user="u1"):
    return ManagedGroup(name, name, timeframe, status, account or f"acct-{name}", user, None)


def _rows(group, symbols):
    return [WatchRow(group, group, s, i + 1, f"rank {i + 1}") for i, s in enumerate(symbols)]


class WatchSource:
    def __init__(self, *, groups=(), positions=None, rows=None, configured=(),
                 watchlists=None):
        self.groups = list(groups)
        self.positions = positions or {}
        self.rows = rows or {}
        self.configured = list(configured)
        self.watchlists = watchlists or {}
        self.watchlist_calls: list = []
        self.sessions: list = []

    def managed_groups(self):
        return list(self.groups)

    def open_position_symbols(self, account_id):
        return list(self.positions.get(account_id, []))

    def intraday_watch_rows(self, session):
        self.sessions.append(session)
        return list(self.rows.get(session, []))

    def stream_symbols(self):
        return [StreamSymbol(symbol=s, priority=10 * (i + 1), note=None)
                for i, s in enumerate(self.configured)]

    def watchlist_symbols(self, user_id):
        self.watchlist_calls.append(user_id)
        return list(self.watchlists.get(user_id, []))


def _symbols(entries):
    return [e.symbol for e in entries]


def test_w1_single_daily_group_keeps_the_old_order():
    src = WatchSource(groups=[_group("v1")], positions={"acct-v1": ["held", "SHARED"]},
                      configured=["SHARED", "CFG"], watchlists={"u1": ["WL", "HELD"]})
    kept, dropped = registry.resolve_watch(src, now=NOW)
    assert _symbols(kept) == ["HELD", "SHARED", "CFG", "WL"]
    assert dropped == []


def test_w2_all_tiers_in_order():
    src = WatchSource(
        groups=[_group("orb", "1m"), _group("v1")],
        positions={"acct-orb": ["H1M"], "acct-v1": ["H1D"]},
        rows={SESSION: _rows("orb", ["SL"])},
        configured=["CFG"], watchlists={"u1": ["WL"]})
    kept, _ = registry.resolve_watch(src, now=NOW, extra=["EXT"])
    assert [(e.symbol, e.tier) for e in kept] == [
        ("H1M", "held_1m"), ("SL", "shortlist_1m"), ("H1D", "held_1d"),
        ("CFG", "configured"), ("WL", "watchlist"), ("EXT", "extra")]
    assert [e.reason for e in kept] == [
        "open position", "rank 1", "open position",
        "market_stream_symbols priority 10", "owner watchlist", "stored file"]


def test_w3_shortlists_round_robin_across_groups():
    src = WatchSource(groups=[_group("g1", "1m"), _group("g2", "1m")],
                      rows={SESSION: _rows("g1", ["A", "B", "C"]) + _rows("g2", ["X", "Y"])})
    kept, _ = registry.resolve_watch(src, now=NOW)
    assert _symbols(kept) == ["A", "X", "B", "Y", "C"]


def test_w4_first_tier_wins_and_groups_merge():
    src = WatchSource(groups=[_group("orb", "1m"), _group("v1")],
                      positions={"acct-v1": ["DUP"]},
                      rows={SESSION: _rows("orb", ["DUP"])})
    kept, _ = registry.resolve_watch(src, now=NOW)
    assert len(kept) == 1
    assert kept[0].tier == "shortlist_1m"
    assert kept[0].groups == ("orb", "v1")


def test_w5_cap_truncates_and_dropped_keeps_order():
    src = WatchSource(configured=[f"C{i}" for i in range(6)])
    kept, dropped = registry.resolve_watch(src, now=NOW, cap=4)
    assert _symbols(kept) == ["C0", "C1", "C2", "C3"]
    assert _symbols(dropped) == ["C4", "C5"]


def test_w6_held_1m_survives_a_full_shortlist():
    src = WatchSource(groups=[_group("orb", "1m")],
                      positions={"acct-orb": ["H1", "H2", "H3"]},
                      rows={SESSION: _rows("orb", [f"S{i:02d}" for i in range(30)])})
    kept, dropped = registry.resolve_watch(src, now=NOW, cap=30)
    assert _symbols(kept)[:3] == ["H1", "H2", "H3"]
    assert len(kept) == 30 and len(dropped) == 3
    assert _symbols(dropped) == ["S27", "S28", "S29"]


def test_w7_held_1m_beyond_the_cap_keeps_the_first():
    held = [f"H{i:02d}" for i in range(35)]
    src = WatchSource(groups=[_group("orb", "1m")], positions={"acct-orb": held})
    kept, dropped = registry.resolve_watch(src, now=NOW, cap=30)
    assert _symbols(kept) == held[:30]
    assert _symbols(dropped) == held[30:]


@pytest.mark.parametrize("now, expected", [
    ("2026-10-26 19:59", date(2026, 10, 26)),
    ("2026-10-26 20:00", date(2026, 10, 27)),
    ("2026-10-24 12:00", date(2026, 10, 26)),      # Saturday
    ("2026-11-27 16:59", date(2026, 11, 27)),      # half day: tape stops at 17:00
    ("2026-11-27 17:00", date(2026, 11, 30)),
    ("2026-11-02 19:59", date(2026, 11, 2)),       # first week of EST
])
def test_w8_watch_session_rolls_at_the_extended_close(now, expected):
    assert registry.watch_session(_et(now)) == expected


def test_w9_explicit_session_wins_over_now():
    other = date(2026, 10, 30)
    src = WatchSource(groups=[_group("orb", "1m")],
                      rows={other: _rows("orb", ["LATER"]), SESSION: _rows("orb", ["NOW"])})
    kept, _ = registry.resolve_watch(src, session=other, now=NOW)
    assert _symbols(kept) == ["LATER"]
    assert src.sessions == [other]


def test_w10_rows_are_empty_before_the_table_exists(monkeypatch):
    sent: list[str] = []

    def fake_run(sql, params, *, fetch=None):
        sent.append(sql)
        return (None,)

    monkeypatch.setattr(registry, "_run", fake_run)
    assert registry.intraday_watch_rows(SESSION) == []
    assert len(sent) == 1 and "to_regclass" in sent[0]


def test_w11_resolve_stream_symbols_matches_resolve_watch():
    src = WatchSource(
        groups=[_group("orb", "1m"), _group("v1")],
        positions={"acct-orb": ["H1M"], "acct-v1": ["H1D"]},
        rows={SESSION: _rows("orb", ["S1", "S2"])},
        configured=["CFG1", "CFG2"], watchlists={"u1": ["WL"]})
    kept, dropped = registry.resolve_watch(src, now=NOW, cap=4, extra=["EXT"])
    assert registry.resolve_stream_symbols(src, cap=4, extra=["EXT"], now=NOW) == \
        (_symbols(kept), _symbols(dropped))


def test_w12_extra_comes_last():
    src = WatchSource(groups=[_group("v1")], positions={"acct-v1": ["H"]},
                      configured=["CFG"], watchlists={"u1": ["WL"]})
    kept, _ = registry.resolve_watch(src, now=NOW, extra=["ext", "CFG"])
    assert _symbols(kept) == ["H", "CFG", "WL", "EXT"]
    assert kept[-1].tier == "extra"


def test_w13_shared_owner_watchlist_read_once():
    src = WatchSource(groups=[_group("a", user="u1"), _group("b", user="u1")],
                      watchlists={"u1": ["WL"]})
    kept, _ = registry.resolve_watch(src, now=NOW)
    assert src.watchlist_calls == ["u1"]
    assert _symbols(kept) == ["WL"]


def test_w14_paused_daily_group_positions_count():
    src = WatchSource(groups=[_group("v1", status="paused")], positions={"acct-v1": ["P"]})
    kept, _ = registry.resolve_watch(src, now=NOW)
    assert [(e.symbol, e.tier, e.groups) for e in kept] == [("P", "held_1d", ("v1",))]


def test_w15_no_managed_groups_leaves_configured_only():
    src = WatchSource(configured=["SPY", "QQQ"], watchlists={"u1": ["WL"]})
    kept, _ = registry.resolve_watch(src, now=NOW)
    assert _symbols(kept) == ["SPY", "QQQ"]
    assert src.watchlist_calls == []


def test_w16_managed_groups_match_the_table():
    pytest.importorskip("psycopg")
    try:
        registry._run("SELECT 1", (), fetch="one")
    except RuntimeError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    expected = registry._run(
        "SELECT id, sim_account_id FROM strategy_groups "
        "WHERE status IN ('active', 'paused') ORDER BY created_at, id",
        (), fetch="all")
    got = registry.managed_groups()
    assert [(g.group_id, g.account_id) for g in got] == [tuple(r) for r in expected]
