"""P2 rule screener: spec parsing (every error message verbatim), feature math
against hand computation, NULL handling, pool assembly, point-in-time shares,
and the no-lookahead guarantee."""

import math
import statistics
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.data import calendar
from quant.engine import indicators
from quant.engine.screener import (DEFAULT_SCREENER, SHARE_CONCEPTS, ScreenerSpecError,
                                   apply, compute_features, parse_spec, required_lookback,
                                   shares_as_of)

JIACONG = {"rules": [
    {"field": "market_cap", "op": ">=", "value": 100000000},
    {"field": "adv_usd", "window": 20, "op": ">=", "value": 50000000},
    {"field": "ret_std", "window": 5, "op": ">=", "value": 0.035},
    {"field": "close_vs_ema", "window": 21, "op": ">", "value": 0},
]}

AS_OF = date(2026, 10, 16)
SESSIONS = calendar.sessions_in_range(date(2026, 4, 1), AS_OF)


def _rule(**kw):
    return {"rules": [{"field": "price", "op": ">=", "value": 5, **kw}]}


# --- parse_spec ----------------------------------------------------------------

def test_jiacong_example_parses():
    spec = parse_spec(JIACONG)
    assert [r.key for r in spec.rules] == ["market_cap", "adv_usd:20", "ret_std:5",
                                           "close_vs_ema:21"]
    assert all(r.on_null == "fail" for r in spec.rules)
    assert spec.asset_types == ("stock", "adr", "etf")
    assert spec.include == () and spec.exclude == ()


def test_empty_object_is_the_legacy_pool():
    assert parse_spec({}) is None


def test_default_screener_parses():
    assert parse_spec(DEFAULT_SCREENER).rules[0].key == "market_cap"


def test_include_only_spec_is_valid():
    spec = parse_spec({"include": ["AAPL", "BRK.B"]})
    assert spec.rules == () and spec.include == ("AAPL", "BRK.B")


@pytest.mark.parametrize("obj, message", [
    ([], "screener_json: must be an object"),
    ({"x": 1}, "screener_json: unknown key 'x'"),
    ({"rules": {}}, "screener_json.rules: must be a list"),
    ({"rules": []}, "screener_json: no rules and no include — selects nothing"),
    ({"rules": [], "include": []},
     "screener_json: no rules and no include — selects nothing"),
    ({"rules": [1]}, "screener_json.rules[0]: must be an object"),
    ({"rules": [{"field": "price", "op": ">=", "value": 1, "foo": 1}]},
     "screener_json.rules[0]: unknown key 'foo'"),
    ({"rules": [{"op": ">=", "value": 1}]}, "screener_json.rules[0].field: required"),
    ({"rules": [{"field": "pe", "op": ">=", "value": 1}]},
     "screener_json.rules[0].field: unknown field 'pe'"),
    (_rule(op="=>"), "screener_json.rules[0].op: must be one of >=, <=, >, <, =="),
    (_rule(value=True), "screener_json.rules[0].value: must be a finite number"),
    (_rule(value="5"), "screener_json.rules[0].value: must be a finite number"),
    (_rule(value=float("nan")), "screener_json.rules[0].value: must be a finite number"),
    (_rule(value=float("inf")), "screener_json.rules[0].value: must be a finite number"),
    (_rule(value=-1), "screener_json.rules[0].value: must be >= 0 for price"),
    ({"rules": [{"field": "ret_std", "window": 5, "op": ">=", "value": 2}]},
     "screener_json.rules[0].value: must be within [0, 1] for ret_std"),
    ({"rules": [{"field": "close_vs_ema", "window": 5, "op": ">", "value": -2}]},
     "screener_json.rules[0].value: must be within [-1, 10] for close_vs_ema"),
    (_rule(window=5), "screener_json.rules[0].window: not allowed for price"),
    ({"rules": [_rule()["rules"][0], _rule()["rules"][0],
                {"field": "ret_std", "op": ">=", "value": 0.03}]},
     "screener_json.rules[2].window: required for ret_std"),
    ({"rules": [{"field": "adv_usd", "window": 20.0, "op": ">=", "value": 1}]},
     "screener_json.rules[0].window: must be an integer"),
    ({"rules": [{"field": "adv_usd", "window": True, "op": ">=", "value": 1}]},
     "screener_json.rules[0].window: must be an integer"),
    ({"rules": [{"field": "adv_usd", "window": 251, "op": ">=", "value": 1}]},
     "screener_json.rules[0].window: must be within [1, 250] for adv_usd"),
    ({"rules": [{"field": "ret_std", "window": 1, "op": ">=", "value": 0.1}]},
     "screener_json.rules[0].window: must be within [2, 250] for ret_std"),
    ({"rules": [{"field": "close_vs_ema", "window": 61, "op": ">", "value": 0}]},
     "screener_json.rules[0].window: must be within [2, 60] for close_vs_ema"),
    (_rule(on_null="skip"), "screener_json.rules[0].on_null: must be 'fail' or 'pass'"),
    ({"rules": [_rule()["rules"][0]] * 21}, "screener_json.rules: at most 20 rules"),
    ({"include": "AAPL"}, "screener_json.include: must be a list"),
    ({"include": ["aapl"]}, "screener_json.include[0]: invalid symbol 'aapl'"),
    ({"include": ["AAPL", "1AB"]}, "screener_json.include[1]: invalid symbol '1AB'"),
    ({"include": ["AAPL", "AAPL"]}, "screener_json.include: duplicate symbol 'AAPL'"),
    ({"include": [f"A{i}" for i in range(501)]},
     "screener_json.include: at most 500 symbols"),
    ({**_rule(), "exclude": ["msft"]}, "screener_json.exclude[0]: invalid symbol 'msft'"),
    ({"include": ["AAPL"], "exclude": ["AAPL"]},
     "screener_json: 'AAPL' is in both include and exclude"),
    ({**_rule(), "asset_types": []}, "screener_json.asset_types: must be a non-empty list"),
    ({**_rule(), "asset_types": ["bond"]},
     "screener_json.asset_types[0]: unknown asset type 'bond'"),
    ({**_rule(), "asset_types": ["etf", "etf"]},
     "screener_json.asset_types: duplicate asset type 'etf'"),
])
def test_parse_errors_are_verbatim(obj, message):
    with pytest.raises(ScreenerSpecError) as e:
        parse_spec(obj)
    assert str(e.value) == message


