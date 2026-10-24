"""P5a: the 1m backtest — shortlists, coverage refusal, the session loop over
the shared simulator, session walk-forward windows, the stitch anchor and the
artifacts, on a tmp store with a synthetic panel and synthetic 1min days."""

from datetime import date

import pandas as pd
import pytest

from quant import config
from quant.backtest import calibration
from quant.backtest.intraday import DayContext, GroupDayState, run_day
from quant.backtest.intraday_costs import IntradayCostModel
from quant.data import calendar, intraday_data, panel, screen, store
from quant.engine.intraday_strategies import orb_score
from quant.engine.screener import parse_spec
from quant.engine.strategies import M1_ORB_KEY, get_spec
from quant.engine.trade_rules import IntradayTradeSettings
from quant.research import group_backtest
from quant.research import intraday_backtest as ib

DAILY = calendar.sessions_in_range(date(2026, 7, 1), date(2026, 10, 23))
MINUTE_DAYS = calendar.sessions_in_range(date(2026, 10, 1), date(2026, 10, 23))
TARGET = MINUTE_DAYS[-10:]
TYPES = {"AAA": "stock", "BBB": "stock", "CCC": "stock", "SPY": "etf"}
SPEC = {"rules": [{"field": "price", "op": ">=", "value": 1}], "asset_types": ["stock"]}
ET = "America/New_York"


def _daily(code, closes):
    ts = [pd.Timestamp(d).tz_localize(ET).tz_convert("UTC") for d in DAILY]
    df = pd.DataFrame({"ts": ts, "open": closes, "high": closes, "low": closes, "close": closes,
                       "volume": 1_000_000.0, "vwap": closes, "trade_count": 10})
    store.write_frame(code, "daily", df, provider=config.PROVIDER_HISTORICAL)


def _session(day, breakout: bool) -> pd.DataFrame:
    o, _ = calendar.rth_bounds(day)
    rows = []
    for i in range(390):
        ts = o + pd.Timedelta(minutes=i)
        if i < 15:
            b = (100.0, 100.5, 99.8, 100.0, 1000.0)
        elif breakout and i == 20:
            b = (100.4, 101.2, 100.3, 101.0, 3000.0)
        elif breakout and i == 21:
            b = (101.1, 101.5, 101.0, 101.4, 1000.0)
        elif breakout and i > 21:
            b = (102.0, 102.0, 102.0, 102.0, 1000.0)
        else:
            b = (100.2, 100.3 + (i % 7) * 0.05, 100.0 - (i % 5) * 0.04, 100.2, 1000.0)
        rows.append((ts, *b))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["vwap"], df["trade_count"] = df["close"], 5.0
    return df


