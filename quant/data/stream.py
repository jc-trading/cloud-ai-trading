"""Realtime 1-minute bar consumer — the IEX WebSocket writer (方案 Phase 5).

Run as ``python -m quant.data.stream``; the compose service ``market-stream``
does exactly that, as a SINGLE instance (the 30-symbol cap is counted per
connection, and the minute flush rewrites the whole day file — a second writer
would fight over both).

Flow per connection: resolve the subscription set (open positions >
``market_stream_symbols`` > the 对照账户 owner's stock watchlist, truncated to 30
with the dropped names logged) -> reload today's stored bars back into the
in-memory buffer -> fill the minutes missing since the last stored bar over IEX
REST -> stream. Once a minute every symbol that received a bar is rewritten as
the whole day file (``replace=True`` over the day buffer, which is why the
reload has to happen first — an empty buffer would truncate the day). Writing
stops at 20:00 ET, where the EOD SIP correction takes over; the heartbeat keeps
beating regardless so the watchdog can tell "idle" from "dead".
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config
from quant.data import calendar, store
from quant.data.providers import get_historical, get_realtime
from quant.data.providers.alpaca_ws import MAX_STREAM_SYMBOLS, is_auth_error
from quant.data.providers.base import Bar, StreamAuthError
from quant.data.registry import resolve_stream_symbols

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_TIMEFRAME = "1min"
HEARTBEAT_NAME = "market_stream"

# 拍板 2026-09-07: one session window, shared by the WS writer, the REST
# backfill and the EOD correction — extended hours included. Defined ONCE in
# quant.data.calendar; re-exported here for the modules that already read them.
SESSION_OPEN_ET = calendar.SESSION_OPEN_ET
SESSION_CLOSE_ET = calendar.SESSION_CLOSE_ET

FLUSH_SECONDS = 60
HEARTBEAT_SECONDS = 60
RESOLVE_SECONDS = 300
# alpaca-py rides out transient drops inside run() and resubscribes itself, so
# the supervisor below never sees them; a tape that goes quiet this long during
# a session is the only signal that a silent reconnect ate some minutes
GAP_SILENCE_SECONDS = 180
# one last sweep after 20:00 ET picks up the tail of the session, then the day
# is handed to the EOD SIP correction
POST_CLOSE_FILL_GRACE = pd.Timedelta(minutes=15)
# IEX REST can trail the just-closed minute by seconds; only minutes older than
# this are remembered as "genuinely no trades" and never asked for again
REST_SETTLE_MINUTES = 2
TICK_SECONDS = 5
INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0
# a connection that survived this long counts as healthy, so the next drop
# starts backing off from scratch instead of inheriting the old delay
STABLE_SESSION_S = 60.0

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def session_bounds(now) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The extended session window of the ET calendar date of ``now``, in UTC."""
    return calendar.session_bounds(_ts(now).tz_convert(_ET).date())


