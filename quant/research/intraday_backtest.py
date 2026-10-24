"""1m strategy-group backtests: one run or a session walk-forward over 1min bars.

Each session S trades the shortlist ranked from ``screen(previous_session(S))``
— the same function the nightly watch list will call — through the shared
minute simulator, from the store's SIP day files only (no DB, no network).
A run refuses to start when fewer than INTRADAY_MIN_COVERAGE of the symbol-days
it needs are in the store, and names them so a need-list backfill can fill
them. Every session starts and ends flat, so the equity curve is the closing
cash per session behind a starting-capital anchor on the session before.

    python -m quant.research.intraday_backtest --spec JSON --start D --end D --need-out PATH
    python -m quant.research.intraday_backtest --spec JSON --start D --end D --smoke
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from quant import config
from quant.backtest import calibration, metrics
from quant.backtest.intraday import DayContext, Event, GroupDayState, run_day
from quant.backtest.intraday_costs import IntradayCostModel
from quant.backtest.metrics import Trade
from quant.backtest.walkforward import WFWindow
from quant.data import calendar, intraday_data, screen, store
from quant.engine import trade_rules
from quant.engine.intraday_strategies import ORBConfig, orb_score, rank_pool
from quant.engine.screener import ScreenerSpec, parse_spec
from quant.engine.strategies import M1_ORB_KEY, get_spec, validate_grid
from quant.engine.trade_rules import IntradayTradeSettings
from quant.research import group_backtest, r09

COST_MODEL_LABEL = "IntradayCostModel (uncalibrated)"
_MINUTE = pd.Timedelta(minutes=1)


class MissingIntradayData(RuntimeError):
    def __init__(self, pairs):
        self.pairs = sorted(pairs)
        super().__init__(f"missing 1min data: {len(self.pairs)} symbol-days")


@dataclass(frozen=True)
class IntradayGroupConfig:
    strategy_key: str
    cfg: ORBConfig
    screener: ScreenerSpec
    starting_capital: float
    platform: dict
    trade: IntradayTradeSettings
    score_floor: float | None = None

    @classmethod
    def from_snapshot(cls, snap: dict) -> IntradayGroupConfig:
        base = dict(snap.get("trade_settings_base") or {})
        ref = snap.get("calibration_ref")
        if ref:
            base["entry_prob_min"] = ref["entry_prob_min"]
            base["calibration_run_id"] = ref["run_id"]
        return cls(snap["strategy_key"], get_spec(snap["strategy_key"]).build(snap["params"]),
                   parse_spec(snap["screener"]), float(snap["starting_capital"]),
                   dict(snap["platform"]),
                   trade=trade_rules.parse_trade_settings(base, timeframe="1m")
                   or IntradayTradeSettings(),
                   score_floor=(ref or {}).get("score_floor"))


@dataclass(frozen=True)
class IntradayResult:
    equity: pd.Series
    trades: list[Trade]
    scores: list[float]
    events: list[Event]
    dropped: int


def shortlist_for_session(spec: ScreenerSpec, cfg: ORBConfig, S: date) -> list[tuple[str, float]]:
    """Session S's ranked watch list: S's previous-session pool by ret_std
    descending, ties by symbol. The nightly watch task must call this."""
    as_of = calendar.previous_session(S)
    pool = screen.run_screen(spec, as_of).pool
    if not pool:
        return []
    return rank_pool(screen.rank_values("ret_std", cfg.rank.rank_window, as_of, pool),
                     cfg.rank.watch_max)


def shortlist_history(spec: ScreenerSpec, cfg: ORBConfig, sessions
                      ) -> tuple[dict[date, list], dict[date, str]]:
    """``shortlist_for_session`` for many sessions with screen_history's
    batching; a gap session gets an empty list and its gap reason."""
    as_of_of = {S: calendar.previous_session(S) for S in sessions}
    pools, gaps = screen.screen_history(spec, sorted(set(as_of_of.values())))
    codes = {a: sorted(p) for a, p in pools.items() if p is not None}
    values = screen.rank_history("ret_std", cfg.rank.rank_window, list(codes), codes)
    out: dict[date, list] = {}
    reasons: dict[date, str] = {}
    for S, a in as_of_of.items():
        if pools[a] is None:
            out[S] = []
            reasons[S] = gaps[a]
        else:
            out[S] = rank_pool(values[a], cfg.rank.watch_max)
    return out, reasons


def pair_present(sym: str, day: date) -> bool:
    path = store.bar_path(sym, intraday_data.TIMEFRAME, day.isoformat())
    return path.exists() and store.parquet_provider(path) == config.PROVIDER_HISTORICAL


def _pairs(shortlists: dict[date, list]) -> set[tuple[str, date]]:
    return intraday_data.need_pairs({S: [s for s, _ in sl] for S, sl in shortlists.items()})


def need_list(gc: IntradayGroupConfig, start, end) -> set[tuple[str, date]]:
    sessions = calendar.sessions_in_range(start, end)
    return _pairs(shortlist_history(gc.screener, gc.cfg, sessions)[0])


def coverage_of(pairs) -> dict:
    present, missing, iex = 0, [], 0
    for sym, d in sorted(pairs):
        if pair_present(sym, d):
            present += 1
            continue
        missing.append((sym, d))
        path = store.bar_path(sym, intraday_data.TIMEFRAME, d.isoformat())
        if path.exists() and store.parquet_provider(path) == config.PROVIDER_REALTIME:
            iex += 1
    needed = len(pairs)
    return {"needed": needed, "present": present, "missing": missing, "iex": iex,
            "ratio": present / needed if needed else 1.0}


def _minute_bars(df: pd.DataFrame) -> dict[pd.Timestamp, dict]:
    return {ts: {"open": o, "high": h, "low": lo, "close": c, "volume": v}
            for ts, o, h, lo, c, v in zip(df["ts"], df["open"].astype("float64"),
                                          df["high"].astype("float64"),
                                          df["low"].astype("float64"),
                                          df["close"].astype("float64"),
                                          df["volume"].astype("float64"))}


def _trades(events: list[Event], day: date) -> tuple[list[Trade], list[float]]:
    trades, scores, open_buys = [], [], {}
    for e in events:
        if e.kind != "fill":
            continue
        if e.side == "buy":
            open_buys[e.symbol] = e
            continue
        buy = open_buys.pop(e.symbol)
        trades.append(Trade(e.symbol, day, day, buy.eff_price, e.eff_price, buy.qty, buy.r_unit,
                            buy.qty * (e.eff_price - buy.eff_price), e.reason))
        scores.append(buy.score)
    return trades, scores


def run_sessions(gc: IntradayGroupConfig, sessions, shortlists: dict[date, list],
                 cfg: ORBConfig | None = None, *, check=lambda: None,
                 progress=lambda *_: None) -> IntradayResult:
    """Simulate the sessions in order, carrying cash; each session reads only
    its shortlisted symbols' day files (a symbol without a SIP file is
    dropped) plus their warmup sessions and previous-session daily ADV."""
    cfg = cfg or gc.cfg
    costs = IntradayCostModel()
    risk_pct = float(gc.platform["per_trade_risk_pct"])
    cash = gc.starting_capital
    closes, trades, scores, events, dropped = [], [], [], [], 0
    for S in sessions:
        check()
        ranked = [s for s, _ in shortlists.get(S, [])]
        watched = [s for s in ranked if pair_present(s, S)]
        dropped += len(ranked) - len(watched)
        prev = calendar.previous_session(S)
        prior = {s: intraday_data.prior_rth(s, S, config.INTRADAY_WARMUP_SESSIONS)
                 for s in watched}
        adv = {s: intraday_data.daily_adv(s, prev) for s in watched}
        bars = {s: _minute_bars(intraday_data.day_bars(s, S)) for s in watched}
        rth = calendar.rth_bounds(S)
        state = GroupDayState(S, cash, {}, [], {}, cash)
        ctx = DayContext.from_prior(prior)
        n = int((rth[1] - rth[0]) / _MINUTE)
        inputs = ((t, {s: bars[s][t] for s in watched if t in bars[s]}, watched, "ok", "live")
                  for t in (rth[0] + i * _MINUTE for i in range(n)))
        day_events = run_day(state, ctx, inputs, strategy=orb_score, strategy_cfg=cfg,
                             trade=gc.trade, costs=costs, adv=adv, score_floor=gc.score_floor,
                             shortlist=watched, rth=rth, risk_pct=risk_pct)
        cash = state.cash
        closes.append(cash)
        t_, s_ = _trades(day_events, S)
        trades += t_
        scores += s_
        events += day_events
        progress(f"session {S}: {len(watched)} watched, {len(t_)} trades")
    index = [calendar.previous_session(sessions[0]), *sessions]
    equity = pd.Series([gc.starting_capital, *closes], index=index, dtype="float64")
    return IntradayResult(equity, trades, scores, events, dropped)


def _info(sessions, reasons: dict[date, str], cov: dict, dropped: int) -> dict:
    return {"label": "intraday_shortlist", "biased": True, "sessions": len(sessions),
            "screen_gaps": dict(Counter(reasons.values())), "dropped": dropped,
            "coverage": cov["ratio"]}


def _coverage_json(cov: dict) -> dict:
    return {"needed": cov["needed"], "present": cov["present"], "missing": len(cov["missing"]),
            "iex": cov["iex"], "ratio": cov["ratio"]}


def _prepare(gc: IntradayGroupConfig, sessions):
    shortlists, reasons = shortlist_history(gc.screener, gc.cfg, sessions)
    cov = coverage_of(_pairs(shortlists))
    if cov["ratio"] < config.INTRADAY_MIN_COVERAGE:
        raise MissingIntradayData(cov["missing"])
    if not any(shortlists.values()):
        raise RuntimeError("no screenable session in range")
    return shortlists, reasons, cov


def run_single_1m(gc: IntradayGroupConfig, start, end, *, check=lambda: None,
                  progress=lambda *_: None) -> dict:
    sessions = calendar.sessions_in_range(start, end)
    shortlists, reasons, cov = _prepare(gc, sessions)
    res = run_sessions(gc, sessions, shortlists, check=check, progress=progress)
    info = _info(sessions, reasons, cov, res.dropped)
    return {"kind": "single", "universe": SimpleNamespace(pools=None, info=info),
            "equity": res.equity, "trades": res.trades, "scores": res.scores,
            "shortlists": shortlists,
            "metrics": {"summary": metrics.payload(res.equity, res.trades), "universe": info,
                        "coverage": _coverage_json(cov), "cost_model": COST_MODEL_LABEL}}


def session_windows(sessions, is_n: int, oos_n: int) -> list[WFWindow]:
    sessions = sorted(sessions)
    out: list[WFWindow] = []
    i = 0
    while i + is_n + oos_n <= len(sessions):
        is_s, oos_s = sessions[i:i + is_n], sessions[i + is_n:i + is_n + oos_n]
        out.append(WFWindow(pd.Timestamp(is_s[0]), pd.Timestamp(is_s[-1]),
                            pd.Timestamp(oos_s[0]), pd.Timestamp(oos_s[-1])))
        i += oos_n
    return out


def _between(sessions, a: pd.Timestamp, b: pd.Timestamp) -> list[date]:
    return [s for s in sessions if a.date() <= s <= b.date()]


def _with_knobs(cfg: ORBConfig, knobs: dict) -> ORBConfig:
    return replace(cfg, signal=replace(cfg.signal, **{k.split(".")[1]: v
                                                      for k, v in knobs.items()}))


def _calibrate_window(gc, sessions, shortlists, win, grid, check) -> tuple[dict, dict]:
    """r09's single-pass coordinate descent on the IS sessions, started from
    the group's own signal knobs; each knob combination runs once."""
    is_sessions = _between(sessions, win.is_start, win.is_end)
    cache: dict[tuple, float] = {}

    def evaluate(kv: dict) -> float:
        key = tuple(kv[k] for k in grid)
        if key not in cache:
            cache[key] = r09.objective(run_sessions(gc, is_sessions, shortlists,
                                                    _with_knobs(gc.cfg, kv), check=check))
        return cache[key]

    current = {k: getattr(gc.cfg.signal, k.split(".")[1]) for k in grid}
    best = evaluate(current)
    trail = {"start_score": best, "steps": []}
    for knob, values in grid.items():
        for v in values:
            if v == current[knob]:
                continue
            trial = {**current, knob: v}
            score = evaluate(trial)
            if score > best:
                best, current = score, trial
        trail["steps"].append({knob: current[knob], "score": best})
    trail["end_score"] = best
    trail["runs"] = len(cache)
    return current, trail


