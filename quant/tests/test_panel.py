"""P2 panel: adjusted returns across a split, the RAW-guard truncation, atomic
idempotent year rebuilds, and the screen on top of it (incomplete session ->
PanelIncomplete; a complete one screens end to end)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant import config
from quant.data import calendar, corporate_actions, panel, screen, store
from quant.engine import screener

DAYS = calendar.sessions_in_range(date(2026, 8, 3), date(2026, 10, 16))


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


def test_split_day_return_is_adjusted(world):
    split_day = DAYS[20]
    closes = [100.0 + i for i in range(len(DAYS))]
    raw = [c if d >= split_day else c * 2 for c, d in zip(closes, DAYS)]
    _write("SPL", raw)
    corporate_actions.store_actions([{"symbol": "SPL", "ex_date": split_day,
                                      "action_type": "split", "ratio": 2.0,
                                      "cash_amount": None}])
    corporate_actions.record_sync(["SPL"], DAYS[-1])
    rows = panel.symbol_rows("SPL", DAYS[5], DAYS[-1]).set_index("date")
    assert rows.loc[split_day, "ret"] == pytest.approx(closes[20] / closes[19] - 1,
                                                       rel=1e-12)
    assert rows.loc[split_day, "close"] == raw[20]            # RAW stays RAW
    assert rows.index[0] == DAYS[5]
    assert not np.isnan(rows["ret"].iloc[0])                  # padded prior close


def test_unadjusted_symbol_is_cut_at_synced_at(world):
    jump = DAYS[30]
    raw = [50.0 if d < jump else 100.0 for d in DAYS]         # +100%, no action cached
    _write("JMP", raw)
    corporate_actions.record_sync(["JMP"], DAYS[25])
    rows = panel.symbol_rows("JMP", DAYS[0], DAYS[-1])
    assert rows["date"].max() == DAYS[25]
    _write("NEVER", raw)
    assert panel.symbol_rows("NEVER", DAYS[0], DAYS[-1]).empty


def test_rebuild_is_idempotent_and_records_sessions(world):
    _write("AAA", [10.0 + i for i in range(len(DAYS))])
    _write("BBB", [20.0 - 0.01 * i for i in range(len(DAYS))])
    panel.write_years(["AAA", "BBB"], [2026], complete_sessions={DAYS[-1]: True})
    first = pd.read_parquet(panel.year_path(2026))
    panel.write_years(["BBB", "AAA"], [2026], complete_sessions={DAYS[-2]: False})
    second = pd.read_parquet(panel.year_path(2026))
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 2 * len(DAYS)
    assert panel.is_complete(DAYS[-1]) and not panel.is_complete(DAYS[-2])
    assert panel.latest_complete() == DAYS[-1]


def test_a_failed_rebuild_leaves_the_old_file(world, monkeypatch):
    _write("AAA", [10.0 + i for i in range(len(DAYS))])
    panel.write_years(["AAA"], [2026], complete_sessions={DAYS[-1]: True})
    before = panel.year_path(2026).read_bytes()

    def boom(code, start, end):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(panel, "symbol_rows", boom)
    with pytest.raises(RuntimeError):
        panel.write_years(["AAA"], [2026], complete_sessions={DAYS[-2]: True})
    assert panel.year_path(2026).read_bytes() == before
    assert list(panel.year_path(2026).parent.glob("*.tmp-*")) == []
    assert not panel.is_complete(DAYS[-2])


def test_update_rebuilds_the_years_of_the_last_15_sessions(world, monkeypatch):
    seen = {}
    monkeypatch.setattr(panel, "write_years",
                        lambda codes, years, complete_sessions: seen.update(
                            years=years, flags=complete_sessions))
    panel.update(date(2026, 1, 9), ["AAA"], complete=True)
    assert seen == {"years": [2025, 2026], "flags": {date(2026, 1, 9): True}}


def test_read_returns_wide_frames_with_gaps_as_nan(world):
    _write("AAA", [10.0 + i for i in range(len(DAYS))])
    _write("BBB", [5.0] * (len(DAYS) - 1), days=DAYS[:-1])
    panel.write_years(["AAA", "BBB"], [2026])
    close, volume, ret = panel.read(["AAA", "BBB", "ZZZ"], DAYS[-5:])
    assert list(close.index) == DAYS[-5:]
    assert list(close.columns) == ["AAA", "BBB", "ZZZ"]
    assert close.loc[DAYS[-1], "AAA"] == 10.0 + len(DAYS) - 1
    assert np.isnan(close.loc[DAYS[-1], "BBB"]) and close["ZZZ"].isna().all()
    assert volume.loc[DAYS[-2], "BBB"] == 1_000_000.0


def _securities(monkeypatch, types):
    monkeypatch.setattr(screen.securities, "eligible_codes",
                        lambda t=None: sorted(c for c, k in types.items() if k in t))
    monkeypatch.setattr(screen.securities, "asset_types", lambda: dict(types))
    monkeypatch.setattr(screen.securities, "cik_by_code", lambda: {"AAA": 1, "BBB": 2})
    monkeypatch.setattr(screen.edgar, "load_facts", lambda ciks: pd.DataFrame(
        [(1, screener.SHARE_CONCEPTS[0], "2026-06-30", "2026-07-20", "a1", "10-Q",
          10_000_000),
         (2, screener.SHARE_CONCEPTS[0], "2026-06-30", "2026-07-20", "a2", "10-Q", 1000)],
        columns=["cik", "concept", "end_date", "filed", "accn", "form", "value"]))


def test_run_screen_refuses_an_incomplete_session(world):
    spec = screener.parse_spec(screener.DEFAULT_SCREENER)
    panel.write_years([], [2026], complete_sessions={DAYS[-1]: False})
    with pytest.raises(screen.PanelIncomplete):
        screen.run_screen(spec, DAYS[-1])


def test_run_screen_end_to_end(world, monkeypatch):
    _write("AAA", [20.0] * len(DAYS))
    _write("BBB", [20.0] * len(DAYS))
    _write("SPY", [500.0] * len(DAYS))
    panel.write_years(["AAA", "BBB", "SPY"], [2026], complete_sessions={DAYS[-1]: True})
    _securities(monkeypatch, {"AAA": "stock", "BBB": "stock", "SPY": "etf"})
    spec = screener.parse_spec({"rules": [
        {"field": "market_cap", "op": ">=", "value": 100_000_000},
        {"field": "adv_usd", "window": 20, "op": ">=", "value": 1_000_000}]})
    res = screen.run_screen(spec, DAYS[-1], held=("HELD",))
    assert res.evaluated == 3
    assert [s.passed_cumulative for s in res.rule_stats] == [1, 1]
    assert res.rule_stats[0].null == 1                         # SPY: ETF -> NULL
    assert res.pool == ("AAA", "HELD")
    explained = screen.explain(spec, DAYS[-1], ["AAA", "SPY"])
    assert explained.loc["AAA", "market_cap"] == 20.0 * 10_000_000
    assert explained.loc["AAA", "shares"] == 10_000_000
    assert bool(explained.loc["SPY", "pass:market_cap"]) is False


def test_include_codes_that_are_not_eligible_are_ignored(world, monkeypatch):
    _write("AAA", [20.0] * len(DAYS))
    _write("SPY", [500.0] * len(DAYS))
    panel.write_years(["AAA", "SPY"], [2026], complete_sessions={DAYS[-1]: True})
    _securities(monkeypatch, {"AAA": "stock", "SPY": "etf"})
    spec = screener.parse_spec({"rules": [{"field": "price", "op": ">=", "value": 1000}],
                                "include": ["AAA", "SPY", "GONE"],
                                "asset_types": ["stock"]})
    res = screen.run_screen(spec, DAYS[-1], held=("HELD",))
    assert res.include_ignored == ("SPY", "GONE")              # etf not in asset_types
    assert res.include_added == ("AAA",)
    assert res.pool == ("AAA", "HELD")


def test_orphan_temp_files_are_removed(world):
    root = config.PANEL_ROOT
    root.mkdir(parents=True)
    (root / "2026.parquet").write_bytes(b"keep")
    (root / "2026.parquet.tmp-19").write_bytes(b"orphan")
    (root / "_sessions.json.tmp-7").write_text("{}")
    (root / "_sessions.json").write_text('{"2026-10-16": true}')
    assert panel.remove_orphan_tmp() == 2
    assert sorted(p.name for p in root.iterdir()) == ["2026.parquet", "_sessions.json"]
    assert (root / "2026.parquet").read_bytes() == b"keep"
    assert (root / "_sessions.json").read_text() == '{"2026-10-16": true}'


def test_orphan_cleanup_without_a_panel_dir(world):
    assert not config.PANEL_ROOT.exists()
    assert panel.remove_orphan_tmp() == 0
