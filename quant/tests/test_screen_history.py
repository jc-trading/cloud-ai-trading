"""P3b screen_history: historical pools with run_screen's exact math, plus the
gap rules (live flag first, panel coverage for unflagged sessions)."""

from datetime import date

import pandas as pd
import pytest

from quant import config
from quant.data import calendar, panel, screen, store
from quant.engine import screener

DAYS = calendar.sessions_in_range(date(2025, 11, 3), date(2026, 1, 30))
TYPES = {"AAA": "stock", "BBB": "stock", "CCC": "stock", "SPY": "etf"}
SPEC = {"rules": [{"field": "market_cap", "op": ">=", "value": 100_000_000},
                  {"field": "adv_usd", "window": 5, "op": ">=", "value": 1_000_000}]}


def _write(code, closes, days=DAYS, volume=1_000_000.0):
    ts = [pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC") for d in days]
    df = pd.DataFrame({"ts": ts, "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": volume, "vwap": closes,
                       "trade_count": 10})
    store.write_frame(code, "daily", df, provider=config.PROVIDER_HISTORICAL)


@pytest.fixture
def world(tmp_store, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PANEL_ROOT", tmp_path / "panel")
    return tmp_path


def _securities(monkeypatch, types=TYPES):
    monkeypatch.setattr(screen.securities, "eligible_codes",
                        lambda t=None: sorted(c for c, k in types.items() if k in t))
    monkeypatch.setattr(screen.securities, "asset_types", lambda: dict(types))
    monkeypatch.setattr(screen.securities, "cik_by_code",
                        lambda: {"AAA": 1, "BBB": 2, "CCC": 3})
    monkeypatch.setattr(screen.edgar, "load_facts", lambda ciks: pd.DataFrame(
        [(1, screener.SHARE_CONCEPTS[0], "2025-06-30", "2025-08-01", "a1", "10-Q", 10_000_000),
         (2, screener.SHARE_CONCEPTS[0], "2025-06-30", "2025-08-01", "a2", "10-Q", 10_000_000),
         (3, screener.SHARE_CONCEPTS[0], "2025-06-30", "2025-08-01", "a3", "10-Q", 1000)],
        columns=["cik", "concept", "end_date", "filed", "accn", "form", "value"]))


def _market(days=DAYS, *, hole=None):
    n = len(days)
    with_hole = [d for d in days if d != hole]
    _write("AAA", [20.0] * n, days=days)
    _write("BBB", [5.0 + 10.0 * DAYS.index(d) / (len(DAYS) - 1) for d in days], days=days)
    _write("CCC", [30.0] * len(with_hole), days=with_hole)
    _write("SPY", [500.0] * len(with_hole), days=with_hole)
    panel.write_years(["AAA", "BBB", "CCC", "SPY"], sorted({d.year for d in days}))


def _history(as_ofs, **kw):
    return screen.screen_history(screener.parse_spec(SPEC), as_ofs, **kw)


def test_history_equals_run_screen_on_each_day_across_years(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    as_ofs = [DAYS[10], DAYS[35], DAYS[-3]]
    assert as_ofs[1].year == 2025 and as_ofs[2].year == 2026
    seen = []
    pools, gaps = _history(as_ofs, progress=seen.append)
    assert gaps == {}
    assert len(seen) == 3
    panel._save_sessions({str(d): True for d in as_ofs})
    for d in as_ofs:
        assert pools[d] == frozenset(screen.run_screen(screener.parse_spec(SPEC), d).pool)
    assert pools[DAYS[10]] == {"AAA"}
    assert pools[DAYS[-3]] == {"AAA", "BBB"}


def test_live_flag_false_is_a_live_incomplete_gap(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    panel._save_sessions({str(DAYS[20]): False})
    pools, gaps = _history([DAYS[20], DAYS[21]])
    assert gaps == {DAYS[20]: "live_incomplete"}
    assert pools[DAYS[20]] is None and pools[DAYS[21]] == {"AAA"}


def test_low_coverage_unless_the_session_is_flagged_complete(world, monkeypatch):
    hole = DAYS[30]
    _market(hole=hole)
    _securities(monkeypatch)
    assert screen.session_coverage()[hole] == 0.5
    pools, gaps = _history([hole])
    assert gaps == {hole: "low_coverage"} and pools[hole] is None
    panel._save_sessions({str(hole): True})
    pools, gaps = _history([hole])
    assert gaps == {}
    assert pools[hole] == frozenset(screen.run_screen(screener.parse_spec(SPEC), hole).pool)
    assert "AAA" in pools[hole]


def test_pool_over_the_cap_is_a_gap_not_a_truncation(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    monkeypatch.setattr(config, "SCREEN_POOL_MAX", 1)
    pools, gaps = _history([DAYS[10], DAYS[-3]])
    assert gaps == {DAYS[-3]: "over_cap"}
    assert pools == {DAYS[10]: frozenset({"AAA"}), DAYS[-3]: None}


def test_lookback_gap_and_earliest_start(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    spec = screener.parse_spec(SPEC)
    assert screen.panel_first_session() == DAYS[0]
    earliest = screen.earliest_start(spec)
    assert earliest == DAYS[5]
    ok_as_of = calendar.previous_session(earliest)
    bad_as_of = calendar.previous_session(calendar.previous_session(earliest))
    pools, gaps = _history([ok_as_of, bad_as_of])
    assert gaps == {bad_as_of: "lookback"}
    assert pools[ok_as_of] == {"AAA"}


def test_rows_after_the_as_of_never_change_the_pools(world, monkeypatch):
    early = DAYS[:-10]
    _market(early)
    _securities(monkeypatch)
    as_ofs = [early[10], early[-1]]
    before = _history(as_ofs)
    late = DAYS[-10:]
    for code in ("AAA", "BBB", "CCC", "SPY"):
        _write(code, [16.0] * len(late), days=late, volume=1.0)
    panel.write_years(["AAA", "BBB", "CCC", "SPY"], [2025, 2026])
    assert screen.panel_first_session() == DAYS[0]
    assert _history(as_ofs) == before


def test_session_coverage_counts_only_alive_codes(world):
    d = DAYS[:10]
    _write("AAA", [10.0] * 10, days=d)
    _write("BBB", [10.0] * 7, days=d[3:])
    c_days = [x for i, x in enumerate(d[:8]) if i != 5]
    _write("CCC", [10.0] * len(c_days), days=c_days)
    panel.write_years(["AAA", "BBB", "CCC"], [2025])
    cov = screen.session_coverage()
    assert set(cov) == set(d)
    assert cov[d[0]] == 1.0                     # BBB not listed yet
    assert cov[d[3]] == 1.0
    assert cov[d[5]] == pytest.approx(2 / 3)    # CCC suspended
    assert cov[d[8]] == 1.0                     # CCC delisted after d[7]
    assert all(type(k) is date for k in cov)
