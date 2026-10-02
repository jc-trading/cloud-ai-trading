"""D2 sensitivity tests: the perturbation primitives are pure and reproducible,
and the get_bars shim is a real seam — a perturbed run changes the simulator's
outcome while bps=0 reproduces the unperturbed run exactly."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from quant.backtest import metrics, simulator
from quant.backtest.metrics import Trade
from quant.data import corporate_actions, store
from quant.research import r09
from quant.research import sensitivity as sens
from quant.tests.test_backtest import _ohlc_bars, _synth_bars, small_params  # noqa: F401


def _bars(n=200, seed=0):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    dates = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=n)]
    df = _ohlc_bars(dates, list(closes), list(closes * 1.01),
                    list(closes * 0.99), list(closes))
    df.attrs["unadjusted"] = True
    return df


# --- perturb_bars ---------------------------------------------------------

def test_perturb_preserves_shape_invariants_and_attrs():
    df = _bars()
    out = sens.perturb_bars(df, np.random.default_rng(7), bps=5.0)
    assert out.shape == df.shape and list(out.columns) == list(df.columns)
    assert out.attrs["unadjusted"] is True
    assert (out["high"] >= out["low"]).all()
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()
    assert (out["low"] <= out[["open", "close"]].min(axis=1)).all()
    # volume/vwap model the tape, not the quote — untouched
    assert_frame_equal(out[["volume", "vwap", "ts"]], df[["volume", "vwap", "ts"]])


def test_perturb_magnitude_within_five_sigma():
    df = _bars(n=1000)
    out = sens.perturb_bars(df, np.random.default_rng(3), bps=5.0)
    dev = (out["close"] / df["close"] - 1.0).abs()
    assert dev.max() < 5 * 5e-4
    assert dev.mean() > 0                       # noise actually applied
    # the same factor hits every price in the bar
    assert np.allclose(out["close"] / df["close"], out["open"] / df["open"])


def test_perturb_zero_bps_is_identity():
    df = _bars()
    assert_frame_equal(sens.perturb_bars(df, np.random.default_rng(1), bps=0.0), df)


def test_perturb_is_deterministic_for_a_seed():
    df = _bars()
    a = sens.perturb_bars(df, np.random.default_rng(11), bps=5.0)
    b = sens.perturb_bars(df, np.random.default_rng(11), bps=5.0)
    assert_frame_equal(a, b)


# --- bars shim ------------------------------------------------------------

def test_bars_shim_is_per_symbol_deterministic_and_independent_of_read_order():
    frames = {"AAA": _bars(seed=1), "BBB": _bars(seed=2)}

    def real(symbol, timeframe="1d", start=None, end=None, **kw):
        return frames[symbol].copy()

    first = sens.make_bars_shim(42, 5.0, real_get_bars=real)
    second = sens.make_bars_shim(42, 5.0, real_get_bars=real)
    a = first("AAA")
    b_then_a = (second("BBB"), second("AAA"))[1]
    assert_frame_equal(a, b_then_a)
    assert not np.allclose(a["close"], second("BBB")["close"])
    # a different seed moves the tape
    other = sens.make_bars_shim(43, 5.0, real_get_bars=real)("AAA")
    assert not np.allclose(a["close"], other["close"])


def test_bars_shim_passes_unadjusted_attr_through():
    df = _bars()

    def real(symbol, timeframe="1d", start=None, end=None, **kw):
        return df.copy()

    out = sens.make_bars_shim(1, 5.0, real_get_bars=real)("AAA")
    assert out.attrs["unadjusted"] is True


def test_bars_shim_noise_is_window_invariant():
    # the SPY benchmark is read with a different `end` in every walk-forward
    # window; a windowed read must return exactly the rows the full read would
    df = _bars(n=300)
    seen = []

    def real(symbol, timeframe="1d", start=None, end=None, **kw):
        seen.append((start, end))
        return store.slice_range(df.copy(), start, end)

    shim = sens.make_bars_shim(5, 5.0, real_get_bars=real)
    full = shim("AAA")
    cut = pd.Timestamp(df["ts"].iloc[199])
    windowed = shim("AAA", "1d", end=cut)
    assert len(windowed) == 200
    assert_frame_equal(windowed, full.iloc[:200].reset_index(drop=True))
    mid = shim("AAA", "1d", start=pd.Timestamp(df["ts"].iloc[100]), end=cut)
    assert_frame_equal(mid, full.iloc[100:200].reset_index(drop=True))
    # every underlying read was unwindowed — that is what makes it invariant
    assert seen == [(None, None)] * 3


def test_bars_shim_refuses_intraday():
    def real(symbol, timeframe="1d", start=None, end=None, **kw):
        return _bars()

    with pytest.raises(ValueError, match="daily-only"):
        sens.make_bars_shim(1, 5.0, real_get_bars=real)("AAA", "1min")


# --- actions shim ---------------------------------------------------------

def _actions():
    return pd.DataFrame({
        "symbol": ["AAA", "AAA", "BBB"],
        "ex_date": [date(2020, 3, 2), date(2021, 6, 1), date(2020, 3, 2)],
        "action_type": ["split", "dividend", "split"],
        "ratio": [4.0, None, 2.0],
        "cash_amount": [None, 0.5, None],
    })


def _pool(n_split, n_dividend):
    rows = [(f"S{i}", f"2020-01-{i % 28 + 1:02d}", "split") for i in range(n_split)]
    rows += [(f"D{i}", f"2021-01-{i % 28 + 1:02d}", "dividend") for i in range(n_dividend)]
    return pd.DataFrame(rows, columns=["symbol", "ex_date", "action_type"])


def _kinds(targets):
    return {k: sum(1 for t in targets if t["action_type"] == k)
            for k in ("split", "dividend")}


def test_stratify_is_half_splits_rounded_up():
    pool = _pool(40, 200)
    t = sens.stratify_action_targets(pool, 50, seed=3)
    assert len(t) == 50 and _kinds(t) == {"split": 25, "dividend": 25}
    # odd n rounds the SPLIT half up — splits are the case worth testing
    assert _kinds(sens.stratify_action_targets(pool, 5, seed=3)) == {"split": 3, "dividend": 2}
    assert _kinds(sens.stratify_action_targets(pool, 4, seed=3)) == {"split": 2, "dividend": 2}
    # distinct targets: a repeated drop buys no information
    assert len({tuple(sorted(x.items())) for x in t}) == 50


def test_stratify_falls_back_when_a_stratum_is_short():
    # the live cache is ~150:1 dividends, so the split half is the one that runs out
    t = sens.stratify_action_targets(_pool(3, 50), 10, seed=1)
    assert len(t) == 10 and _kinds(t) == {"split": 3, "dividend": 7}
    t = sens.stratify_action_targets(_pool(50, 2), 10, seed=1)
    assert len(t) == 10 and _kinds(t) == {"split": 8, "dividend": 2}
    # both short -> take everything there is, never more
    assert len(sens.stratify_action_targets(_pool(3, 4), 20, seed=1)) == 7


def test_stratify_is_reproducible_and_empty_safe():
    pool = _pool(10, 40)
    assert sens.stratify_action_targets(pool, 6, 9) == sens.stratify_action_targets(pool, 6, 9)
    assert sens.stratify_action_targets(pool, 6, 9) != sens.stratify_action_targets(pool, 6, 10)
    assert sens.stratify_action_targets(pool.iloc[0:0], 6, 9) == []
    assert sens.stratify_action_targets(pool, 0, 9) == []


def _trade_on(symbol, entry):
    return Trade(symbol, entry, entry, 100.0, 105.0, 1.0, 1.0, 5.0, "trailing")


def test_informative_actions_keeps_only_movable_ones():
    pool = pd.DataFrame([("TRADED", "2021-06-01", "split"),
                         ("TRADED", "2019-01-01", "dividend"),
                         ("NEVER", "2021-06-01", "split")],
                        columns=["symbol", "ex_date", "action_type"])
    # the FIRST trade is the floor, so the later 2022 entry must not exclude 2021-06-01
    trades = [_trade_on("TRADED", date(2022, 1, 3)), _trade_on("TRADED", date(2020, 5, 4))]
    out = sens.informative_actions(pool, trades)
    assert len(out) == 1
    assert out.iloc[0]["symbol"] == "TRADED" and out.iloc[0]["ex_date"] == "2021-06-01"
    # an ex_date ON the first trade moves no earlier bar either
    assert sens.informative_actions(
        pool.iloc[[0]], [_trade_on("TRADED", date(2021, 6, 1))]).empty
    assert sens.informative_actions(pool, []).empty
    assert sens.informative_actions(pool.iloc[0:0], trades).empty


def test_build_jobs_stratifies_and_carries_the_target(monkeypatch):
    monkeypatch.setattr(sens, "load_all_actions", lambda: _pool(40, 200))
    jobs = sens.build_jobs(["drop_action"], {"min_confidence": 0.0}, 4)
    assert len(jobs) == 4
    assert _kinds([j["target"] for j in jobs]) == {"split": 2, "dividend": 2}
    assert len({j["seed"] for j in jobs}) == 4
    # the default (n_cap=None) is the deliberately small 10 — drop_action is a
    # weak probe by construction, so it does not get a 50-run budget
    default = sens.build_jobs(["drop_action"], {"min_confidence": 0.0}, None)
    assert len(default) == 10
    assert _kinds([j["target"] for j in default]) == {"split": 5, "dividend": 5}


def test_actions_shim_drops_exactly_the_target_row():
    def real(symbol, **kw):
        df = _actions()
        return df[df["symbol"] == symbol.upper()].reset_index(drop=True)

    shim = sens.make_actions_shim({"symbol": "AAA", "ex_date": "2020-03-02",
                                   "action_type": "split"}, real_load_actions=real)
    aaa = shim("AAA")
    assert len(aaa) == 1 and aaa["action_type"].iloc[0] == "dividend"
    assert len(shim("BBB")) == 1                  # another symbol is untouched
    assert len(sens.make_actions_shim(None, real_load_actions=real)("AAA")) == 2


# --- session arithmetic ---------------------------------------------------

def test_shift_sessions_skips_weekends_and_holidays():
    # 2024-01-02 (Tue) is the first session of the year; -1 must land on
    # 2023-12-29 (Fri), not 2024-01-01 (New Year's Day)
    assert sens.shift_sessions("2024-01-02", -1) == date(2023, 12, 29)
    assert sens.shift_sessions("2024-01-02", 3) == date(2024, 1, 5)
    assert sens.shift_sessions("2024-01-02", -3) == date(2023, 12, 27)
    assert sens.shift_sessions("2024-01-02", 0) == date(2024, 1, 2)


def test_shift_sessions_does_not_eat_a_step_off_a_holiday_anchor():
    # r09.START is New Year's Day: without snapping the anchor onto the calendar
    # first, +1 returns 2016-01-04 — the same effective start as +0 — and the
    # whole start_shift grid silently collapses to (-5,-3,-2,-1,0,+1,+2,+4)
    assert sens.shift_sessions("2016-01-01", 1) == date(2016, 1, 5)
    assert sens.shift_sessions("2016-01-01", 2) == date(2016, 1, 6)
    # the backward side is unchanged by the snap
    assert sens.shift_sessions("2016-01-01", -1) == date(2015, 12, 31)
    assert sens.shift_sessions("2016-01-01", -2) == date(2015, 12, 30)


def test_start_shift_cap_is_symmetric():
    jobs = sens.build_jobs(["start_shift"], {}, 4)
    assert sorted(j["k"] for j in jobs) == [-2, -1, 1, 2]
    assert len(sens.build_jobs(["start_shift"], {}, None)) == 8


# --- bootstrap ------------------------------------------------------------

def _trade(pnl, r_unit=1.0):
    exit_px = 100 + pnl
    return Trade("A", date(2020, 1, 1), date(2020, 2, 1), 100.0, exit_px,
                 1.0, r_unit, pnl, "trailing")


def test_block_bootstrap_length_reproducible_and_metric_stable():
    trades = [_trade(p) for p in (5, -2, 8, -3, 4, -1, 6, -7, 2, -4, 9, -5)]
    a = sens.block_bootstrap(trades, np.random.default_rng(4), block=3)
    b = sens.block_bootstrap(trades, np.random.default_rng(4), block=3)
    assert len(a) == len(trades)
    assert [id(x) for x in a] == [id(x) for x in b]
    assert sens.block_bootstrap([], np.random.default_rng(4)) == []
    # resampling an all-identical sequence cannot move the profit factor
    same = [_trade(5)] * 10 + [_trade(-2)] * 10
    resampled = sens.block_bootstrap(same, np.random.default_rng(1), block=4)
    assert metrics.profit_factor(resampled) == pytest.approx(
        metrics.profit_factor(same), rel=0.5)   # block resample keeps the 2 pnl values


def test_block_bootstrap_actually_reshuffles():
    trades = [_trade(p) for p in range(1, 41)]
    out = sens.block_bootstrap(trades, np.random.default_rng(2), block=5)
    assert [id(x) for x in out] != [id(x) for x in trades]


# --- distribution summary -------------------------------------------------

def test_summarise_distribution_hand_values_and_inf():
    d = sens.summarise_distribution([1.0, 2.0, 3.0, 4.0, 5.0])
    assert d["median"] == pytest.approx(3.0)
    assert d["p5"] == pytest.approx(1.2)         # linear interp on 5 points
    assert d["p95"] == pytest.approx(4.8)
    assert d["n"] == 5 and d["dropped_non_finite"] == 0

    d = sens.summarise_distribution([1.0, float("inf"), 3.0, float("nan"), None])
    assert d["n"] == 2 and d["dropped_non_finite"] == 2
    assert d["median"] == pytest.approx(2.0)

    empty = sens.summarise_distribution([float("inf")])
    assert empty["n"] == 0 and empty["median"] is None


def test_judge_requires_all_three_conditions():
    def runs(pf, cagr, r):
        return [{"summary": {"profit_factor": pf, "cagr": cagr, "avg_r": r}}] * 20

    assert sens.judge(runs(1.5, 0.1, 0.2))["survive"] is True
    assert sens.judge(runs(0.9, 0.1, 0.2))["survive"] is False
    assert sens.judge(runs(1.5, -0.01, 0.2))["survive"] is False
    assert sens.judge(runs(1.5, 0.1, -0.05))["survive"] is False


def test_json_safe_strips_non_finite():
    import json
    out = sens._json_safe({"a": float("inf"), "b": [float("nan"), 1.0], "c": "x"})
    assert out == {"a": None, "b": [None, 1.0], "c": "x"}
    assert "Infinity" not in json.dumps(out) and "NaN" not in json.dumps(out)


# --- driver wiring (pipeline stubbed, seam observed) -----------------------

_STUB_SUMMARY = {"cagr": 0.05, "sharpe": 0.4, "sortino": 0.5, "max_drawdown": -0.2,
                 "return_over_maxdd": 0.25, "win_rate": 0.5, "profit_factor": 1.1,
                 "avg_r": 0.1, "num_trades": 12, "final_equity": 2100.0}


def _stub_pipeline(monkeypatch):
    """Replace the 33s fixed-OOS pipeline with recorders that capture what
    ``bars.get_bars`` was bound to AT CALL TIME — the only way to prove the
    perturbation is installed for feature building, not merely constructed."""
    seen = []

    def fake_inputs(start=r09.START, end=r09.END, *, progress=lambda *_: None):
        seen.append(("inputs", start, simulator.barsmod.get_bars))
        return {}, {}, simulator.SimConfig(start=start, end=end)

    def fake_stitched(feats, sectors, base_cfg, params, *, progress=lambda *_: None):
        seen.append(("stitched", base_cfg.start, simulator.barsmod.get_bars))
        return dict(_STUB_SUMMARY), [], []

    monkeypatch.setattr(r09, "fixed_oos_inputs", fake_inputs)
    monkeypatch.setattr(r09, "stitched_run", fake_stitched)
    return seen


def test_run_one_installs_the_bars_shim_only_for_noise(monkeypatch):
    real = simulator.barsmod.get_bars
    seen = _stub_pipeline(monkeypatch)

    res = sens.run_one({"kind": "noise_1bp", "params": {}, "seed": 1, "bps": 1.0})
    assert res["params"] == {"bps": 1.0}
    assert [s[0] for s in seen] == ["inputs", "stitched"]
    assert all(s[2] is not real for s in seen), "shim was not live during the run"
    assert simulator.barsmod.get_bars is real, "shim leaked past the run"

    seen.clear()
    res = sens.run_one({"kind": "start_shift", "params": {}, "seed": 1, "k": 1})
    assert all(s[2] is real for s in seen), "start_shift must not touch get_bars"
    assert res["params"]["start"] == sens.shift_sessions(r09.START, 1).isoformat()
    assert seen[0][1] == res["params"]["start"], "shifted start never reached the pipeline"


def test_run_one_installs_the_actions_shim_for_drop_action(monkeypatch):
    real = corporate_actions.load_actions
    seen = _stub_pipeline(monkeypatch)
    live = []
    monkeypatch.setattr(r09, "stitched_run",
                        lambda *a, **kw: (live.append(corporate_actions.load_actions),
                                          (dict(_STUB_SUMMARY), [], []))[1])
    target = {"symbol": "AAA", "ex_date": "2020-01-02", "action_type": "split"}
    res = sens.run_one({"kind": "drop_action", "params": {}, "seed": 1, "target": target})
    assert res["params"] == {"action_kind": "split", "symbol": "AAA",
                             "ex_date": "2020-01-02"}
    assert live and live[0] is not real
    assert corporate_actions.load_actions is real


def test_main_writes_artifacts_with_no_simulated_runs(monkeypatch, tmp_path):
    # `--kinds bootstrap` produces zero simulated runs; the verdict placeholder
    # must still carry every key build_report indexes, or the report blows up
    # after the baseline has already been paid for
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(sens, "_store_latest_session", lambda: "2026-01-02")
    monkeypatch.setattr(sens, "OUT_DIR", tmp_path / "sensitivity")
    monkeypatch.setattr(r09, "experiment_stamp", lambda: {"code_sha": "deadbeef"})
    r09_out = tmp_path / "r09"
    r09_out.mkdir()
    (r09_out / "results.json").write_text(
        json.dumps({"recommended_params": {"min_confidence": 0.0}}))
    monkeypatch.setattr(r09, "OUT_DIR", r09_out)

    payload = sens.main(["bootstrap"], 3, 1)

    assert (tmp_path / "sensitivity" / "results.json").is_file()
    report = (tmp_path / "sensitivity" / "report.md").read_text()
    assert "DOES NOT SURVIVE" in report and "p5(profit_factor)" in report
    v = payload["verdict"]
    assert v["runs"] == 0 and v["survive"] is False
    assert set(v["checks"]) == set(sens.VERDICT_CHECKS)
    assert set(v["distribution"]) == set(sens.VERDICT_METRICS)
    assert all(d["p5"] is None for d in v["distribution"].values())
    assert json.loads((tmp_path / "sensitivity" / "results.json").read_text())


# --- end-to-end through the simulator seam --------------------------------

def _e2e_summary(monkeypatch, small_params, seed, bps):  # noqa: F811
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=40)]
    # trend up (entry), crash (stop), recover — every stage reads perturbed prices
    aaa = [10 + i for i in range(25)] + [34, 28, 22, 18, 15, 14, 16, 18, 20, 22,
                                         24, 26, 28, 30, 32]
    bbb = [20 + 0.8 * i for i in range(25)] + [40, 30, 26, 24, 22, 23, 25, 27, 29,
                                               31, 33, 35, 37, 39, 41]
    frames = {"AAA": _synth_bars(sessions, aaa),
              "BBB": _synth_bars(sessions, bbb),
              "SPY": _synth_bars(sessions, [400.0] * 40)}

    def real(symbol, timeframe="1d", start=None, end=None, **kw):
        df = frames.get(symbol)
        if df is None:
            return next(iter(frames.values())).iloc[0:0].copy()
        df = df.copy()
        if start is not None:
            df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
        return df

    monkeypatch.setattr(simulator.barsmod, "get_bars",
                        sens.make_bars_shim(seed, bps, real_get_bars=real))
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-03-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA", "BBB"], {"AAA": "tech", "BBB": "fin"}, cfg)
    return metrics.summary(res.equity, res.trades)


def test_end_to_end_zero_bps_matches_and_seeds_diverge(monkeypatch, small_params):  # noqa: F811
    base = _e2e_summary(monkeypatch, small_params, seed=0, bps=0.0)
    assert base["num_trades"] > 0, "scenario produced no trades — nothing to perturb"
    assert _e2e_summary(monkeypatch, small_params, seed=7, bps=0.0) == base

    seeds = [_e2e_summary(monkeypatch, small_params, seed=s, bps=200.0)
             for s in (1, 2, 3)]
    assert len({round(s["final_equity"], 6) for s in seeds}) == 3, \
        "three seeds collapsed to the same outcome — the shim is not a real seam"
    assert all(s["final_equity"] != base["final_equity"] for s in seeds)
