"""P5a: 1min need lists, RTH day reads, the rate-limited SIP backfill with its
disk / budget guards, and the shortlist rank features (no network, no PG)."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from quant import config
from quant.data import calendar, intraday_data, screen, store
from quant.engine import screener
from quant.tests.test_backfill_intraday import FakeProvider, _minutes
from quant.tests.test_screen_history import DAYS, _market, _securities

NOW = pd.Timestamp("2026-10-23 12:00", tz="UTC")
S1, S2, S3 = date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)


def _row(reg, sym, day, provider, status="ok"):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reg.upsert(sym, "1min", day.isoformat(), path="x", provider=provider, row_count=1,
               first_ts=ts, last_ts=ts, checksum="x", fetched_at=ts, status=status)


def _provider(*syms_days):
    frames = {}
    for sym, day in syms_days:
        frames[sym] = pd.concat([frames.get(sym, _minutes(day.isoformat(), 0)),
                                 _minutes(day.isoformat(), 390)], ignore_index=True)
    return FakeProvider(frames)


def _fill(pairs, reg, provider, **kw):
    kw.setdefault("sleep", lambda s: None)
    return intraday_data.backfill_sessions(pairs, provider=provider, registry=reg, now=NOW, **kw)


def test_need_pairs_add_the_warmup_sessions():
    pairs = intraday_data.need_pairs({S3: ["AAA"]})
    assert pairs == {("AAA", S3), ("AAA", S2), ("AAA", S1)}
    assert intraday_data.completed_sessions_before(S3, 2) == [S1, S2]
    assert intraday_data.need_pairs({S3: ["AAA"]}, warmup=0) == {("AAA", S3)}


@pytest.mark.parametrize("status", ["ok", "partial", "stale"])
def test_a_sip_file_is_present_whatever_its_status(tmp_store, status):
    _row(tmp_store, "AAA", S1, "alpaca:sip", status)
    provider = _provider()
    stats = _fill({("AAA", S1)}, tmp_store, provider)
    assert stats["skipped"] == 1 and stats["requests"] == 0 and provider.calls == []
    assert intraday_data.missing_pairs({("AAA", S1)}, registry=tmp_store)["present"] == [
        ("AAA", S1)]


def test_an_iex_file_is_reported_not_overwritten(tmp_store):
    _row(tmp_store, "AAA", S1, "alpaca:iex")
    stats = _fill({("AAA", S1)}, tmp_store, _provider(("AAA", S1)))
    assert stats["iex_uncorrected"] == [["AAA", "2026-10-21"]] and stats["requests"] == 0
    assert tmp_store.get_row("AAA", "1min", "2026-10-21").provider == "alpaca:iex"
    assert intraday_data.missing_pairs({("AAA", S1), ("BBB", S1)}, registry=tmp_store) == {
        "present": [], "missing": [("BBB", S1)], "iex_uncorrected": [("AAA", S1)]}


def test_an_unfinished_session_is_left_remaining(tmp_store):
    stats = _fill({("AAA", S3), ("AAA", S2)}, tmp_store, _provider(("AAA", S2)))
    assert stats["remaining"] == 1 and stats["written"] == [["AAA", "2026-10-22"]]
    assert stats["done"] is False


def test_written_pairs_land_in_the_store_as_sip(tmp_store):
    stats = _fill({("AAA", S1), ("BBB", S2)}, tmp_store, _provider(("AAA", S1), ("BBB", S2)))
    assert stats["written"] == [["AAA", "2026-10-21"], ["BBB", "2026-10-22"]]
    assert stats["done"] is True and stats["requests"] == 2
    assert tmp_store.get_row("AAA", "1min", "2026-10-21").provider == "alpaca:sip"
    again = _fill({("AAA", S1), ("BBB", S2)}, tmp_store, _provider())
    assert again["skipped"] == 2 and again["requests"] == 0


def test_rate_limit_sleeps_out_each_request(tmp_store):
    days = calendar.sessions_in_range(date(2026, 9, 1), date(2026, 10, 22))[-10:]
    slept = []
    stats = _fill({("AAA", d) for d in days}, tmp_store, _provider(*[("AAA", d) for d in days]),
                  rpm=60, sleep=slept.append, clock=lambda: 0.0)
    assert stats["requests"] == 10 and sum(slept) >= 9


def test_deadline_cuts_the_run_and_counts_the_rest(tmp_store):
    days = calendar.sessions_in_range(date(2026, 9, 1), date(2026, 10, 22))[-6:]
    ticks = iter(range(0, 10_000, 100))
    stats = _fill({("AAA", d) for d in days}, tmp_store, _provider(*[("AAA", d) for d in days]),
                  deadline_s=250, clock=lambda: float(next(ticks)))
    assert 0 < stats["requests"] < 6
    assert stats["requests"] + stats["remaining"] == 6 and stats["done"] is False


def test_low_disk_fails_closed_before_any_request(tmp_store, monkeypatch):
    monkeypatch.setattr(intraday_data.shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=1e12, used=1e12, free=5e9))
    provider = _provider(("AAA", S1))
    stats = _fill({("AAA", S1)}, tmp_store, provider)
    assert stats["fail_closed"] == "disk" and stats["requests"] == 0 and provider.calls == []


def test_over_budget_store_fails_closed(tmp_store, monkeypatch):
    _fill({("AAA", S1)}, tmp_store, _provider(("AAA", S1)))
    size = store.bar_path("AAA", "1min", "2026-10-21").stat().st_size
    assert intraday_data.store_1min_bytes() == size
    monkeypatch.setattr(config, "INTRADAY_STORE_MAX_GB", size / 2e9)
    provider = _provider(("AAA", S2))
    stats = _fill({("AAA", S2)}, tmp_store, provider)
    assert stats["fail_closed"] == "budget" and provider.calls == []


def test_an_empty_page_is_counted_and_writes_nothing(tmp_store):
    stats = _fill({("ZZZ", S1)}, tmp_store, _provider())
    assert stats["empty"] == [["ZZZ", "2026-10-21"]] and stats["written"] == []
    assert not store.bar_path("ZZZ", "1min", "2026-10-21").exists()


def test_a_failing_fetch_is_isolated(tmp_store):
    provider = _provider(("BBB", S1))
    provider.errors = {"AAA"}
    stats = _fill({("AAA", S1), ("BBB", S1)}, tmp_store, provider)
    assert len(stats["failed"]) == 1 and stats["failed"][0].startswith("AAA 2026-10-21")
    assert stats["written"] == [["BBB", "2026-10-21"]] and stats["done"] is False


def test_day_bars_keep_rth_only_and_report_the_provider(tmp_store):
    df = _minutes("2026-10-21", 16 * 60, start="04:00")
    store.write_bars("AAA", "1min", "2026-10-21", df, provider="alpaca:sip")
    got = intraday_data.day_bars("AAA", S1)
    o, c = calendar.rth_bounds(S1)
    assert len(got) == 390 and got["ts"].min() == o and got["ts"].max() == c - pd.Timedelta("1min")
    assert got.attrs["provider"] == "alpaca:sip"
    missing = intraday_data.day_bars("AAA", S2)
    assert missing.empty and missing.attrs["provider"] is None
    assert list(missing.columns) == list(config.BAR_COLUMNS)


def test_prior_rth_concatenates_the_previous_sessions(tmp_store):
    for d in (S1, S2):
        store.write_bars("AAA", "1min", d.isoformat(), _minutes(d.isoformat(), 390),
                         provider="alpaca:sip")
    got = intraday_data.prior_rth("AAA", S3, 2)
    assert len(got) == 780 and got["ts"].is_monotonic_increasing
    assert intraday_data.prior_rth("ZZZ", S3, 2).empty


def test_daily_adv_needs_twenty_sessions(tmp_store):
    days = calendar.sessions_in_range(date(2026, 9, 1), date(2026, 10, 22))
    ts = [pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC") for d in days]
    df = pd.DataFrame({"ts": ts, "open": 10.0, "high": 10.0, "low": 10.0,
                       "close": [10.0 + i for i in range(len(days))], "volume": 100.0,
                       "vwap": 10.0, "trade_count": 1})
    store.write_frame("AAA", "daily", df, provider="alpaca:sip")
    tail = df.tail(20)
    assert intraday_data.daily_adv("AAA", S2) == pytest.approx(float((tail.close * 100.0).mean()))
    assert intraday_data.daily_adv("AAA", date(2026, 9, 15)) is None


# --- rank features ------------------------------------------------------------

@pytest.fixture
def world(tmp_store, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PANEL_ROOT", tmp_path / "panel")
    return tmp_path


RANK_SPEC = {"rules": [{"field": "ret_std", "window": 5, "op": ">=", "value": 0}]}


def test_rank_values_equal_the_screener_feature(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    d = DAYS[30]
    codes = ["AAA", "BBB", "CCC", "SPY", "NOPE"]
    got = screen.rank_values("ret_std", 5, d, codes)
    want = screen.explain(screener.parse_spec(RANK_SPEC), d, codes)["ret_std:5"]
    pd.testing.assert_series_equal(got, want, check_names=False)
    assert pd.isna(got["NOPE"]) and got["BBB"] > 0


def test_rank_history_equals_rank_values_day_by_day(world, monkeypatch):
    _market()
    _securities(monkeypatch)
    as_ofs = [DAYS[10], DAYS[35], DAYS[-3]]
    codes = {DAYS[10]: ["AAA", "BBB"], DAYS[35]: ["BBB", "CCC", "NOPE"], DAYS[-3]: ["SPY"]}
    hist = screen.rank_history("ret_std", 5, as_ofs, codes)
    assert list(hist) == sorted(as_ofs)
    for d in as_ofs:
        pd.testing.assert_series_equal(hist[d], screen.rank_values("ret_std", 5, d, codes[d]))
    assert screen.rank_history("ret_std", 5, [DAYS[10]], {DAYS[10]: []})[DAYS[10]].empty


# --- CLI ------------------------------------------------------------------------

@pytest.mark.parametrize("stats, rc, suffix", [
    ({"fail_closed": None, "failed": [], "remaining": 0}, 0, "done"),
    ({"fail_closed": None, "failed": [], "remaining": 3}, 1, "partial"),
    ({"fail_closed": "disk", "failed": [], "remaining": 0}, 1, "partial"),
    ({"fail_closed": None, "failed": ["AAA x"], "remaining": 0}, 1, "partial"),
])
def test_cli_writes_done_or_partial_and_exits_accordingly(tmp_path, monkeypatch, capsys,
                                                         stats, rc, suffix):
    need = tmp_path / "bt-1.parquet"
    pd.DataFrame({"symbol": ["AAA"], "session": [S1]}).to_parquet(need, index=False)
    seen = {}

    def fake(pairs, **kw):
        seen["pairs"], seen["kw"] = pairs, kw
        return stats
    monkeypatch.setattr(intraday_data, "backfill_sessions", fake)
    assert intraday_data.main(["--need", str(need), "--minutes", "2"]) == rc
    out = tmp_path / f"bt-1.{suffix}.json"
    assert out.exists() and json.loads(out.read_text()) == stats
    assert seen["pairs"] == {("AAA", S1)} and seen["kw"]["deadline_s"] == 120
    assert capsys.readouterr().out.startswith("STATS ")
    assert not (tmp_path / f"bt-1.{'partial' if suffix == 'done' else 'done'}.json").exists()
