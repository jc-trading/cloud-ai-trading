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

import pandas as pd

from quant.data import calendar, corporate_actions, edgar, panel, securities
from quant.engine import screener


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
