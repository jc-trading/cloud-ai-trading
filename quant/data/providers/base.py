"""Provider abstraction for bar data — the seam between CAT and any market data
vendor (方案 Phase 1).

Two protocols:
  * ``BarProvider``      — historical / corrective pulls, returns closed bars only
  * ``RealtimeBarSource``— intraday stream, emits CLOSED 1-minute ``Bar`` objects

The realtime contract is deliberately "closed 1-min bar", not "tick": a provider
that only streams trades aggregates inside its own module, so the stream
consumer, the store and the EOD correction never learn the difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Protocol, runtime_checkable

import pandas as pd

from quant import config

TIMEFRAMES: tuple[str, ...] = config.TIMEFRAMES


class StreamAuthError(RuntimeError):
    """A realtime source rejected the credentials (401/403). Never retried —
    the consumer exits non-zero so the operator fixes the keys."""


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trade_count: float | None = None


@runtime_checkable
class BarProvider(Protocol):
    provider_key: str

    def supports(self, timeframe: str) -> bool: ...

    def fetch_bars(self, symbol: str, timeframe: str, start: datetime,
                   end: datetime) -> pd.DataFrame:
        """Closed bars for one symbol as a config.BAR_COLUMNS frame, ts UTC."""
        ...


@runtime_checkable
class RealtimeBarSource(Protocol):
    provider_key: str

    def subscribe(self, symbols: Iterable[str]) -> None: ...

    def update_subscription(self, symbols: Iterable[str]) -> tuple[list[str], list[str]]:
        """Diff the live subscription in place, returning (added, removed)."""
        ...

    def run(self, on_bar: Callable[[Bar], None]) -> None: ...
