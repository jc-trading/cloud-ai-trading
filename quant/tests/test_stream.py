"""Phase 5 WebSocket consumer — subscription resolution, the minute flush, the
ET-date boundaries, restart/reconnect recovery and gap filling, all against a
fake RealtimeBarSource + fake registry (no network, no PostgreSQL)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from quant import config
from quant.data import store, stream
from quant.data.providers.base import Bar
from quant.data.registry import StreamSymbol, SystemAccount
from quant.data.stream import StreamConsumer
from quant.tests.conftest import FakeRegistry

ET = "America/New_York"
DAY = "2026-09-08"        # Tuesday, an XNYS session
NEXT_DAY = "2026-09-09"


def _et(day: str, hhmm: str) -> datetime:
    return pd.Timestamp(f"{day} {hhmm}", tz=ET).tz_convert("UTC").to_pydatetime()


def _bar(symbol: str, ts: datetime, close: float = 100.0) -> Bar:
    return Bar(symbol=symbol, ts=pd.Timestamp(ts).tz_convert("UTC"), open=close,
               high=close + 1, low=close - 1, close=close, volume=1000.0,
               vwap=close, trade_count=10.0)


def _rest_frame(day: str, times: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [pd.Timestamp(f"{day} {t}", tz=ET).tz_convert("UTC") for t in times],
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
        "vwap": 1.2, "trade_count": 3.0,
    })


class StreamRegistry(FakeRegistry):
    def __init__(self, *, positions=(), configured=(), watchlist=(), account=True):
        super().__init__()
        self.positions = list(positions)
        self.configured = list(configured)
        self.watchlist = list(watchlist)
        self.account = SystemAccount(account_id="acct", user_id="user") if account else None
        self.beats: list[tuple[str, dict]] = []

    def system_account(self):
        return self.account

    def open_position_symbols(self, account_id):
        return list(self.positions)

    def watchlist_symbols(self, user_id):
        return list(self.watchlist)

    def stream_symbols(self):
        return [StreamSymbol(symbol=s, priority=i, note=None)
                for i, s in enumerate(self.configured)]

    def beat(self, name, meta=None):
        self.beats.append((name, meta or {}))


class FakeSource:
    provider_key = "alpaca:iex"

    def __init__(self, script=None):
        self.script = script
        self.symbols: list[str] = []
        self.subscribed: list[list[str]] = []
        self.updates: list[tuple[list[str], list[str]]] = []
        self.runs = 0
        self.stops = 0

    def subscribe(self, symbols):
        self.symbols = list(symbols)
        self.subscribed.append(list(symbols))

    def update_subscription(self, symbols):
        symbols = list(symbols)
        added = [s for s in symbols if s not in self.symbols]
        removed = [s for s in self.symbols if s not in symbols]
        self.symbols = symbols
        self.updates.append((added, removed))
        return added, removed

    def run(self, on_bar):
        self.runs += 1
        if self.script is not None:
            self.script(self, on_bar)

    def stop(self):
        self.stops += 1


class FakeRest:
    provider_key = "alpaca:iex"

    def __init__(self, frames=None):
        self.frames = frames or {}
        self.calls: list[tuple] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, client=None, now=None):
        self.calls.append((symbol, timeframe, start, end))
        return self.frames.get(symbol,
                               pd.DataFrame(columns=list(config.BAR_COLUMNS)))


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def bars_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BARS_ROOT", tmp_path / "stock-market-data")
    return tmp_path


def _consumer(registry, clock, *, source_factory=None, rest=None) -> StreamConsumer:
    return StreamConsumer(
        source_factory=source_factory or (lambda: FakeSource()),
        rest=rest if rest is not None else FakeRest(),
        registry=registry, clock=clock, backoff_s=0.0, max_backoff_s=0.0,
        tick_seconds=0.01)


def _subscribe(c: StreamConsumer, rest: FakeRest | None = None) -> None:
    """Take the initial subscription, then forget the startup gap sweep it runs
    so a gap test starts from a clean slate."""
    c.refresh_subscriptions()
    c._known_empty.clear()
    c._buf.clear()
    c._dirty.clear()
    if rest is not None:
        rest.calls.clear()


def _stored(symbol: str, day: str) -> pd.DataFrame:
    path = store.bar_path(symbol, "1min", day)
    if not path.exists():
        return pd.DataFrame(columns=list(config.BAR_COLUMNS))
    return store.normalize(pd.read_parquet(path))


# --- subscription set ------------------------------------------------------

def test_subscription_priority_order_and_dedupe(bars_root):
    reg = StreamRegistry(positions=["HELD", "SHARED"],
                         configured=["SHARED", "CFG"], watchlist=["WL", "HELD"])
    c = _consumer(reg, Clock(_et(DAY, "10:00")))
    assert c.resolve_symbols() == ["HELD", "SHARED", "CFG", "WL"]


def test_subscription_truncates_at_thirty_and_names_the_dropped(bars_root, caplog):
    reg = StreamRegistry(positions=["HELD"],
                         configured=[f"C{i:02d}" for i in range(40)],
                         watchlist=["WL"])
    c = _consumer(reg, Clock(_et(DAY, "10:00")))
    with caplog.at_level("WARNING"):
        got = c.resolve_symbols()
    assert len(got) == stream.MAX_STREAM_SYMBOLS
    assert got[0] == "HELD" and got[1] == "C00"
    assert "WL" not in got and "C39" not in got
    assert "C39" in caplog.text and "WL" in caplog.text


def test_subscription_without_system_account_uses_configured_only(bars_root):
    reg = StreamRegistry(configured=["CFG"], account=False)
    assert _consumer(reg, Clock(_et(DAY, "10:00"))).resolve_symbols() == ["CFG"]


# --- minute flush ----------------------------------------------------------

def test_minute_flush_writes_the_day_file(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "10:02"))
    c = _consumer(reg, clock)
    c.refresh_subscriptions()

    c.on_bar(_bar("AAA", _et(DAY, "10:00"), 10.0))
    c.on_bar(_bar("AAA", _et(DAY, "10:01"), 11.0))
    assert c.flush() == 1

    df = _stored("AAA", DAY)
    assert len(df) == 2
    assert list(df["close"]) == [10.0, 11.0]
    row = reg.get_row("AAA", "1min", DAY)
    assert row.provider == "alpaca:iex" and row.row_count == 2
    assert store.bar_path("AAA", "1min", DAY).exists()


def test_flush_is_a_noop_without_new_bars(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "10:02")))
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "10:00")))
    assert c.flush() == 1
    assert c.flush() == 0


def test_last_minute_of_the_session_still_lands_after_2000_et(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "19:59"))
    c = _consumer(reg, clock)
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "19:58"), 42.0))

    clock.now = _et(DAY, "20:30")   # the 19:59 bar is delivered after the close
    assert c.flush() == 1
    assert list(_stored("AAA", DAY)["close"]) == [42.0]


def test_no_write_once_the_et_date_has_rolled(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "19:59"))
    c = _consumer(reg, clock)
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "19:58")))

    clock.now = _et(NEXT_DAY, "00:30")
    assert c.flush() == 0
    assert not store.bar_path("AAA", "1min", DAY).exists()


def test_out_of_window_bar_is_not_written(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "21:00")))
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "20:30")))   # same ET date, past the close
    assert c.flush() == 0


def test_wrong_date_bar_is_dropped(bars_root, caplog):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "10:02")))
    c.refresh_subscriptions()
    with caplog.at_level("WARNING"):
        c.on_bar(_bar("AAA", _et(NEXT_DAY, "10:00")))
    assert c.flush() == 0
    assert "not the current trading date" in caplog.text


# --- ET date rollover ------------------------------------------------------

def test_date_rollover_switches_the_target_file(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "19:00"))
    c = _consumer(reg, clock)
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "18:59"), 10.0))
    c.flush()

    clock.now = _et(NEXT_DAY, "10:00")
    assert c.roll_date() is True
    c.on_bar(_bar("AAA", _et(NEXT_DAY, "09:59"), 20.0))
    c.flush()

    assert list(_stored("AAA", DAY)["close"]) == [10.0]
    assert list(_stored("AAA", NEXT_DAY)["close"]) == [20.0]


# --- restart ---------------------------------------------------------------

def test_restart_reloads_the_day_buffer_without_losing_rows(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "10:03"))
    first = _consumer(reg, clock)
    first.refresh_subscriptions()
    for i, minute in enumerate(["10:00", "10:01", "10:02"]):
        first.on_bar(_bar("AAA", _et(DAY, minute), 10.0 + i))
    first.flush()

    restarted = _consumer(reg, clock)
    restarted.refresh_subscriptions()
    assert restarted.reload_buffers() == 3
    restarted.on_bar(_bar("AAA", _et(DAY, "10:03"), 13.0))
    restarted.flush()

    df = _stored("AAA", DAY)
    assert len(df) == 4
    assert list(df["close"]) == [10.0, 11.0, 12.0, 13.0]


# --- gap recovery ----------------------------------------------------------

def test_missing_minutes_covers_interior_holes_not_just_the_tail(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "09:35"))
    c = _consumer(reg, clock)
    c.refresh_subscriptions()
    # a silent alpaca-py reconnect leaves a hole at 09:31-09:33, inside the day
    for minute in ["09:30", "09:34"]:
        c.on_bar(_bar("AAA", _et(DAY, minute)))
    c._known_empty["AAA"] = {
        pd.Timestamp(f"{DAY} {t}", tz=ET).tz_convert("UTC")
        for t in [f"{h:02d}:{m:02d}" for h in range(4, 10) for m in range(60)]
        if t < "09:30"}

    missing = c.missing_minutes("AAA")
    assert [m.tz_convert(ET).strftime("%H:%M") for m in missing] == \
        ["09:31", "09:32", "09:33"]


def test_missing_minutes_stops_at_the_last_closed_minute(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "04:05")))
    _subscribe(c)
    c.on_bar(_bar("AAA", _et(DAY, "04:00")))
    assert [m.tz_convert(ET).strftime("%H:%M") for m in c.missing_minutes("AAA")] == \
        ["04:01", "04:02", "04:03", "04:04"]


def test_minutes_rest_reports_empty_are_not_requested_twice(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest({"AAA": _rest_frame(DAY, ["04:01"])})
    clock = Clock(_et(DAY, "04:06"))
    c = _consumer(reg, clock, rest=rest)
    _subscribe(c, rest)

    assert c.fill_gaps() == 1
    # 04:00/04:02/04:03 are settled and genuinely trade-less; the last two minutes
    # are inside the REST settle window and stay eligible
    assert [m.tz_convert(ET).strftime("%H:%M") for m in c.missing_minutes("AAA")] == \
        ["04:04", "04:05"]
    rest.frames = {}
    c.fill_gaps()
    assert len(rest.calls) == 2
    assert pd.Timestamp(rest.calls[1][2]).tz_convert(ET).strftime("%H:%M") == "04:04"


def test_missing_minutes_start_at_session_open_when_buffer_is_empty(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "04:03")))
    _subscribe(c)
    missing = c.missing_minutes("AAA")
    assert [m.tz_convert(ET).strftime("%H:%M") for m in missing] == \
        ["04:00", "04:01", "04:02"]


def test_gap_fill_stops_once_the_post_close_grace_has_passed(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest()
    c = _consumer(reg, Clock(_et(DAY, "20:10")), rest=rest)
    c.refresh_subscriptions()
    assert c.fill_gaps() > 0 or rest.calls          # inside the 15-minute grace
    rest.calls.clear()
    c._clock = Clock(_et(DAY, "20:30"))
    assert c.fill_gaps() == 0
    assert rest.calls == []


def test_gap_fill_merges_rest_rows_into_the_day_file(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest({"AAA": _rest_frame(DAY, ["10:01", "10:03"])})
    clock = Clock(_et(DAY, "10:05"))
    c = _consumer(reg, clock, rest=rest)
    _subscribe(c, rest)
    c.on_bar(_bar("AAA", _et(DAY, "10:00")))

    assert c.fill_gaps() == 2
    symbol, timeframe, start, end = rest.calls[0]
    assert (symbol, timeframe) == ("AAA", "1min")
    assert pd.Timestamp(start).tz_convert(ET).strftime("%H:%M") == "04:00"
    assert pd.Timestamp(end).tz_convert(ET).strftime("%H:%M") == "10:05"

    c.flush()
    df = _stored("AAA", DAY)
    assert [t.tz_convert(ET).strftime("%H:%M") for t in df["ts"]] == \
        ["10:00", "10:01", "10:03"]


def test_gap_fill_skips_non_trading_days(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest()
    c = _consumer(reg, Clock(_et("2026-09-12", "10:00")), rest=rest)  # Saturday
    c.refresh_subscriptions()
    assert c.fill_gaps() == 0
    assert rest.calls == []


# --- supervision -----------------------------------------------------------

def test_reconnect_resubscribes_the_full_set(bars_root):
    reg = StreamRegistry(configured=["AAA", "BBB"])
    sources: list[FakeSource] = []

    def factory():
        def script(src, on_bar):
            if len(sources) == 1:
                raise RuntimeError("connection reset")
            consumer.shutdown()
        src = FakeSource(script=script)
        sources.append(src)
        return src

    consumer = _consumer(reg, Clock(_et(DAY, "10:00")), source_factory=factory)
    consumer.run()

    assert len(sources) == 2
    assert [s.subscribed for s in sources] == [[["AAA", "BBB"]], [["AAA", "BBB"]]]
    assert all(s.stops == 1 for s in sources)


def test_auth_failure_exits_without_retrying(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    sources: list[FakeSource] = []

    def factory():
        def script(src, on_bar):
            raise ValueError("auth failed: 403 forbidden")
        src = FakeSource(script=script)
        sources.append(src)
        return src

    c = _consumer(reg, Clock(_et(DAY, "10:00")), source_factory=factory)
    with pytest.raises(stream.StreamAuthError):
        c.run()
    assert len(sources) == 1


def test_main_returns_non_zero_on_auth_failure(monkeypatch):
    class _Boom:
        def run(self):
            raise stream.StreamAuthError("401")

    monkeypatch.setattr(stream, "StreamConsumer", lambda *a, **kw: _Boom())
    assert stream.main([]) == 1


# --- subscription diff / heartbeat ----------------------------------------

def test_subscription_diff_updates_the_live_connection(bars_root):
    reg = StreamRegistry(configured=["AAA", "BBB"])
    source = FakeSource()
    c = _consumer(reg, Clock(_et(DAY, "10:00")), source_factory=lambda: source)
    c.refresh_subscriptions()
    c._source = source
    source.subscribe(["AAA", "BBB"])

    reg.configured = ["BBB", "CCC"]
    added, removed = c.refresh_subscriptions()
    assert (added, removed) == (["CCC"], ["AAA"])
    assert source.updates == [(["CCC"], ["AAA"])]
    assert source.symbols == ["BBB", "CCC"]


def test_heartbeat_written_even_when_idle(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "02:00")))   # before the session opens
    c.refresh_subscriptions()
    c.beat()
    name, meta = reg.beats[-1]
    assert name == "market_stream"
    assert meta["symbols"] == 1 and meta["in_session"] is False
    assert meta["period_key"] == DAY

    c._clock = Clock(_et(DAY, "10:00"))
    c.beat()
    assert reg.beats[-1][1]["in_session"] is True
    c._clock = Clock(_et("2026-09-12", "10:00"))   # Saturday, clock window only
    c.beat()
    assert reg.beats[-1][1]["in_session"] is False


def test_silent_tape_triggers_a_gap_sweep(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest({"AAA": _rest_frame(DAY, ["10:01"])})
    clock = Clock(_et(DAY, "10:02"))
    source = FakeSource()
    c = _consumer(reg, clock, source_factory=lambda: source, rest=rest)
    c.refresh_subscriptions()
    c._source = source
    c.on_bar(_bar("AAA", _et(DAY, "10:00")))

    c.tick()                         # the startup resolve tick sweeps once
    rest.calls.clear()

    clock.now = _et(DAY, "10:02")
    c.tick()
    assert rest.calls == []          # a live tape asks REST for nothing

    clock.now = _et(DAY, "10:06")    # silent past GAP_SILENCE_SECONDS
    c.tick()
    assert len(rest.calls) == 1
    assert sorted(t.tz_convert(ET).strftime("%H:%M") for t in c._frame("AAA")["ts"]) == \
        ["10:00", "10:01"]


# --- QA fix loop 1 regressions --------------------------------------------

def test_readded_symbol_keeps_its_stored_day(bars_root):
    """[92] disable -> re-enable mid-session must not truncate the day file."""
    reg = StreamRegistry(configured=["AAA"])
    clock = Clock(_et(DAY, "10:05"))
    source = FakeSource()
    c = _consumer(reg, clock, source_factory=lambda: source)
    c.refresh_subscriptions()
    c._source = source
    for i, minute in enumerate(["10:00", "10:01", "10:02"]):
        c.on_bar(_bar("AAA", _et(DAY, minute), 10.0 + i))
    c.flush()
    assert len(_stored("AAA", DAY)) == 3

    reg.configured = []
    c.refresh_subscriptions()
    assert c._buf.get("AAA") is None

    reg.configured = ["AAA"]
    added, _ = c.refresh_subscriptions()
    assert added == ["AAA"]
    c.on_bar(_bar("AAA", _et(DAY, "10:03"), 13.0))
    c.flush()

    assert list(_stored("AAA", DAY)["close"]) == [10.0, 11.0, 12.0, 13.0]


def test_resolve_tick_runs_a_gap_sweep(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    rest = FakeRest()
    source = FakeSource()
    clock = Clock(_et(DAY, "10:00"))
    c = _consumer(reg, clock, source_factory=lambda: source, rest=rest)
    c.refresh_subscriptions()
    c._source = source
    c.tick()
    calls = len(rest.calls)
    clock.now = _et(DAY, "10:06")
    c._last_bar_at = clock.now          # silence trigger disarmed on purpose
    c.tick()
    assert len(rest.calls) > calls


def test_final_flush_writes_the_buffer_on_shutdown(bars_root):
    reg = StreamRegistry(configured=["AAA"])
    c = _consumer(reg, Clock(_et(DAY, "20:05")))
    c.refresh_subscriptions()
    c.on_bar(_bar("AAA", _et(DAY, "19:59"), 7.0))
    assert c.final_flush() == 1
    assert list(_stored("AAA", DAY)["close"]) == [7.0]


def test_preflight_rejects_bad_credentials_before_connecting(bars_root):
    class _Denied:
        def fetch_bars(self, *a, **kw):
            raise RuntimeError("APIError: 403 forbidden")

    reg = StreamRegistry(configured=["AAA"])
    sources: list[FakeSource] = []
    c = _consumer(reg, Clock(_et(DAY, "10:00")), rest=_Denied(),
                  source_factory=lambda: sources.append(FakeSource()) or sources[-1])
    with pytest.raises(stream.StreamAuthError):
        c.run()
    assert sources == []


def test_preflight_tolerates_a_network_error(bars_root, caplog):
    class _Down:
        def fetch_bars(self, *a, **kw):
            raise ConnectionError("name resolution failed")

    c = _consumer(StreamRegistry(configured=["AAA"]), Clock(_et(DAY, "10:00")),
                  rest=_Down())
    with caplog.at_level("WARNING"):
        c.preflight_credentials()
    assert "pre-flight inconclusive" in caplog.text
