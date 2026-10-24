"""Run a group's screener against the panel — the I/O half of the screener.

``run_screen`` loads exactly what ``quant.engine.screener`` needs for one
session (the eligible codes, the panel window, point-in-time share facts and
cached splits) and returns its ScreenResult. It refuses a session whose panel
was not marked complete rather than screen on a partial night.

  python -m quant.data.screen --spec '<json>' [--date YYYY-MM-DD] [--explain AAPL,MSFT]

The CLI is read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from quant import config
from quant.data import calendar, corporate_actions, edgar, panel, securities
from quant.engine import screener

GAP_REASONS = ("lookback", "live_incomplete", "no_panel", "low_coverage", "over_cap")


class PanelIncomplete(RuntimeError):
    pass


def window_sessions(as_of: date, n: int) -> list[date]:
    days = calendar.sessions_in_range(as_of - timedelta(days=2 * n + 30), as_of)
    return days[-n:]


def _shares(spec, codes, as_of: date) -> pd.Series:
    if not any(r.field == "market_cap" for r in spec.rules):
        return pd.Series(dtype="float64")
    cik_map = {c: k for c, k in securities.cik_by_code().items() if c in set(codes)}
    facts = edgar.load_facts(cik_map.values())
    return screener.shares_as_of(facts, corporate_actions.load_all_splits(), cik_map,
                                 as_of)


def _features(spec, as_of: date, codes: list[str], types: dict[str, str],
              shares: pd.Series):
    sessions = window_sessions(as_of, screener.required_lookback(spec))
    close, volume, ret = panel.read(codes, sessions)
    return screener.compute_features(spec, as_of, sessions, close, volume, ret,
                                     shares, types)


def run_screen(spec, as_of: date, *, held=()) -> screener.ScreenResult:
    """The group's pool for one session. An include code that is not an active,
    tradable listing of the spec's asset types is left out of the pool and
    reported in ``include_ignored`` instead of being traded blind."""
    if not panel.is_complete(as_of):
        raise PanelIncomplete(f"panel not complete for {as_of}")
    codes = securities.eligible_codes(spec.asset_types)
    eligible = set(codes)
    ignored = tuple(c for c in spec.include if c not in eligible)
    spec = replace(spec, include=tuple(c for c in spec.include if c in eligible))
    types = securities.asset_types()
    features = _features(spec, as_of, codes, types, _shares(spec, codes, as_of))
    return replace(screener.apply(spec, features, held, as_of=as_of),
                   include_ignored=ignored)


def explain(spec, as_of: date, codes) -> pd.DataFrame:
    """Per-code feature values and per-rule verdicts, for any codes (eligible
    or not) — the hand-check view behind a pool."""
    codes = sorted({c.upper() for c in codes})
    types = securities.asset_types()
    ciks = securities.cik_by_code()
    shares = _shares(spec, codes, as_of)
    features = _features(spec, as_of, codes, types, shares)
    out = features.copy()
    out.insert(0, "asset_type", [types.get(c) for c in out.index])
    out.insert(1, "cik", [ciks.get(c) for c in out.index])
    out.insert(2, "shares", shares.reindex(out.index).astype("float64"))
    for r in spec.rules:
        verdict = screener.apply(
            screener.ScreenerSpec((r,), (), (), spec.asset_types), features[[r.key]])
        out[f"pass:{r.key}"] = [c in verdict.screened for c in out.index]
    return out


def session_coverage() -> dict[date, float]:
    """Per panel session: codes with a close that day / codes alive that day,
    where a code is alive from its first to its last close anywhere in the
    panel. Mid-history listings and delistings therefore never count as gaps."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    firsts: dict[str, date] = {}
    lasts: dict[str, date] = {}
    have: dict[date, int] = {}
    for path in sorted(config.PANEL_ROOT.glob("[0-9][0-9][0-9][0-9].parquet")):
        t = pq.read_table(path, columns=["date", "code", "close"])
        t = t.filter(pc.is_valid(t["close"]))
        span = t.group_by("code").aggregate([("date", "min"), ("date", "max")]).to_pydict()
        for code, lo, hi in zip(span["code"], span["date_min"], span["date_max"]):
            if code not in firsts or lo < firsts[code]:
                firsts[code] = lo
            if code not in lasts or hi > lasts[code]:
                lasts[code] = hi
        counts = t.group_by("date").aggregate([("code", "count")]).to_pydict()
        for d, n in zip(counts["date"], counts["code_count"]):
            have[d] = have.get(d, 0) + n
    first_arr = np.sort(np.array(list(firsts.values()), dtype="datetime64[D]"))
    last_arr = np.sort(np.array(list(lasts.values()), dtype="datetime64[D]"))
    out: dict[date, float] = {}
    for d in sorted(have):
        x = np.datetime64(d, "D")
        alive = int(np.searchsorted(first_arr, x, "right") - np.searchsorted(last_arr, x, "left"))
        if alive > 0:
            out[d] = have[d] / alive
    return out