class StreamConsumer:
    def __init__(self, *, source_factory: Callable[[], object] | None = None,
                 rest=None, registry=None, clock: Callable[[], datetime] = _utcnow,
                 backoff_s: float = INITIAL_BACKOFF_S,
                 max_backoff_s: float = MAX_BACKOFF_S,
                 tick_seconds: float = TICK_SECONDS) -> None:
        self._source_factory = source_factory or get_realtime
        self._rest = rest if rest is not None else get_historical(config.PROVIDER_REALTIME)
        if registry is None:
            from quant.data import registry as registry_module

            registry = registry_module
        self._registry = registry
        self._clock = clock
        self._backoff_s = backoff_s
        self._max_backoff_s = max_backoff_s
        self._tick_seconds = tick_seconds

        self._source = None
        self._symbols: list[str] = []
        self._buf: dict[str, dict[pd.Timestamp, dict]] = {}
        self._dirty: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._period_key = store.period_key(self._clock(), _TIMEFRAME)
        self._next_flush = 0.0
        self._next_beat = 0.0
        self._next_resolve = 0.0
        self._last_bar_at = self._clock()
        self._known_empty: dict[str, set[pd.Timestamp]] = {}

    def _period_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """The session window of the day being written — anchored on
        ``_period_key``, not on the wall clock, so a flush or gap fill that runs
        after 20:00 ET still targets the day it belongs to."""
        return calendar.session_bounds(pd.Timestamp(self._period_key).date())

    # --- subscription set --------------------------------------------------

    def resolve_symbols(self) -> list[str]:
        """Priority-ordered subscription set, truncated to the IEX
        per-connection cap — never silently."""
        ordered, dropped = resolve_stream_symbols(self._registry,
                                                  cap=MAX_STREAM_SYMBOLS)
        if dropped:
            logger.warning(
                "subscription set of %d exceeds the %d-symbol IEX cap — dropping %s",
                len(ordered) + len(dropped), MAX_STREAM_SYMBOLS,
                ",".join(dropped))
        return ordered

    def refresh_subscriptions(self) -> tuple[list[str], list[str]]:
        try:
            wanted = self.resolve_symbols()
        except Exception:
            logger.warning("subscription refresh failed, keeping %d symbols",
                           len(self._symbols), exc_info=True)
            return [], []
        if wanted == self._symbols:
            return [], []
        added = [s for s in wanted if s not in self._symbols]
        removed = [s for s in self._symbols if s not in wanted]
        self.flush()
        with self._lock:
            self._symbols = wanted
            for symbol in removed:
                self._buf.pop(symbol, None)
                self._dirty.discard(symbol)
                self._known_empty.pop(symbol, None)
        # a re-added symbol must get its stored day back BEFORE it can receive a
        # bar, or the next replace=True write truncates the file to that one row
        if added:
            self.reload_buffers(symbols=added)
            self.fill_gaps(symbols=added)
        if self._source is not None:
            self._source.update_subscription(wanted)
        logger.info("subscription diff: +%s -%s (%d subscribed)",
                    ",".join(added) or "-", ",".join(removed) or "-", len(wanted))
        return added, removed

    # --- buffer ------------------------------------------------------------

    def reload_buffers(self, now=None, symbols=None) -> int:
        """Load the day's already-stored bars back into the buffer.

        The minute flush rewrites the whole day file, so a writer that started
        from an empty buffer would overwrite a full session with the handful of
        bars seen since it started (复核 Grok [90]). Called for the whole set at
        startup and for the added symbols on every subscription change."""
        open_ts, close_ts = self._period_bounds()
        # the window is half-open: a 20:00 ET end lands on UTC midnight during
        # EDT, which store.slice_range reads as "through that day" and would
        # spill the next day's file into today's buffer
        end_ts = close_ts - pd.Timedelta(nanoseconds=1)
        targets = list(self._symbols) if symbols is None else list(symbols)
        frames = {s: store.read_bars(s, _TIMEFRAME, open_ts, end_ts) for s in targets}
        loaded = 0
        with self._lock:
            if symbols is None:
                self._buf.clear()
                self._dirty.clear()
                self._known_empty.clear()
            for symbol, df in frames.items():
                self._buf.pop(symbol, None)
                self._dirty.discard(symbol)
                self._known_empty.pop(symbol, None)
                rows = {_ts(r["ts"]): {c: r[c] for c in config.BAR_COLUMNS}
                        for _, r in df.iterrows()}
                if rows:
                    self._buf[symbol] = rows
                    loaded += len(rows)
        logger.info("reloaded %d stored bars for %s across %d symbols",
                    loaded, self._period_key, len(targets))
        return loaded

    def on_bar(self, bar: Bar) -> None:
        key = store.period_key(bar.ts, _TIMEFRAME)
        if key != self._period_key:
            logger.warning("dropping %s bar at %s: ET date %s is not the current "
                           "trading date %s", bar.symbol, bar.ts, key, self._period_key)
            return
        with self._lock:
            self._last_bar_at = self._clock()
            self._buf.setdefault(bar.symbol, {})[_ts(bar.ts)] = {
                "ts": _ts(bar.ts), "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
                "vwap": bar.vwap, "trade_count": bar.trade_count,
            }
            self._dirty.add(bar.symbol)

    def _frame(self, symbol: str) -> pd.DataFrame:
        open_ts, close_ts = self._period_bounds()
        rows = self._buf.get(symbol) or {}
        keep = [v for ts, v in rows.items() if open_ts <= ts < close_ts]
        return pd.DataFrame(keep, columns=list(config.BAR_COLUMNS))

    def flush(self, now=None, *, force: bool = False) -> int:
        """Write every symbol that received bars as the whole day file.

        Gated on the DATA, not on the wall clock: the 19:59 bar and any
        post-close gap fill arrive after 20:00 ET and must still land, so a
        flush stops only once the ET date itself has rolled (``force`` writes
        the finished day out on the way past that boundary)."""
        now = now or self._clock()
        if not force and store.period_key(now, _TIMEFRAME) != self._period_key:
            return 0
        with self._lock:
            pending = sorted(self._dirty)
            self._dirty.clear()
            frames = {s: self._frame(s) for s in pending}
        written = 0
        for symbol, df in frames.items():
            if df.empty:
                continue
            try:
                store.write_bars(symbol, _TIMEFRAME, self._period_key, df,
                                 provider=config.PROVIDER_REALTIME, replace=True,
                                 registry=self._registry)
                written += 1
            except Exception:
                logger.warning("write failed for %s %s", symbol, self._period_key,
                               exc_info=True)
                with self._lock:
                    self._dirty.add(symbol)
        if written:
            logger.info("flushed %d symbols to %s", written, self._period_key)
        return written

    def roll_date(self, now=None) -> bool:
        now = now or self._clock()
        key = store.period_key(now, _TIMEFRAME)
        if key == self._period_key:
            return False
        logger.info("ET trading date rolled %s -> %s", self._period_key, key)
        self.flush(now, force=True)
        self._period_key = key
        self.reload_buffers(now)
        self.fill_gaps(now)
        return True

    # --- gap recovery ------------------------------------------------------

    def missing_minutes(self, symbol: str, now=None) -> list[pd.Timestamp]:
        """Every minute of the session with no bar, from the open to the last
        CLOSED minute.

        Scans the WHOLE window, not just forward of the newest bar: alpaca-py
        reconnects internally without telling the consumer, so a hole opens in
        the middle of the day and a forward-only scan would never see it.
        Minutes REST has already answered "no trades" for are excluded, which is
        what keeps the repeated sweeps cheap."""
        now = _ts(now or self._clock())
        open_ts, close_ts = self._period_bounds()
        have = self._buf.get(symbol) or {}
        empty = self._known_empty.get(symbol) or set()
        end = min(now.floor("min"), close_ts) - pd.Timedelta(minutes=1)
        if end < open_ts:
            return []
        return [ts for ts in pd.date_range(open_ts, end, freq="1min")
                if ts not in have and ts not in empty]

    def _merge(self, symbol: str, df: pd.DataFrame) -> int:
        merged = 0
        with self._lock:
            rows = self._buf.setdefault(symbol, {})
            for _, r in store.normalize(df).iterrows():
                ts = _ts(r["ts"])
                if ts in rows or store.period_key(ts, _TIMEFRAME) != self._period_key:
                    continue
                rows[ts] = {c: r[c] for c in config.BAR_COLUMNS}
                merged += 1
            if merged:
                self._dirty.add(symbol)
        return merged

    def fill_gaps(self, now=None, symbols=None) -> int:
        """Pull the minutes missed while disconnected from IEX REST (no 15-minute
        rule there) and merge them in — same provider, so the day file stays
        single-sourced."""
        now = now or self._clock()
        open_ts, close_ts = self._period_bounds()
        if not calendar.is_trading_day(pd.Timestamp(self._period_key).date()):
            return 0
        if _ts(now) < open_ts or _ts(now) >= close_ts + POST_CLOSE_FILL_GRACE:
            return 0
        settled = _ts(now).floor("min") - pd.Timedelta(minutes=REST_SETTLE_MINUTES)
        merged = 0
        for symbol in (list(self._symbols) if symbols is None else list(symbols)):
            missing = self.missing_minutes(symbol, now)
            if not missing:
                continue
            try:
                df = self._rest.fetch_bars(
                    symbol, _TIMEFRAME, missing[0].to_pydatetime(),
                    (missing[-1] + pd.Timedelta(minutes=1)).to_pydatetime(), now=now)
            except Exception:
                logger.warning("gap fill failed for %s (%d minutes from %s)",
                               symbol, len(missing), missing[0], exc_info=True)
                continue
            merged += self._merge(symbol, df)
            answered = {_ts(t) for t in store.normalize(df)["ts"]}
            self._known_empty.setdefault(symbol, set()).update(
                ts for ts in missing if ts not in answered and ts < settled)
        if merged:
            logger.info("gap fill merged %d bars into %s", merged, self._period_key)
        return merged

    # --- heartbeat ---------------------------------------------------------

    def beat(self, now=None) -> None:
        now = now or self._clock()
        open_ts, close_ts = session_bounds(now)
        in_session = (calendar.is_trading_day(_ts(now).tz_convert(_ET).date())
                      and open_ts <= _ts(now) < close_ts)
        with self._lock:
            buffered = sum(len(v) for v in self._buf.values())
        try:
            self._registry.beat(HEARTBEAT_NAME, {
                "symbols": len(self._symbols),
                "buffered": buffered,
                "period_key": self._period_key,
                "in_session": bool(in_session),
                "connected": self._source is not None,
            })
        except Exception:
            logger.warning("heartbeat write failed", exc_info=True)

    # --- supervision -------------------------------------------------------

    def tick(self, now=None) -> None:
        now = now or self._clock()
        elapsed = _ts(now).timestamp()
        self.roll_date(now)
        if elapsed >= self._next_flush:
            self._next_flush = elapsed + FLUSH_SECONDS
            self.flush(now)
        if elapsed >= self._next_beat:
            self._next_beat = elapsed + HEARTBEAT_SECONDS
            self.beat(now)
        if (self._source is not None
                and (_ts(now) - _ts(self._last_bar_at)).total_seconds() >= GAP_SILENCE_SECONDS):
            self._last_bar_at = now
            self.fill_gaps(now)
        if elapsed >= self._next_resolve:
            self._next_resolve = elapsed + RESOLVE_SECONDS
            if self._source is not None:
                self.refresh_subscriptions()
                self.fill_gaps(now)

    def _tick_loop(self) -> None:
        while not self._stop.wait(self._tick_seconds):
            try:
                self.tick()
            except Exception:
                logger.warning("periodic tick failed", exc_info=True)
        self.final_flush()

    def final_flush(self) -> int:
        """Land the buffered minute and close the socket on shutdown. Runs off
        the tick thread: the main thread is blocked inside the stream's event
        loop and cannot stop it from a signal handler without deadlocking."""
        written = 0
        try:
            written = self.flush(force=True)
        except Exception:
            logger.warning("final flush failed", exc_info=True)
        source = self._source
        stop = getattr(source, "stop", None) if source is not None else None
        if stop is not None:
            stop()
        return written

    def preflight_credentials(self) -> None:
        """Prove the keys over REST before opening the stream.

        alpaca-py's stream loop retries a rejected key for ever, so a 401/403
        must be caught where it surfaces as an ordinary exception; anything else
        (network, rate limit) is left to the reconnect loop."""
        now = self._clock()
        try:
            self._rest.fetch_bars("SPY", _TIMEFRAME,
                                  (_ts(now) - pd.Timedelta(minutes=30)).to_pydatetime(),
                                  _ts(now).to_pydatetime(), now=now)
        except StreamAuthError:
            raise
        except Exception as exc:
            if is_auth_error(exc):
                raise StreamAuthError(str(exc)) from exc
            logger.warning("credential pre-flight inconclusive (%s) — continuing", exc)

    def _session(self) -> None:
        self.preflight_credentials()
        source = self._source_factory()
        symbols = self.resolve_symbols()
        if not symbols:
            raise RuntimeError("subscription set is empty — nothing to stream")
        source.subscribe(symbols)  # a reconnect resubscribes the FULL set
        self._symbols = symbols
        self._source = source
        logger.info("subscribing %d symbols: %s", len(symbols), ",".join(symbols))
        self.reload_buffers()
        self.fill_gaps()
        self._last_bar_at = self._clock()
        try:
            source.run(self.on_bar)
        except StreamAuthError:
            raise
        except Exception as exc:
            if is_auth_error(exc):
                raise StreamAuthError(str(exc)) from exc
            raise
        finally:
            self._source = None
            stop = getattr(source, "stop", None)
            if stop is not None:
                stop()

    def run(self) -> None:
        ticker = threading.Thread(target=self._tick_loop, name="market-stream-tick",
                                  daemon=True)
        ticker.start()
        backoff = self._backoff_s
        try:
            while not self._stop.is_set():
                started = self._clock()
                try:
                    self._session()
                    if not self._stop.is_set():
                        logger.warning("stream ended, reconnecting in %.0fs", backoff)
                except StreamAuthError:
                    raise
                except Exception:
                    logger.warning("stream dropped, reconnecting in %.0fs", backoff,
                                   exc_info=True)
                if (self._clock() - started).total_seconds() >= STABLE_SESSION_S:
                    backoff = self._backoff_s
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self._max_backoff_s)
        finally:
            self._stop.set()
            self.flush(force=True)

    def shutdown(self) -> None:
        self._stop.set()


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    consumer = StreamConsumer()

    def _shutdown(signum, _frame):
        logger.info("signal %s received — flushing and shutting down", signum)
        consumer.shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _shutdown)
    try:
        consumer.run()
    except StreamAuthError as exc:
        logger.error("Alpaca rejected the stream credentials (%s) — check "
                     "ALPACA_API_KEY / ALPACA_API_SECRET; not retrying", exc)
        return 1
    except KeyboardInterrupt:
        consumer.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