def test_spec_error_is_a_value_error():
    assert issubclass(ScreenerSpecError, ValueError)


def test_required_lookback_takes_the_widest_rule():
    assert required_lookback(parse_spec(JIACONG)) == 84          # ema 4 x 21
    assert required_lookback(parse_spec(
        {"rules": [{"field": "ret_std", "window": 30, "op": ">=", "value": 0}]})) == 31
    assert required_lookback(parse_spec(
        {"rules": [{"field": "adv_usd", "window": 60, "op": ">=", "value": 0}]})) == 60
    assert required_lookback(parse_spec({"include": ["AAPL"]})) == 1
    assert required_lookback(parse_spec(DEFAULT_SCREENER)) == 1


# --- feature math ----------------------------------------------------------------

def _frames(codes=("AAA", "BBB"), n=120, seed=7):
    """Synthetic panel: RAW close/volume and the adjusted return; BBB has a 2:1
    split mid-window so its RAW close halves while the adjusted return does not."""
    rng = np.random.default_rng(seed)
    days = SESSIONS[-n:]
    close, volume, ret, adj = {}, {}, {}, {}
    for j, code in enumerate(codes):
        r = rng.normal(0.001, 0.02 + 0.01 * j, n)
        a = 50.0 * (1 + j) * np.cumprod(1 + r)
        raw = a.copy()
        if code == "BBB":
            raw[: n // 2] *= 2.0                # pre-split RAW prices are double
        adj[code] = pd.Series(a, index=days)
        close[code] = pd.Series(raw, index=days)
        volume[code] = pd.Series(rng.integers(1_000_000, 5_000_000, n).astype(float),
                                 index=days)
        ret[code] = adj[code].pct_change()
    return (days, pd.DataFrame(close), pd.DataFrame(volume), pd.DataFrame(ret),
            pd.DataFrame(adj))


def _spec(*rules):
    return parse_spec({"rules": list(rules)})


def _features(spec, days, close, volume, ret, shares=None, types=None):
    codes = list(close.columns)
    shares = shares if shares is not None else pd.Series(1e6, index=codes)
    types = types if types is not None else {c: "stock" for c in codes}
    return compute_features(spec, days[-1], days, close, volume, ret, shares, types)


def test_price_is_the_raw_close():
    days, close, volume, ret, _ = _frames()
    f = _features(_spec({"field": "price", "op": ">", "value": 0}), days, close, volume, ret)
    assert f.loc["BBB", "price"] == close["BBB"].iloc[-1]


def test_adv_usd_is_the_raw_dollar_volume_mean():
    days, close, volume, ret, _ = _frames()
    f = _features(_spec({"field": "adv_usd", "window": 20, "op": ">", "value": 0}),
                  days, close, volume, ret)
    for code in ("AAA", "BBB"):
        c, v = list(close[code])[-20:], list(volume[code])[-20:]
        expected = sum(x * y for x, y in zip(c, v)) / 20
        assert f.loc[code, "adv_usd:20"] == pytest.approx(expected, rel=1e-12)


def test_ret_std_is_the_sample_std_of_adjusted_returns():
    days, close, volume, ret, adj = _frames()
    f = _features(_spec({"field": "ret_std", "window": 5, "op": ">", "value": 0}),
                  days, close, volume, ret)
    for code in ("AAA", "BBB"):
        a = list(adj[code])[-6:]
        rets = [a[i] / a[i - 1] - 1 for i in range(1, 6)]
        assert f.loc[code, "ret_std:5"] == pytest.approx(statistics.stdev(rets), rel=1e-12)
        assert statistics.stdev(rets) != pytest.approx(statistics.pstdev(rets), rel=1e-6)


def test_ret_std_is_a_decimal_not_a_percent():
    days, close, volume, ret, _ = _frames()
    f = _features(_spec({"field": "ret_std", "window": 20, "op": ">", "value": 0}),
                  days, close, volume, ret)
    assert 0.005 < f.loc["AAA", "ret_std:20"] < 0.1


def _manual_ema(xs, n):
    alpha = 2.0 / (n + 1)
    e = xs[0]
    for x in xs[1:]:
        e = alpha * x + (1 - alpha) * e
    return e


def test_close_vs_ema_matches_indicators_ema_on_the_last_4n():
    days, close, volume, ret, adj = _frames()
    n = 21
    f = _features(_spec({"field": "close_vs_ema", "window": n, "op": ">", "value": -1}),
                  days, close, volume, ret)
    for code in ("AAA", "BBB"):
        series = adj[code]
        ema = indicators.ema(series.tail(4 * n), n).iloc[-1]
        expected = series.iloc[-1] / ema - 1
        assert f.loc[code, f"close_vs_ema:{n}"] == pytest.approx(expected, rel=1e-12)
        tail = list(series.tail(4 * n))
        assert expected == pytest.approx(tail[-1] / _manual_ema(tail, n) - 1, rel=1e-12)


def test_market_cap_is_shares_times_raw_close_and_null_for_etf_adr():
    days, close, volume, ret, _ = _frames(codes=("AAA", "BBB", "SPY"))
    shares = pd.Series({"AAA": 2e6, "BBB": np.nan, "SPY": 9e9})
    f = _features(_spec({"field": "market_cap", "op": ">", "value": 0}), days, close,
                  volume, ret, shares=shares,
                  types={"AAA": "stock", "BBB": "adr", "SPY": "etf"})
    assert f.loc["AAA", "market_cap"] == pytest.approx(2e6 * close["AAA"].iloc[-1],
                                                       rel=1e-12)
    assert math.isnan(f.loc["BBB", "market_cap"])
    assert math.isnan(f.loc["SPY", "market_cap"])


def test_adr_with_shares_is_still_null_market_cap():
    days, close, volume, ret, _ = _frames()
    f = _features(_spec({"field": "market_cap", "op": ">", "value": 0}), days, close,
                  volume, ret, shares=pd.Series({"AAA": 1e6, "BBB": 1e6}),
                  types={"AAA": "adr", "BBB": "stock"})
    assert math.isnan(f.loc["AAA", "market_cap"])
    assert f.loc["BBB", "market_cap"] > 0


@pytest.mark.parametrize("rule, key, gap_back", [
    ({"field": "adv_usd", "window": 20, "op": ">", "value": 0}, "adv_usd:20", 19),
    ({"field": "ret_std", "window": 5, "op": ">", "value": 0}, "ret_std:5", 5),
    ({"field": "close_vs_ema", "window": 10, "op": ">", "value": -1}, "close_vs_ema:10",
     39),
])
def test_a_missing_bar_inside_the_window_is_null(rule, key, gap_back):
    days, close, volume, ret, _ = _frames()
    gap = days[-1 - gap_back]
    close.loc[gap, "AAA"] = np.nan
    volume.loc[gap, "AAA"] = np.nan
    ret.loc[gap, "AAA"] = np.nan
    f = _features(_spec(rule), days, close, volume, ret)
    assert math.isnan(f.loc["AAA", key])
    assert not math.isnan(f.loc["BBB", key])


def test_a_gap_just_outside_the_window_is_ignored():
    days, close, volume, ret, _ = _frames()
    gap = days[-21]
    close.loc[gap, "AAA"] = np.nan
    volume.loc[gap, "AAA"] = np.nan
    f = _features(_spec({"field": "adv_usd", "window": 20, "op": ">", "value": 0}),
                  days, close, volume, ret)
    assert not math.isnan(f.loc["AAA", "adv_usd:20"])


def test_a_session_missing_from_the_frame_is_null_not_skipped():
    days, close, volume, ret, _ = _frames()
    dropped = days[-3]
    f = _features(_spec({"field": "adv_usd", "window": 5, "op": ">", "value": 0}),
                  days, close.drop(index=dropped), volume.drop(index=dropped),
                  ret.drop(index=dropped))
    assert f["adv_usd:5"].isna().all()


def test_short_history_is_null():
    days, close, volume, ret, _ = _frames(n=30)
    f = _features(_spec({"field": "close_vs_ema", "window": 21, "op": ">", "value": 0}),
                  days, close, volume, ret)
    assert f["close_vs_ema:21"].isna().all()


def test_no_lookahead_rows_after_as_of_change_nothing():
    days, close, volume, ret, _ = _frames()
    spec = parse_spec(JIACONG)
    shares = pd.Series({"AAA": 5e6, "BBB": 5e6})
    types = {"AAA": "stock", "BBB": "stock"}
    as_of = days[-10]
    base = compute_features(spec, as_of, days, close.loc[:as_of], volume.loc[:as_of],
                            ret.loc[:as_of], shares, types)
    future = compute_features(spec, as_of, days, close, volume, ret, shares, types)
    pd.testing.assert_frame_equal(base, future)


# --- apply ---------------------------------------------------------------------------

def _feat(rows):
    return pd.DataFrame(rows).T.astype("float64")


def test_apply_counts_each_rule_independently_and_cumulatively():
    spec = _spec({"field": "price", "op": ">=", "value": 10},
                 {"field": "market_cap", "op": ">=", "value": 100})
    f = _feat({"A": {"price": 20, "market_cap": 50}, "B": {"price": 5, "market_cap": 500},
               "C": {"price": 30, "market_cap": 300}, "D": {"price": 1, "market_cap": 1}})
    res = apply(spec, f)
    assert [(s.passed, s.null, s.passed_cumulative) for s in res.rule_stats] == [
        (2, 0, 2), (2, 0, 1)]
    assert res.screened == ("C",) and res.pool == ("C",) and res.evaluated == 4


def test_on_null_fail_and_pass():
    f = _feat({"A": {"market_cap": np.nan}, "B": {"market_cap": 1e9}})
    fail = apply(_spec({"field": "market_cap", "op": ">=", "value": 1e8}), f)
    keep = apply(_spec({"field": "market_cap", "op": ">=", "value": 1e8,
                        "on_null": "pass"}), f)
    assert fail.pool == ("B",) and fail.rule_stats[0].null == 1
    assert keep.pool == ("A", "B") and keep.rule_stats[0].passed == 2


@pytest.mark.parametrize("op, expected", [(">=", ("B", "C")), ("<=", ("A", "B")),
                                           (">", ("C",)), ("<", ("A",)), ("==", ("B",))])
def test_every_operator(op, expected):
    f = _feat({"A": {"price": 1}, "B": {"price": 2}, "C": {"price": 3}})
    assert apply(_spec({"field": "price", "op": op, "value": 2}), f).pool == expected


def test_include_exclude_held_order():
    spec = parse_spec({"rules": [{"field": "price", "op": ">=", "value": 10}],
                       "include": ["INC", "A"], "exclude": ["B", "HX"]})
    f = _feat({"A": {"price": 20}, "B": {"price": 20}, "C": {"price": 1},
               "INC": {"price": 1}})
    res = apply(spec, f, held=("HELD", "HX", "C"))
    assert res.screened == ("A", "B")
    assert res.include_added == ("INC",)
    assert res.excluded_removed == ("B",)
    assert res.pool == ("A", "C", "HELD", "INC")


def test_include_only_spec_pools_only_the_include_list():
    f = _feat({"A": {"price": 20}, "B": {"price": 20}})
    res = apply(parse_spec({"include": ["Z"]}), f, held=("H",))
    assert res.screened == () and res.pool == ("H", "Z")


def test_impossible_threshold_gives_an_empty_pool():
    f = _feat({"A": {"market_cap": 3e12}, "B": {"market_cap": 1e9}})
    assert apply(_spec({"field": "market_cap", "op": ">=", "value": 1e15}), f).pool == ()


# --- shares_as_of ------------------------------------------------------------------

def _facts(rows):
    return pd.DataFrame(rows, columns=["cik", "concept", "end_date", "filed", "value"])


DEI, GAAP = ("dei:EntityCommonStockSharesOutstanding",
             "us-gaap:CommonStockSharesOutstanding")
WAB = "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic"
NO_SPLITS = pd.DataFrame(columns=["symbol", "ex_date", "ratio"])


def test_shares_ignore_facts_filed_after_as_of():
    facts = _facts([(1, DEI, "2026-06-30", "2026-07-20", 100),
                    (1, DEI, "2026-09-30", "2026-10-25", 999)])
    s = shares_as_of(facts, NO_SPLITS, {"AAA": 1}, date(2026, 10, 16))
    assert s["AAA"] == 100


def test_shares_take_the_latest_period_end_then_dei_then_latest_filing():
    facts = _facts([(1, GAAP, "2026-06-30", "2026-07-20", 200),
                    (1, DEI, "2026-03-31", "2026-04-20", 100),
                    (2, GAAP, "2026-06-30", "2026-07-20", 200),
                    (2, DEI, "2026-06-30", "2026-07-21", 300),
                    (3, DEI, "2026-06-30", "2026-07-20", 400),
                    (3, DEI, "2026-06-30", "2026-08-01", 410)])
    s = shares_as_of(facts, NO_SPLITS, {"A": 1, "B": 2, "C": 3}, date(2026, 10, 16))
    assert (s["A"], s["B"], s["C"]) == (200, 300, 410)


def test_shares_older_than_400_days_are_null():
    as_of = date(2026, 10, 16)
    facts = _facts([(1, DEI, as_of - timedelta(days=401), "2025-10-01", 100),
                    (2, DEI, as_of - timedelta(days=400), "2025-10-01", 100)])
    s = shares_as_of(facts, NO_SPLITS, {"OLD": 1, "EDGE": 2, "NOCIK": None}, as_of)
    assert math.isnan(s["OLD"]) and s["EDGE"] == 100 and math.isnan(s["NOCIK"])


def test_shares_scale_by_splits_after_the_period_end():
    facts = _facts([(1, DEI, "2026-06-30", "2026-07-20", 1_000_000),
                    (2, DEI, "2026-06-30", "2026-07-20", 1_000_000)])
    splits = pd.DataFrame([("FWD", "2026-08-10", 4.0),
                           ("FWD", "2026-06-01", 2.0),       # before end: already in
                           ("REV", "2026-09-01", 0.1),       # 1:10 reverse
                           ("REV", "2026-10-20", 0.5)],      # after as_of: not yet
                          columns=["symbol", "ex_date", "ratio"])
    s = shares_as_of(facts, splits, {"FWD": 1, "REV": 2}, date(2026, 10, 16))
    assert s["FWD"] == 4_000_000
    assert s["REV"] == pytest.approx(100_000)


def test_multi_class_codes_share_the_company_total():
    facts = _facts([(1652044, GAAP, "2026-06-30", "2026-07-25", 12_230_000_000)])
    s = shares_as_of(facts, NO_SPLITS, {"GOOGL": 1652044, "GOOG": 1652044},
                     date(2026, 10, 16))
    assert s["GOOGL"] == s["GOOG"] == 12_230_000_000


@pytest.mark.parametrize("form, null", [("20-F", True), ("6-K", True), ("20-F/A", True),
                                        ("6-K/A", True), ("40-F", False), ("40-F/A", False),
                                        ("10-K", False), ("10-Q", False), (None, False)])
def test_foreign_issuer_share_counts_are_null(form, null):
    facts = pd.DataFrame([(1, DEI, "2026-06-30", "2026-07-20", 5_000_000_000, form)],
                         columns=["cik", "concept", "end_date", "filed", "value", "form"])
    s = shares_as_of(facts, NO_SPLITS, {"TSM": 1}, date(2026, 10, 16))
    assert math.isnan(s["TSM"]) is null


def test_price_on_as_of_with_a_missing_bar_is_null_not_carried():
    days, close, volume, ret, _ = _frames()
    close.loc[days[-1], "AAA"] = np.nan
    f = _features(_spec({"field": "price", "op": ">", "value": 0},
                        {"field": "market_cap", "op": ">", "value": 0}),
                  days, close, volume, ret)
    assert math.isnan(f.loc["AAA", "price"]) and math.isnan(f.loc["AAA", "market_cap"])
    assert f.loc["BBB", "price"] == close["BBB"].iloc[-1]


def test_us_gaap_counts_are_not_split_adjusted_twice():
    """A 2:1 split between the period end and the filing: the us-gaap
    statement count is already restated, the dei cover count is not."""
    facts = pd.DataFrame([(1, GAAP, "2026-06-30", "2026-08-01", 2_000_000),
                          (2, DEI, "2026-06-30", "2026-08-01", 1_000_000)],
                         columns=["cik", "concept", "end_date", "filed", "value"])
    splits = pd.DataFrame([("G", "2026-07-15", 2.0), ("D", "2026-07-15", 2.0),
                           ("G", "2026-09-01", 3.0)],
                          columns=["symbol", "ex_date", "ratio"])
    s = shares_as_of(facts, splits, {"G": 1, "D": 2}, date(2026, 10, 16))
    assert s["G"] == 6_000_000          # only the post-filing 3:1
    assert s["D"] == 2_000_000          # the 2:1 after the cover date


def test_share_concepts_rank_dei_then_us_gaap_then_weighted_average():
    assert SHARE_CONCEPTS == (DEI, GAAP, WAB)


def test_weighted_average_loses_a_tie_but_wins_a_later_period_end():
    facts = _facts([(1, WAB, "2026-06-30", "2026-07-20", 250),
                    (1, GAAP, "2026-06-30", "2026-07-20", 200),
                    (2, DEI, "2026-03-31", "2026-04-20", 100),
                    (2, WAB, "2026-06-30", "2026-07-20", 250)])
    s = shares_as_of(facts, NO_SPLITS, {"A": 1, "B": 2}, date(2026, 10, 16))
    assert (s["A"], s["B"]) == (200, 250)


def test_weighted_average_is_split_adjusted_from_its_filing_date():
    facts = _facts([(1, WAB, "2026-06-30", "2026-08-01", 2_000_000)])
    splits = pd.DataFrame([("W", "2026-07-15", 2.0), ("W", "2026-09-01", 3.0)],
                          columns=["symbol", "ex_date", "ratio"])
    s = shares_as_of(facts, splits, {"W": 1}, date(2026, 10, 16))
    assert s["W"] == 6_000_000


def test_weighted_average_filed_after_as_of_is_unknown():
    facts = _facts([(1, WAB, "2026-09-30", "2026-10-17", 2_000_000)])
    s = shares_as_of(facts, NO_SPLITS, {"W": 1}, date(2026, 10, 16))
    assert math.isnan(s["W"])
