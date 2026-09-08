"""Alpaca IEX WebSocket bar source — thin ``RealtimeBarSource`` adapter.

Subscribes ONLY the ``bars`` channel (``updatedBars`` / ``dailyBars`` would
double-write) and hands each message on as a closed 1-minute ``Bar``.
``update_subscription`` adds/removes symbols on the LIVE connection (Phase 5);
reconnect supervision and heartbeats belong to the consumer, not here.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

import pandas as pd

from quant.data.providers.alpaca_rest import _keys
from quant.data.providers.base import Bar, StreamAuthError

logger = logging.getLogger(__name__)

# Alpaca's free IEX stream accepts at most 30 symbols per connection.
MAX_STREAM_SYMBOLS = 30

# alpaca-py raises auth rejections as a plain ValueError off the handshake, so
# 401/403 can only be told apart from a transient drop by its message
_AUTH_MARKERS = ("auth failed", "failed to authenticate", "not authenticated",
                 "unauthorized", "forbidden", "401", "403")


def is_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


class SymbolCapExceeded(ValueError):
    pass


def to_bar(msg) -> Bar:
    """Convert one alpaca-py stream bar message into the canonical Bar."""
    return Bar(
        symbol=str(msg.symbol).upper(),
        ts=pd.Timestamp(msg.timestamp).tz_convert("UTC")
        if pd.Timestamp(msg.timestamp).tzinfo else pd.Timestamp(msg.timestamp, tz="UTC"),
        open=float(msg.open),
        high=float(msg.high),
        low=float(msg.low),
        close=float(msg.close),
        volume=float(msg.volume),
        vwap=None if msg.vwap is None else float(msg.vwap),
        trade_count=None if msg.trade_count is None else float(msg.trade_count),
    )


def _stream():
    from alpaca.data.enums import DataFeed
    from alpaca.data.live import StockDataStream

    key, sec = _keys()
    return StockDataStream(key, sec, feed=DataFeed.IEX)


class AlpacaWsSource:
    provider_key = "alpaca:iex"

    def __init__(self, stream=None) -> None:
        self._stream = stream
        self._symbols: list[str] = []
        self._on_bar: Callable[[Bar], None] | None = None
        self._running = False
        self._auth_error: BaseException | None = None

    def _checked(self, symbols: Iterable[str]) -> list[str]:
        syms = [s.upper() for s in symbols]
        if len(syms) > MAX_STREAM_SYMBOLS:
            logger.error("stream symbol cap %d exceeded by %d symbols: %s",
                         MAX_STREAM_SYMBOLS, len(syms) - MAX_STREAM_SYMBOLS,
                         ",".join(syms[MAX_STREAM_SYMBOLS:]))
            raise SymbolCapExceeded(
                f"{len(syms)} symbols requested, IEX stream caps at {MAX_STREAM_SYMBOLS}")
        return syms

    def subscribe(self, symbols: Iterable[str]) -> None:
        self._symbols = self._checked(symbols)

    def update_subscription(self, symbols: Iterable[str]) -> tuple[list[str], list[str]]:
        """Move the live subscription to ``symbols``, returning (added, removed).

        Symbols are added/removed on the open connection: reconnecting to change
        the set would drop the minute currently being assembled, and the 30-symbol
        cap is counted per connection so a second socket is not an option.
        """
        syms = self._checked(symbols)
        added = [s for s in syms if s not in self._symbols]
        removed = [s for s in self._symbols if s not in syms]
        self._symbols = syms
        if self._running and self._stream is not None:
            if removed:
                self._stream.unsubscribe_bars(*removed)
            if added:
                self._stream.subscribe_bars(self._handle_bar, *added)
        return added, removed

    def run(self, on_bar: Callable[[Bar], None]) -> None:
        """Stream until the connection ends, raising StreamAuthError on 401/403.

        alpaca-py's ``_run_forever`` catches everything ``_start_ws`` raises and
        reconnects on a backoff, so a rejected key would otherwise loop for ever
        and the caller could never exit. Wrapping ``_start_ws`` to clear
        ``_should_run`` is the only supported way out of that loop.
        """
        if not self._symbols:
            raise RuntimeError("subscribe() before run()")
        self._on_bar = on_bar
        stream = self._stream or _stream()
        self._stream = stream
        stream.subscribe_bars(self._handle_bar, *self._symbols)
        self._running = True
        self._auth_error = None
        start_ws = getattr(stream, "_start_ws", None)
        if start_ws is None:
            logger.warning("stream exposes no _start_ws — an auth rejection will "
                           "retry forever instead of exiting")

        async def guarded_start_ws():
            try:
                await start_ws()
            except ValueError as exc:
                if is_auth_error(exc):
                    self._auth_error = exc
                    stream._should_run = False
                raise

        if start_ws is not None:
            stream._start_ws = guarded_start_ws
        try:
            stream.run()
        finally:
            if start_ws is not None:
                stream._start_ws = start_ws
            self._running = False
        if self._auth_error is not None:
            raise StreamAuthError(str(self._auth_error))

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception:
            logger.debug("stream stop() failed", exc_info=True)
        finally:
            self._running = False

    async def _handle_bar(self, msg) -> None:
        if self._on_bar is None:
            return
        self._on_bar(to_bar(msg))