def panel_first_session() -> date | None:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    paths = sorted(config.PANEL_ROOT.glob("[0-9][0-9][0-9][0-9].parquet"))
    if not paths:
        return None
    t = pq.read_table(paths[0], columns=["date", "close"])
    return pc.min(t.filter(pc.is_valid(t["close"]))["date"]).as_py()


def earliest_start(spec) -> date | None:
    first = panel_first_session()
    if first is None:
        return None
    return calendar.next_session(calendar.advance(first, screener.required_lookback(spec) - 1))


def screen_history(spec, as_ofs, *, progress=lambda *_: None, check=lambda: None
                   ) -> tuple[dict[date, frozenset[str] | None], dict[date, str]]:
    """Historical pools for many sessions with ``run_screen``'s exact math.
    A session ``_sessions.json`` flags is judged as live judges it; an
    unflagged one screens only when its panel coverage reaches
    BACKTEST_PANEL_MIN_COVERAGE. Gap sessions map to None in the pools and to
    a GAP_REASONS entry in the gaps. Panel windows are read one year at a
    time; securities, share facts and splits are loaded once."""
    flags = panel.load_sessions()
    coverage = session_coverage()
    first = min(coverage) if coverage else None
    codes = securities.eligible_codes(spec.asset_types)
    eligible = set(codes)
    spec = replace(spec, include=tuple(c for c in spec.include if c in eligible))
    types = securities.asset_types()
    lookback = screener.required_lookback(spec)
    with_mcap = any(r.field == "market_cap" for r in spec.rules)
    if with_mcap:
        cik_map = {c: k for c, k in securities.cik_by_code().items() if c in eligible}
        facts = edgar.load_facts(cik_map.values())
        splits = corporate_actions.load_all_splits()
    pools: dict[date, frozenset[str] | None] = {}
    gaps: dict[date, str] = {}
    todo: list[date] = []
    for d in sorted(set(as_ofs)):
        flag = flags.get(str(d))
        if first is None:
            gaps[d] = "no_panel"
        elif window_sessions(d, lookback)[0] < first:
            gaps[d] = "lookback"
        elif flag is False:
            gaps[d] = "live_incomplete"
        elif flag is None and coverage.get(d) is None:
            gaps[d] = "no_panel"
        elif flag is None and coverage[d] < config.BACKTEST_PANEL_MIN_COVERAGE:
            gaps[d] = "low_coverage"
        else:
            todo.append(d)
            continue
        pools[d] = None
    for year in sorted({d.year for d in todo}):
        days = [d for d in todo if d.year == year]
        close, volume, ret = panel.read(
            codes, calendar.sessions_in_range(window_sessions(days[0], lookback)[0], days[-1]))
        for d in days:
            check()
            w = window_sessions(d, lookback)
            shares = (screener.shares_as_of(facts, splits, cik_map, d) if with_mcap
                      else pd.Series(dtype="float64"))
            f = screener.compute_features(spec, d, w, close.loc[w], volume.loc[w], ret.loc[w],
                                          shares, types)
            pool = screener.apply(spec, f, (), as_of=d).pool
            if len(pool) > config.SCREEN_POOL_MAX:
                gaps[d] = "over_cap"
                pools[d] = None
            else:
                pools[d] = frozenset(pool)
            progress(f"screen {d}: pool {len(pool)}")
        del close, volume, ret
    return dict(sorted(pools.items())), dict(sorted(gaps.items()))