@pytest.fixture
def world(tmp_store, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PANEL_ROOT", tmp_path / "panel")
    monkeypatch.setattr(screen.securities, "eligible_codes",
                        lambda t=None: sorted(c for c, k in TYPES.items() if k in t))
    monkeypatch.setattr(screen.securities, "asset_types", lambda: dict(TYPES))
    n = len(DAILY)
    _daily("AAA", [20.0 + 0.5 * (i % 2) for i in range(n)])
    _daily("BBB", [10.0 + 0.1 * (i % 2) for i in range(n)])
    _daily("CCC", [30.0] * n)
    _daily("SPY", [500.0] * n)
    panel.write_years(list(TYPES), sorted({d.year for d in DAILY}))
    panel._save_sessions({str(d): True for d in DAILY})
    for d in MINUTE_DAYS:
        for sym in ("AAA", "BBB", "CCC"):
            store.write_bars(sym, "1min", d.isoformat(), _session(d, sym == "AAA"),
                             provider=config.PROVIDER_HISTORICAL)
    return tmp_store


def _gc(**kw):
    spec = get_spec(M1_ORB_KEY)
    base = dict(strategy_key=M1_ORB_KEY, cfg=spec.defaults, screener=parse_spec(SPEC),
                starting_capital=2000.0, platform=group_backtest.PLATFORM_CONSTANTS,
                trade=IntradayTradeSettings())
    base.update(kw)
    return ib.IntradayGroupConfig(**base)


def test_shortlist_history_equals_shortlist_for_session(world):
    gc = _gc()
    hist, reasons = ib.shortlist_history(gc.screener, gc.cfg, TARGET)
    assert reasons == {}
    for S in TARGET:
        assert hist[S] == ib.shortlist_for_session(gc.screener, gc.cfg, S)
    assert [s for s, _ in hist[TARGET[0]]] == ["AAA", "BBB", "CCC"]


def test_one_session_equals_driving_run_day_directly(world):
    gc = _gc()
    S = TARGET[0]
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, [S])
    res = ib.run_sessions(gc, [S], shortlists)
    syms = ["AAA", "BBB", "CCC"]
    prior = {s: intraday_data.prior_rth(s, S, 2) for s in syms}
    adv = {s: intraday_data.daily_adv(s, calendar.previous_session(S)) for s in syms}
    days = {s: intraday_data.day_bars(s, S).set_index("ts") for s in syms}
    rth = calendar.rth_bounds(S)
    inputs = []
    for i in range(390):
        t = rth[0] + pd.Timedelta(minutes=i)
        bars = {s: {k: float(days[s].at[t, k]) for k in ("open", "high", "low", "close",
                                                           "volume")}
                for s in syms if t in days[s].index}
        inputs.append((t, bars, syms, "ok", "live"))
    state = GroupDayState(S, 2000.0, {}, [], {}, 2000.0)
    want = run_day(state, DayContext.from_prior(prior), inputs, strategy=orb_score,
                   strategy_cfg=gc.cfg, trade=gc.trade, costs=IntradayCostModel(), adv=adv,
                   score_floor=None, shortlist=syms, rth=rth, risk_pct=0.03)
    assert res.events == want
    assert len(res.trades) == 1 and res.trades[0].symbol == "AAA" and res.trades[0].pnl > 0
    assert res.equity.iloc[-1] == state.cash


def test_insufficient_coverage_raises_with_the_missing_pairs(world):
    for sym in ("BBB", "CCC"):
        store.bar_path(sym, "1min", TARGET[-1].isoformat()).unlink()
    gc = _gc()
    monkey = config.INTRADAY_MIN_COVERAGE
    with pytest.raises(ib.MissingIntradayData) as e:
        ib.run_single_1m(gc, TARGET[-2], TARGET[-1])
    assert e.value.pairs == [("BBB", TARGET[-1]), ("CCC", TARGET[-1])]
    assert str(e.value) == "missing 1min data: 2 symbol-days"
    assert monkey == 0.98


def test_an_iex_day_counts_as_missing(world):
    path = store.bar_path("AAA", "1min", TARGET[-1].isoformat())
    path.unlink()
    store.write_bars("AAA", "1min", TARGET[-1].isoformat(), _session(TARGET[-1], True),
                     provider=config.PROVIDER_REALTIME, replace=True)
    cov = ib.coverage_of({("AAA", TARGET[-1]), ("BBB", TARGET[-1])})
    assert cov["present"] == 1 and cov["missing"] == [("AAA", TARGET[-1])]
    assert cov["iex"] == 1 and cov["ratio"] == 0.5
    assert not ib.pair_present("AAA", TARGET[-1])


def test_cancel_stops_before_the_second_session(world):
    gc = _gc()
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, TARGET[:3])
    calls, seen = [], []

    def check():
        calls.append(1)
        if len(calls) == 2:
            raise group_backtest.Cancelled()
    with pytest.raises(group_backtest.Cancelled):
        ib.run_sessions(gc, TARGET[:3], shortlists, check=check, progress=seen.append)
    assert len(seen) == 1 and seen[0].startswith(f"session {TARGET[0]}")


