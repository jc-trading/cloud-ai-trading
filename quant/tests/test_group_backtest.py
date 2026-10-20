"""P3b group backtests on the frozen golden bars: r09 parity of the fixed
stitched OOS, cancellation, universe guards, the platform floor in the grid
search, screener membership offset, and strict-JSON artifacts."""

import json
import warnings
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant import config
from quant.backtest import metrics, simulator
from quant.backtest.walkforward import walk_forward_windows
from quant.data import bars as qbars
from quant.data import calendar
from quant.engine.screener import parse_spec
from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec
from quant.research import group_backtest as gb
from quant.research import r09
from quant.tests.golden import freeze

pytestmark = pytest.mark.skipif(not freeze.BARS_PARQUET.exists(),
                                reason="golden bars not frozen")

SPEC = {"rules": [{"field": "adv_usd", "window": 20, "op": ">=", "value": 1_000_000}]}


def _gcfg(screener=None, params=None, **platform):
    return gb.GroupConfig(V1_COMPOSITE_KEY, get_spec(V1_COMPOSITE_KEY).build(params or {}),
                          parse_spec(screener or {}), 2000.0,
                          {**gb.PLATFORM_CONSTANTS, **platform})


@pytest.fixture(scope="module")
def golden_bars():
    return freeze.frozen_get_bars()


@pytest.fixture
def golden(monkeypatch, golden_bars):
    monkeypatch.setattr(qbars, "get_bars", golden_bars)
    symbols, sectors = freeze.load_symbols()
    uni = gb.Universe(symbols, sectors, None, True, None, "pit_sp500", False,
                      {"label": "pit_sp500"}, None, {})
    monkeypatch.setattr(gb, "prepare_universe", lambda *a, **k: uni)
    return symbols, sectors, uni


@pytest.fixture
def sim_calls(monkeypatch):
    calls = []
    real = simulator.run

    def counting(*a, **k):
        calls.append(a[2])
        return real(*a, **k)

    monkeypatch.setattr(simulator, "run", counting)
    return calls


def test_fixed_stitched_oos_matches_r09(golden):
    symbols, sectors, uni = golden
    base = simulator.SimConfig(start="2018-01-01", end="2026-08-18", membership_on=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        feats = simulator.prepare_features(symbols, sectors, base)
    theirs = r09.stitched_run(feats, sectors, base, gb.R09_FLAT_PARAMS)[0]
    gcfg = _gcfg()
    windows = walk_forward_windows(base.start, base.end, is_years=3, oos_years=1, step_years=1)
    assert len(windows) == 5
    stitched, trades, rows = gb.fixed_oos(gcfg, uni, feats,
                                          gb.sim_config(gcfg, base.start, base.end, None), windows)
    ours = metrics.summary(stitched, trades)
    assert theirs["num_trades"] > 0
    assert ours == theirs
    assert len(rows) == 5


def test_cancel_stops_further_simulator_runs(golden, sim_calls):
    seen = []

    def check():
        seen.append(1)
        if len(seen) >= 3:
            raise gb.Cancelled()

    with pytest.raises(gb.Cancelled):
        gb.run_walk_forward(_gcfg(), "2020-01-01", "2023-01-01", is_years=1, oos_years=1,
                            grid={"exits.reversal_bars": [2, 3, 5]}, check=check)
    assert len(sim_calls) == 2


def test_universe_over_the_cap_raises(monkeypatch):
    monkeypatch.setattr(gb.screen, "earliest_start", lambda spec: date(2016, 1, 1))
    monkeypatch.setattr(gb.screen, "screen_history", lambda spec, as_ofs, **k: (
        {d: frozenset({"AAA", "BBB"}) for d in as_ofs}, {}))
    monkeypatch.setattr(gb.sectorsmod, "load_sectors", lambda: {})
    monkeypatch.setattr(gb, "BACKTEST_MAX_SYMBOLS", 1)
    with pytest.raises(RuntimeError, match="universe too large: 2 symbols > 1"):
        gb.prepare_universe(_gcfg(SPEC), date(2024, 1, 2), date(2024, 3, 1))


def test_walk_forward_without_a_window_raises_value_error(golden):
    with pytest.raises(ValueError, match="no walk-forward window"):
        gb.run_walk_forward(_gcfg(), "2020-01-01", "2021-06-01", is_years=3, oos_years=1)


def test_calibration_sees_only_stock_trades(golden, monkeypatch):
    seen = {}
    real_fit = gb.calibration.fit

    def spy_fit(scores, wins, window_ids=None):
        seen.setdefault("n", len(scores))
        return real_fit(scores, wins, window_ids)

    monkeypatch.setattr(gb.calibration, "fit", spy_fit)
    res = gb.run_walk_forward(_gcfg(), "2019-01-01", "2026-08-18", is_years=3, oos_years=1,
                              grid={"exits.reversal_bars": [3]})
    etf = set(config.ETF_WHITELIST)
    stock = [t for t in res["trades_fixed"] if t.symbol not in etf]
    assert any(t.symbol in etf for t in res["trades_fixed"])     # control: ETFs did trade
    assert seen["n"] + res["calibration"]["skipped_no_score"] == len(stock)
    assert res["calibration"]["n"] == seen["n"] > 0


def test_stitch_keeps_dates_and_matches_r09_math():
    a = pd.Series([2000.0, 2100.0, 2050.0],
                  index=[date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6)])
    b = pd.Series([2000.0, 1900.0], index=[date(2022, 1, 3), date(2022, 1, 4)])
    s = gb.stitch([a, b], 2000.0)
    assert list(s.index) == [date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6),
                             date(2022, 1, 4)]
    assert s.index.is_monotonic_increasing
    rets = pd.concat([a.pct_change().dropna(), b.pct_change().dropna()], ignore_index=True)
    r09_style = pd.concat([pd.Series([2000.0]), 2000.0 * (1.0 + rets).cumprod()],
                          ignore_index=True)
    assert list(s.values) == list(r09_style.values)