def run_walk_forward_1m(gc: IntradayGroupConfig, start, end, *, is_sessions: int,
                        oos_sessions: int, grid=None, check=lambda: None,
                        progress=lambda *_: None) -> dict:
    sessions = calendar.sessions_in_range(start, end)
    windows = session_windows(sessions, is_sessions, oos_sessions)
    if not windows:
        raise ValueError(f"no walk-forward window fits {start}..{end} "
                         f"(IS {is_sessions} + OOS {oos_sessions} sessions)")
    grid = validate_grid(gc.strategy_key, grid)
    shortlists, reasons, cov = _prepare(gc, sessions)

    sel_equities, sel_trades, fix_equities, fix_trades, rows = [], [], [], [], []
    scores, wins, window_ids = [], [], []
    for wi, win in enumerate(windows, 1):
        params, trail = _calibrate_window(gc, sessions, shortlists, win, grid, check)
        oos = _between(sessions, win.oos_start, win.oos_end)
        sel = run_sessions(gc, oos, shortlists, _with_knobs(gc.cfg, params), check=check)
        fix = run_sessions(gc, oos, shortlists, gc.cfg, check=check)
        sel_equities.append(sel.equity)
        sel_trades += sel.trades
        fix_equities.append(fix.equity)
        fix_trades += fix.trades
        for t, s in zip(fix.trades, fix.scores):
            scores.append(s)
            wins.append(t.pnl > 0)
            window_ids.append(wi)
        rows.append({"window": wi, "is": f"{win.is_start.date()}..{win.is_end.date()}",
                     "oos": f"{win.oos_start.date()}..{win.oos_end.date()}",
                     "params": params, "is_trail": trail,
                     "selected": metrics.payload(sel.equity, sel.trades),
                     "fixed": metrics.payload(fix.equity, fix.trades)})
        progress(f"window {wi}/{len(windows)}: selected {params}")
    fixed_equity = group_backtest.stitch(fix_equities, gc.starting_capital)
    selected_equity = group_backtest.stitch(sel_equities, gc.starting_capital)

    finals = [r["fixed"]["final_equity"] for r in rows]
    pfs = [math.inf if r["fixed"]["profit_factor"] is None else r["fixed"]["profit_factor"]
           for r in rows]
    gate = {"windows_positive_pct": sum(1 for f in finals if f > gc.starting_capital) / len(rows),
            "window_pf_median": float(np.median(pfs))}
    cal = calibration.fit(scores, wins, window_ids)
    cal["skipped_no_score"] = 0

    info = _info(sessions, reasons, cov, 0)
    return {"kind": "walk_forward", "universe": SimpleNamespace(pools=None, info=info),
            "equity_fixed": fixed_equity, "equity_selected": selected_equity,
            "trades_fixed": fix_trades, "trades_selected": sel_trades,
            "shortlists": shortlists,
            "metrics": {"fixed": metrics.payload(fixed_equity, fix_trades),
                        "selected": metrics.payload(selected_equity, sel_trades),
                        "windows": rows, "gate": gate, "universe": info,
                        "coverage": _coverage_json(cov), "cost_model": COST_MODEL_LABEL},
            "calibration": cal}


