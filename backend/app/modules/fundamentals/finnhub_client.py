"""Finnhub REST client for fundamental-analysis data.

Wraps the Finnhub free-tier REST API for the data points Phase 3 FA needs:
  - earnings calendar          (/calendar/earnings)
  - EPS/revenue estimate       (/stock/eps-estimate, /stock/revenue-estimate)
  - EPS/revenue actuals        (/stock/earnings)
  - company news               (/company-news)
  - company profile + metrics  (/stock/profile2, /stock/metric)

Design constraints (see task WIRE-finnhub):
  * Uses httpx with an explicit timeout and error handling.
  * Free API quota is limited — this client never loops/retries aggressively
    and issues exactly one request per public method call.
  * Degrades gracefully: with no API key configured, or on any HTTP / network /
    parse error, methods log and return an empty list / None instead of raising.
    Callers can rely on never getting an exception from this module.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 10.0


class FinnhubClient:
    """Thin, fail-safe wrapper around the Finnhub REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        # Fall back to the configured system key when not explicitly provided.
        self.api_key = api_key if api_key is not None else settings.FINNHUB_API_KEY
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        """True when an API key is available; otherwise the client no-ops."""
        return bool(self.api_key)

    # ---- internal ---------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> Optional[Any]:
        """Issue a single GET request. Returns parsed JSON or None on any failure.

        Never raises: a missing key, timeout, HTTP error, or bad JSON all
        resolve to a logged warning + None so callers degrade gracefully.
        """
        if not self.enabled:
            logger.warning(
                "Finnhub API key not configured; skipping request to %s", path
            )
            return None

        query = {**params, "token": self.api_key}
        url = f"{self.base_url}{path}"
        try:
            response = httpx.get(url, params=query, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Finnhub HTTP %s for %s: %s",
                exc.response.status_code,
                path,
                exc,
            )
        except httpx.HTTPError as exc:
            logger.warning("Finnhub request failed for %s: %s", path, exc)
        except (ValueError, TypeError) as exc:
            logger.warning("Finnhub returned unparseable body for %s: %s", path, exc)
        except Exception as exc:  # pragma: no cover - defensive catch-all
            logger.warning("Finnhub unexpected error for %s: %s", path, exc)
        return None

    # ---- earnings calendar ------------------------------------------------

    def earnings_calendar(
        self,
        from_date: str,
        to_date: str,
        symbol: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Earnings calendar between two YYYY-MM-DD dates. Returns [] on failure."""
        params: dict[str, Any] = {"from": from_date, "to": to_date}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/calendar/earnings", params)
        if not isinstance(data, dict):
            return []
        rows = data.get("earningsCalendar")
        return rows if isinstance(rows, list) else []

    # ---- estimates & actuals ---------------------------------------------

    def eps_estimates(
        self, symbol: str, freq: str = "quarterly"
    ) -> list[dict[str, Any]]:
        """Forward EPS estimates for a symbol. Returns [] on failure."""
        data = self._get("/stock/eps-estimate", {"symbol": symbol, "freq": freq})
        return self._extract_data_list(data)

    def revenue_estimates(
        self, symbol: str, freq: str = "quarterly"
    ) -> list[dict[str, Any]]:
        """Forward revenue estimates for a symbol. Returns [] on failure."""
        data = self._get("/stock/revenue-estimate", {"symbol": symbol, "freq": freq})
        return self._extract_data_list(data)

    def earnings_actuals(self, symbol: str) -> list[dict[str, Any]]:
        """Historical EPS actual vs. estimate surprises. Returns [] on failure."""
        data = self._get("/stock/earnings", {"symbol": symbol})
        return data if isinstance(data, list) else []

    # ---- news -------------------------------------------------------------

    def company_news(
        self, symbol: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        """Company news between two YYYY-MM-DD dates. Returns [] on failure."""
        data = self._get(
            "/company-news",
            {"symbol": symbol, "from": from_date, "to": to_date},
        )
        return data if isinstance(data, list) else []

    # ---- profile & fundamentals ------------------------------------------

    def company_profile(self, symbol: str) -> Optional[dict[str, Any]]:
        """Company profile (/stock/profile2). Returns None on failure/empty."""
        data = self._get("/stock/profile2", {"symbol": symbol})
        if isinstance(data, dict) and data:
            return data
        return None

    def basic_financials(
        self, symbol: str, metric: str = "all"
    ) -> Optional[dict[str, Any]]:
        """Basic financial metrics (/stock/metric). Returns None on failure/empty."""
        data = self._get("/stock/metric", {"symbol": symbol, "metric": metric})
        if isinstance(data, dict) and data:
            return data
        return None

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _extract_data_list(data: Any) -> list[dict[str, Any]]:
        """Finnhub estimate endpoints wrap rows under a top-level 'data' key."""
        if not isinstance(data, dict):
            return []
        rows = data.get("data")
        return rows if isinstance(rows, list) else []


def get_finnhub_client() -> FinnhubClient:
    """Factory using the system-configured API key."""
    return FinnhubClient()