def test_platform_floor_collapses_grid_points_into_one_run(golden, sim_calls):
    res = gb.run_walk_forward(_gcfg(min_confidence=65.0), "2020-01-01", "2022-01-01",
                              is_years=1, oos_years=1,
                              grid={"funnel.min_confidence": [50.0, 65.0]})
    assert len(res["metrics"]["windows"]) == 1
    assert res["metrics"]["windows"][0]["is_trail"]["runs"] == 1
    assert len(sim_calls) == 3                       # IS once + OOS selected + OOS fixed
    assert all(c.funnel.min_confidence == 65.0 for c in sim_calls)


def test_screener_membership_reads_the_pool_published_the_night_before(monkeypatch):
    monkeypatch.setattr(gb.screen, "earliest_start", lambda spec: date(2016, 1, 1))
    monkeypatch.setattr(gb.screen, "screen_history", lambda spec, as_ofs, **k: (
        {d: frozenset({f"S{d:%m%d}"}) for d in as_ofs}, {}))
    monkeypatch.setattr(gb.screen, "session_coverage", lambda: {})
    monkeypatch.setattr(gb.sectorsmod, "load_sectors", lambda: {})
    monkeypatch.setattr(gb.securities, "asset_types", lambda: {})
    uni = gb.prepare_universe(_gcfg(SPEC), date(2024, 3, 4), date(2024, 3, 15))
    for prev in calendar.sessions_in_range(date(2024, 3, 4), date(2024, 3, 15)):
        night = calendar.previous_session(prev)
        assert uni.membership_on(prev) == frozenset({f"S{night:%m%d}"})
        assert uni.membership_on(prev) != frozenset({f"S{prev:%m%d}"})
    assert uni.etf_exempt is False and uni.pool_held_exclude == frozenset()
    assert uni.info["screen"]["gaps"] == {r: 0 for r in gb.screen.GAP_REASONS}
    with pytest.raises(KeyError):
        uni.membership_on(date(2024, 3, 18))


def test_jsonable_handles_numpy_inf_and_dates():
    obj = {"b": np.bool_(True), "i": np.int64(3), "f": np.float64(1.5), "inf": float("inf"),
           "nan": np.float64("nan"), "d": date(2024, 1, 2), date(2024, 1, 3): {"x"},
           "t": (1, 2), "ts": pd.Timestamp("2024-01-04")}
    out = gb.jsonable(obj)
    assert out == {"b": True, "i": 3, "f": 1.5, "inf": None, "nan": None, "d": "2024-01-02",
                   "2024-01-03": ["x"], "t": [1, 2], "ts": "2024-01-04T00:00:00"}
    assert type(out["b"]) is bool and type(out["i"]) is int
    json.dumps(out, allow_nan=False)