def test_session_windows_step_by_the_oos_length():
    days = MINUTE_DAYS[:10]
    w = ib.session_windows(days, 4, 2)
    assert [(x.is_start.date(), x.is_end.date(), x.oos_start.date(), x.oos_end.date())
            for x in w] == [(days[0], days[3], days[4], days[5]),
                            (days[2], days[5], days[6], days[7]),
                            (days[4], days[7], days[8], days[9])]
    assert len(ib.session_windows(days, 4, 3)) == 2
    assert ib.session_windows(days, 8, 3) == []
    assert isinstance(w[0].is_start, pd.Timestamp)


def test_walk_forward_without_a_window_raises(world):
    with pytest.raises(ValueError, match="no walk-forward window"):
        ib.run_walk_forward_1m(_gc(), TARGET[0], TARGET[-1], is_sessions=8, oos_sessions=5)


def test_calibration_eats_the_fixed_oos_trades_in_order(world, monkeypatch):
    gc = _gc()
    seen = {}
    real_fit = calibration.fit

    def fit(scores, wins, window_ids=None):
        seen.update(scores=list(scores), wins=list(wins), ids=list(window_ids))
        monkeypatch.setattr(ib.calibration, "fit", real_fit)
        return real_fit(scores, wins, window_ids)
    monkeypatch.setattr(ib.calibration, "fit", fit)
    grid = {"signal.min_volume_ratio": [0.0, 1.0]}
    out = ib.run_walk_forward_1m(gc, TARGET[0], TARGET[-1], is_sessions=4, oos_sessions=3,
                                 grid=grid)
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, TARGET)
    want_scores, want_wins, want_ids = [], [], []
    for wi, win in enumerate(ib.session_windows(TARGET, 4, 3), 1):
        oos = [s for s in TARGET if win.oos_start.date() <= s <= win.oos_end.date()]
        fix = ib.run_sessions(gc, oos, shortlists)
        want_scores += fix.scores
        want_wins += [t.pnl > 0 for t in fix.trades]
        want_ids += [wi] * len(fix.trades)
    assert seen == {"scores": want_scores, "wins": want_wins, "ids": want_ids}
    assert len(out["trades_fixed"]) == len(want_scores) > 0
    assert set(out["metrics"]) >= {"fixed", "selected", "windows", "gate", "universe"}
    assert out["metrics"]["windows"][0]["params"] in (
        {"signal.min_volume_ratio": 0.0}, {"signal.min_volume_ratio": 1.0})


def test_stitch_keeps_each_windows_first_session_return(world):
    gc = _gc()
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, TARGET)
    a = ib.run_sessions(gc, TARGET[:3], shortlists).equity
    b = ib.run_sessions(gc, TARGET[3:6], shortlists).equity
    stitched = group_backtest.stitch([a, b], 2000.0)
    assert stitched.iloc[1] / stitched.iloc[0] == pytest.approx(a.iloc[1] / a.iloc[0])
    assert stitched.iloc[4] / stitched.iloc[3] == pytest.approx(b.iloc[1] / b.iloc[0])
    assert a.iloc[1] != a.iloc[0]
    unanchored = group_backtest.stitch([a.iloc[1:], b.iloc[1:]], 2000.0)
    assert unanchored.iloc[-1] != pytest.approx(stitched.iloc[-1])


def test_equity_starts_on_the_session_before(world):
    gc = _gc()
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, TARGET[:2])
    eq = ib.run_sessions(gc, TARGET[:2], shortlists).equity
    assert list(eq.index) == [calendar.previous_session(TARGET[0]), *TARGET[:2]]
    assert eq.iloc[0] == 2000.0


def test_single_run_and_its_artifacts(world, tmp_path):
    out = ib.run_single_1m(_gc(), TARGET[0], TARGET[-1])
    m = out["metrics"]
    assert m["coverage"]["ratio"] == 1.0 and m["cost_model"] == "IntradayCostModel (uncalibrated)"
    assert m["summary"]["num_trades"] == len(out["trades"]) == len(out["scores"]) == 10
    assert out["universe"].pools is None
    d = tmp_path / "bt"
    ib.write_artifacts_1m(d, out)
    assert {p.name for p in d.iterdir()} == {"equity.csv", "trades.csv", "shortlists.parquet"}
    sl = pd.read_parquet(d / "shortlists.parquet")
    assert list(sl.columns) == ["session", "rank", "symbol", "score"] and len(sl) == 30