def _rank_spec(field: str, window: int):
    return screener.parse_spec({"rules": [{"field": field, "window": window, "op": ">=",
                                           "value": 0}]})


def rank_values(field: str, window: int, as_of: date, codes) -> pd.Series:
    """One screener feature for ``codes`` on ``as_of`` with ``run_screen``'s math
    — the 1m shortlist rank."""
    spec1 = _rank_spec(field, window)
    f = _features(spec1, as_of, list(codes), securities.asset_types(), pd.Series(dtype="float64"))
    return f[spec1.rules[0].key]


def rank_history(field: str, window: int, as_ofs, codes_by_as_of) -> dict[date, pd.Series]:
    """``rank_values`` for many sessions, reading the panel one year at a time."""
    spec1 = _rank_spec(field, window)
    key = spec1.rules[0].key
    lookback = screener.required_lookback(spec1)
    types = securities.asset_types()
    empty = pd.Series(dtype="float64")
    out: dict[date, pd.Series] = {}
    days_all = sorted(set(as_ofs))
    for year in sorted({d.year for d in days_all}):
        days = [d for d in days_all if d.year == year]
        codes = sorted(set().union(*(codes_by_as_of[d] for d in days)))
        if not codes:
            out.update({d: empty.rename(key) for d in days})
            continue
        close, volume, ret = panel.read(
            codes, calendar.sessions_in_range(window_sessions(days[0], lookback)[0], days[-1]))
        for d in days:
            w = window_sessions(d, lookback)
            f = screener.compute_features(spec1, d, w, close.loc[w], volume.loc[w], ret.loc[w],
                                          empty, types)
            out[d] = f[key].loc[sorted({c.upper() for c in codes_by_as_of[d]})]
        del close, volume, ret
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a screener spec against the panel")
    ap.add_argument("--spec", required=True, help="screener_json as a JSON string")
    ap.add_argument("--date", default=None,
                    help="session to screen (default: latest complete panel session)")
    ap.add_argument("--explain", default=None, help="comma-separated codes to explain")
    args = ap.parse_args(argv)

    spec = screener.parse_spec(json.loads(args.spec))
    if spec is None:
        print("screener_json is {} — the legacy v1 pool, nothing to screen")
        return 2
    as_of = date.fromisoformat(args.date) if args.date else panel.latest_complete()
    if as_of is None:
        print("no complete panel session yet")
        return 1
    res = run_screen(spec, as_of)
    print(f"as_of={res.as_of} evaluated={res.evaluated}")
    for s in res.rule_stats:
        window = f"({s.window})" if s.window is not None else ""
        print(f"  rule[{s.index}] {s.field}{window} {s.op} {s.value}: passed={s.passed} "
              f"null={s.null} cumulative={s.passed_cumulative}")
    print(f"screened={len(res.screened)} include_added={len(res.include_added)} "
          f"excluded_removed={len(res.excluded_removed)} pool={len(res.pool)}")
    if res.include_ignored:
        print(f"include_ignored (not active+eligible): {', '.join(res.include_ignored)}")
    if args.explain:
        with pd.option_context("display.max_columns", None, "display.width", 200,
                               "display.float_format", "{:.10g}".format):
            print(explain(spec, as_of, args.explain.split(",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