def test_platform_floor_lifts_a_low_group_threshold_on_every_path(golden, sim_calls):
    low = _gcfg(params={"funnel": {"min_confidence": 0.0}}, min_confidence=65.0)
    assert gb.sim_config(low, "2024-01-01", "2024-12-31", None).funnel.min_confidence == 65.0
    gb.run_single(low, "2024-01-01", "2024-12-31")
    assert [c.funnel.min_confidence for c in sim_calls] == [65.0]
    sim_calls.clear()
    res = gb.run_walk_forward(low, "2020-01-01", "2022-01-01", is_years=1, oos_years=1,
                              grid={"exits.reversal_bars": [3]})
    assert len(res["metrics"]["windows"]) == 1
    assert len(sim_calls) == 3                       # IS + OOS selected + OOS fixed
    fixed_cfg = sim_calls[-1]
    assert fixed_cfg.start == "2021-01-02"
    assert all(c.funnel.min_confidence == 65.0 for c in sim_calls)


def _perturb(feats, mask_fn, seed):
    rng = np.random.default_rng(seed)
    out = {}
    for sym in sorted(feats):
        g = feats[sym].copy()
        m = mask_fn(g["date"])
        g.loc[m, ["open", "high", "low", "close", "price"]] *= rng.uniform(0.5, 1.5)
        g.loc[m, "confidence"] = rng.uniform(0, 100, int(m.sum()))
        out[sym] = g
    return out


def test_is_search_sees_only_the_in_sample_slice(golden):
    gcfg = _gcfg(min_confidence=65.0)
    uni = golden[2]
    start, end = "2019-01-01", "2022-01-01"
    feats = gb.prepare_features(gcfg, uni, start, end)
    base = gb.sim_config(gcfg, start, end, None)
    win = walk_forward_windows(start, end, is_years=1, oos_years=1, step_years=1)[0]
    grid = {"exits.reversal_bars": (2, 3, 5), "exits.trailing_atr_mult": (2.0, 3.0, 4.0)}
    is_lo, is_hi = win.is_start.date(), win.is_end.date()

    def calibrate(f):
        return gb._calibrate_window(gcfg, uni, f, base, win, grid, lambda: None)

    params, trail = calibrate(feats)
    after = _perturb(feats, lambda d: d > is_hi, seed=1)
    assert calibrate(after) == (params, trail)
    inside = _perturb(feats, lambda d: (d >= is_lo) & (d <= is_hi), seed=2)
    assert calibrate(inside)[1] != trail                 # control: the IS slice matters