def write_need(path, pairs) -> None:
    rows = sorted(pairs, key=lambda p: (p[1], p[0]))
    pd.DataFrame({"symbol": [s for s, _ in rows], "session": [d for _, d in rows]}).to_parquet(
        Path(path), index=False)


def write_artifacts_1m(dirpath, result: dict) -> None:
    group_backtest.write_artifacts(dirpath, result)
    rows = [(S, rank, sym, score) for S, sl in sorted(result["shortlists"].items())
            for rank, (sym, score) in enumerate(sl, 1)]
    pd.DataFrame(rows, columns=["session", "rank", "symbol", "score"]).to_parquet(
        Path(dirpath) / "shortlists.parquet", index=False)


def _maxrss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 2**20 if sys.platform == "darwin" else rss / 1024


def _smoke_config(spec_json: str) -> IntradayGroupConfig:
    spec = get_spec(M1_ORB_KEY)
    return IntradayGroupConfig(M1_ORB_KEY, spec.defaults, parse_spec(json.loads(spec_json)),
                               2000.0, group_backtest.PLATFORM_CONSTANTS, IntradayTradeSettings())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="1m strategy-group backtests")
    ap.add_argument("--spec", required=True, help="screener_json as a JSON string")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--need-out", default=None)
    mode.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)
    gc = _smoke_config(args.spec)
    try:
        if args.need_out:
            pairs = need_list(gc, args.start, args.end)
            cov = coverage_of(pairs)
            write_need(args.need_out, cov["missing"])
            print(f"NEED needed={cov['needed']} missing={len(cov['missing'])} iex={cov['iex']}")
            return 0
        result = run_single_1m(gc, args.start, args.end,
                               progress=lambda msg: print(msg, flush=True))
        print(f"COVERAGE {result['metrics']['coverage']['ratio']}")
        summary = group_backtest.jsonable(result["metrics"]["summary"])
        print(f"SUMMARY {json.dumps(summary, allow_nan=False)}")
        print(f"TRADES {len(result['trades'])}")
        print(f"MAXRSS_MB {_maxrss_mb():.0f}")
        return 0
    except MissingIntradayData as e:
        print(f"ERROR {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