def test_from_snapshot_defaults_the_trade_settings():
    snap = {"strategy_key": M1_ORB_KEY, "params": get_spec(M1_ORB_KEY).default_params(),
            "screener": SPEC, "starting_capital": "2000.00",
            "platform": group_backtest.PLATFORM_CONSTANTS, "trade_settings_base": {}}
    gc = ib.IntradayGroupConfig.from_snapshot(snap)
    assert gc.trade == IntradayTradeSettings() and gc.starting_capital == 2000.0
    snap["trade_settings_base"] = {"stop_pct": 0.02}
    assert ib.IntradayGroupConfig.from_snapshot(snap).trade.stop_atr_mult is None


def test_cli_need_out_and_smoke(world, tmp_path, capsys):
    spec = '{"rules": [{"field": "price", "op": ">=", "value": 1}], "asset_types": ["stock"]}'
    store.bar_path("CCC", "1min", TARGET[-1].isoformat()).unlink()
    need = tmp_path / "need.parquet"
    assert ib.main(["--spec", spec, "--start", str(TARGET[-3]), "--end", str(TARGET[-1]),
                    "--need-out", str(need)]) == 0
    assert "NEED needed=15 missing=1 iex=0" in capsys.readouterr().out
    got = pd.read_parquet(need)
    assert list(got["symbol"]) == ["CCC"] and pd.Timestamp(got["session"][0]).date() == TARGET[-1]
    assert ib.main(["--spec", spec, "--start", str(TARGET[-3]), "--end", str(TARGET[-1]),
                    "--smoke"]) == 1
    assert "ERROR missing 1min data: 1 symbol-days" in capsys.readouterr().out
    assert ib.main(["--spec", spec, "--start", str(TARGET[-3]), "--end", str(TARGET[-2]),
                    "--smoke"]) == 0
    out = capsys.readouterr().out
    assert "COVERAGE 1.0" in out and "SUMMARY {" in out and "TRADES 2" in out


def test_an_iex_day_is_dropped_not_traded(world):
    S = TARGET[0]
    store.write_bars("AAA", "1min", S.isoformat(), _session(S, True),
                     provider=config.PROVIDER_REALTIME, replace=True)
    gc = _gc()
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, [S])
    assert [s for s, _ in shortlists[S]][0] == "AAA"
    res = ib.run_sessions(gc, [S], shortlists)
    assert res.dropped == 1 and res.trades == []
    assert {e.symbol for e in res.events} <= {"BBB", "CCC"}


def test_coverage_exactly_at_the_threshold_runs(world, monkeypatch):
    store.bar_path("CCC", "1min", calendar.previous_session(TARGET[-2]).isoformat()).unlink()
    gc = _gc()
    shortlists, _ = ib.shortlist_history(gc.screener, gc.cfg, TARGET[-2:])
    ratio = ib.coverage_of(ib._pairs(shortlists))["ratio"]
    assert 0 < ratio < 1
    monkeypatch.setattr(config, "INTRADAY_MIN_COVERAGE", ratio)
    assert ib.run_single_1m(gc, TARGET[-2], TARGET[-1])["metrics"]["coverage"]["ratio"] == ratio
    monkeypatch.setattr(config, "INTRADAY_MIN_COVERAGE", ratio + 1e-9)
    with pytest.raises(ib.MissingIntradayData):
        ib.run_single_1m(gc, TARGET[-2], TARGET[-1])


@pytest.mark.parametrize("platform, raw, mb", [("darwin", 512 * 2**20, 512.0),
                                               ("linux", 512 * 1024, 512.0)])
def test_maxrss_is_converted_per_platform(monkeypatch, platform, raw, mb):
    from types import SimpleNamespace

    monkeypatch.setattr(ib.sys, "platform", platform)
    monkeypatch.setattr(ib.resource, "getrusage", lambda who: SimpleNamespace(ru_maxrss=raw))
    assert ib._maxrss_mb() == mb