def test_entry_score_reads_the_session_before_entry():
    f = pd.DataFrame({"date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                      "confidence": [61.0, 72.0, 83.0]})
    trade = lambda d: metrics.Trade("AAA", d, date(2024, 2, 1), 10.0, 11.0, 1.0, 1.0,  # noqa: E731
                                    1.0, "x")
    assert gb._entry_score({"AAA": f}, trade(date(2024, 1, 3))) == 61.0
    assert gb._entry_score({"AAA": f}, trade(date(2024, 1, 5))) == 83.0
    assert gb._entry_score({"AAA": f}, trade(date(2024, 1, 2))) is None
    assert gb._entry_score({}, trade(date(2024, 1, 3))) is None


def test_walk_forward_keeps_selected_and_fixed_apart(golden, sim_calls, monkeypatch, tmp_path):
    picked = {"funnel.min_confidence": 95.0, "exits.reversal_bars": 2}
    monkeypatch.setattr(gb, "_calibrate_window",
                        lambda *a, **k: (dict(picked), {"runs": 0, "steps": []}))
    res = gb.run_walk_forward(_gcfg(min_confidence=65.0), "2019-01-01", "2022-01-01",
                              is_years=1, oos_years=1, grid={"exits.reversal_bars": [3]})
    m = res["metrics"]
    assert len(m["windows"]) == 2 and len(sim_calls) == 4      # 2 selected + 2 fixed
    selected_cfgs, fixed_cfgs = sim_calls[:2], sim_calls[2:]
    assert all(c.funnel.min_confidence == 95.0 and c.exits.reversal_bars == 2
               for c in selected_cfgs)
    assert all(c.funnel.min_confidence == 65.0 and c.exits.reversal_bars == 3
               for c in fixed_cfgs)
    assert len(res["trades_selected"]) != len(res["trades_fixed"])
    assert m["fixed"] == metrics.payload(res["equity_fixed"], res["trades_fixed"])
    assert m["selected"] == metrics.payload(res["equity_selected"], res["trades_selected"])
    assert m["fixed"] != m["selected"]
    for row in m["windows"]:
        assert row["params"] == picked and row["fixed"] != row["selected"]
    out = tmp_path / "wf"
    gb.write_artifacts(out, res)
    fixed_csv = pd.read_csv(out / "equity_fixed.csv")
    selected_csv = pd.read_csv(out / "equity_selected.csv")
    assert list(fixed_csv["equity"]) == pytest.approx(list(res["equity_fixed"].values))
    assert list(selected_csv["equity"]) == pytest.approx(list(res["equity_selected"].values))
    assert len(pd.read_csv(out / "trades_fixed.csv")) == len(res["trades_fixed"])
    assert len(pd.read_csv(out / "trades_selected.csv")) == len(res["trades_selected"])
    assert json.loads((out / "windows.json").read_text())[0]["params"] == picked


# --- P3c trade settings --------------------------------------------------------

RID = "0b8a2f7e-5d1c-4c3e-9a77-2f1e6d4b9c01"


def _snap(**extra):
    return {"strategy_key": V1_COMPOSITE_KEY, "params": {}, "screener": {},
            "starting_capital": 2000.0, "platform": dict(gb.PLATFORM_CONSTANTS),
            "timeframe": "1d", **extra}


def _prob_gcfg(floor=80.0, **settings):
    from dataclasses import replace

    from quant.engine.trade_rules import TradeSettings

    trade = TradeSettings(entry_prob_min=0.55, calibration_run_id=RID, **settings)
    return replace(_gcfg(min_confidence=65.0), trade=trade, score_floor=floor)


def test_from_snapshot_reads_trade_settings_and_calibration_ref():
    from quant.engine.trade_rules import TradeSettings

    old = gb.GroupConfig.from_snapshot(_snap())
    assert old.trade is None and old.score_floor is None
    assert gb.GroupConfig.from_snapshot(_snap(trade_settings_base={})).trade is None
    new = gb.GroupConfig.from_snapshot(_snap(
        trade_settings_base={"take_profit_pct": 0.05},
        calibration_ref={"run_id": RID, "entry_prob_min": 0.55, "blocks": [],
                         "score_floor": 80.0}))
    assert new.trade == TradeSettings(take_profit_pct=0.05, entry_prob_min=0.55,
                                      calibration_run_id=RID)
    assert new.score_floor == 80.0


def test_sim_config_applies_the_calibrated_score_floor():
    assert gb.sim_config(_prob_gcfg(), "2024-01-01", "2024-12-31",
                         None).funnel.min_confidence == 80.0
    assert gb.sim_config(_gcfg(min_confidence=65.0), "2024-01-01", "2024-12-31",
                         None).funnel.min_confidence == 65.0


def test_apply_knobs_keeps_the_score_floor_over_a_lower_grid_threshold():
    gcfg = _prob_gcfg()
    base = gb.sim_config(gcfg, "2024-01-01", "2024-12-31", None)
    low = gb.apply_knobs(gcfg, base, {"funnel.min_confidence": 65.0}, "2024-01-01", "2024-06-30")
    high = gb.apply_knobs(gcfg, base, {"funnel.min_confidence": 90.0}, "2024-01-01", "2024-06-30")
    assert (low.funnel.min_confidence, high.funnel.min_confidence) == (80.0, 90.0)


def test_trade_settings_reach_every_simulator_run(golden, monkeypatch):
    from dataclasses import replace

    from quant.engine.trade_rules import TradeSettings

    seen = []
    real = simulator.run

    def spy(*a, **k):
        seen.append(k.get("trade"))
        return real(*a, **k)

    monkeypatch.setattr(simulator, "run", spy)
    gcfg = replace(_gcfg(min_confidence=65.0), trade=TradeSettings(take_profit_pct=0.05))
    gb.run_single(gcfg, "2024-01-01", "2024-12-31")
    res = gb.run_walk_forward(gcfg, "2020-01-01", "2022-01-01", is_years=1, oos_years=1,
                              grid={"exits.reversal_bars": [3, 4]})
    assert len(res["metrics"]["windows"]) == 1
    assert len(seen) >= 4
    assert all(t is gcfg.trade for t in seen)
