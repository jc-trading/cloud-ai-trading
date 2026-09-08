"""Market data providers — one module per vendor feed, selected by provider key.

Upgrading a feed (e.g. IEX -> SIP streaming) is a config change:
``config.PROVIDER_HISTORICAL`` / ``config.PROVIDER_REALTIME`` name the key,
the factories below resolve it.
"""

from __future__ import annotations

from quant import config
from quant.data.providers.alpaca_rest import AlpacaIexRestProvider, AlpacaRestProvider
from quant.data.providers.alpaca_ws import AlpacaWsSource
from quant.data.providers.base import Bar, BarProvider, RealtimeBarSource, TIMEFRAMES

_HISTORICAL = {AlpacaRestProvider.provider_key: AlpacaRestProvider,
               AlpacaIexRestProvider.provider_key: AlpacaIexRestProvider}
_REALTIME = {AlpacaWsSource.provider_key: AlpacaWsSource}

__all__ = ["Bar", "BarProvider", "RealtimeBarSource", "TIMEFRAMES",
           "AlpacaRestProvider", "AlpacaIexRestProvider", "AlpacaWsSource",
           "get_historical", "get_realtime"]


def get_historical(provider_key: str | None = None) -> BarProvider:
    key = provider_key or config.PROVIDER_HISTORICAL
    try:
        return _HISTORICAL[key]()
    except KeyError:
        raise ValueError(f"unknown historical provider {key!r}") from None


def get_realtime(provider_key: str | None = None) -> RealtimeBarSource:
    key = provider_key or config.PROVIDER_REALTIME
    try:
        return _REALTIME[key]()
    except KeyError:
        raise ValueError(f"unknown realtime provider {key!r}") from None
