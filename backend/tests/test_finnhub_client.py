"""Tests for the Finnhub FA client (WIRE-finnhub).

All httpx calls are mocked — the suite never touches the network, so it is
deterministic and does not consume the limited free API quota.
"""

from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.fundamentals.finnhub_client import FinnhubClient


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("GET", "https://x"), response=self
            )

    def json(self):
        return self._json


def _patch_get(monkeypatch, response):
    """Force httpx.get to return `response` and capture the call args."""
    calls = {}

    def fake_get(url, params=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        calls["timeout"] = timeout
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


# ---- graceful degradation: no key ---------------------------------------


def test_no_key_returns_empty_without_network(monkeypatch):
    """With no API key, every method degrades and no HTTP call is made."""
    def boom(*args, **kwargs):
        raise AssertionError("httpx.get must not be called without a key")

    monkeypatch.setattr(httpx, "get", boom)
    client = FinnhubClient(api_key="")

    assert client.enabled is False
    assert client.earnings_calendar("2026-01-01", "2026-02-01") == []
    assert client.eps_estimates("AAPL") == []
    assert client.revenue_estimates("AAPL") == []
    assert client.earnings_actuals("AAPL") == []
    assert client.company_news("AAPL", "2026-01-01", "2026-02-01") == []
    assert client.company_profile("AAPL") is None
    assert client.basic_financials("AAPL") is None


# ---- graceful degradation: HTTP / network errors -------------------------


def test_http_error_degrades_to_empty(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse({}, status_code=429))
    client = FinnhubClient(api_key="k")
    assert client.earnings_calendar("2026-01-01", "2026-02-01") == []
    assert client.company_profile("AAPL") is None


def test_network_error_degrades_to_empty(monkeypatch):
    _patch_get(monkeypatch, httpx.ConnectTimeout("timed out"))
    client = FinnhubClient(api_key="k")
    assert client.company_news("AAPL", "2026-01-01", "2026-02-01") == []
    assert client.basic_financials("AAPL") is None


# ---- happy paths ---------------------------------------------------------


def test_earnings_calendar_parses_and_sends_token(monkeypatch):
    payload = {"earningsCalendar": [{"symbol": "AAPL", "epsEstimate": 1.5}]}
    calls = _patch_get(monkeypatch, _FakeResponse(payload))
    client = FinnhubClient(api_key="secret")

    rows = client.earnings_calendar("2026-01-01", "2026-02-01", symbol="AAPL")
    assert rows == [{"symbol": "AAPL", "epsEstimate": 1.5}]
    assert calls["params"]["token"] == "secret"
    assert calls["params"]["from"] == "2026-01-01"
    assert calls["params"]["symbol"] == "AAPL"
    assert calls["timeout"] == client.timeout


def test_eps_and_revenue_estimates_unwrap_data(monkeypatch):
    calls = _patch_get(monkeypatch, _FakeResponse({"data": [{"epsAvg": 2.0}]}))
    client = FinnhubClient(api_key="k")
    assert client.eps_estimates("AAPL") == [{"epsAvg": 2.0}]
    assert calls["url"].endswith("/stock/eps-estimate")

    _patch_get(monkeypatch, _FakeResponse({"data": [{"revenueAvg": 100}]}))
    assert client.revenue_estimates("AAPL") == [{"revenueAvg": 100}]


def test_earnings_actuals_returns_list(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse([{"actual": 1.2, "estimate": 1.1}]))
    client = FinnhubClient(api_key="k")
    assert client.earnings_actuals("AAPL") == [{"actual": 1.2, "estimate": 1.1}]


def test_company_news_returns_list(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse([{"headline": "news"}]))
    client = FinnhubClient(api_key="k")
    assert client.company_news("AAPL", "2026-01-01", "2026-02-01") == [
        {"headline": "news"}
    ]


def test_company_profile_and_metrics(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse({"name": "Apple Inc"}))
    client = FinnhubClient(api_key="k")
    assert client.company_profile("AAPL") == {"name": "Apple Inc"}

    _patch_get(monkeypatch, _FakeResponse({}))  # empty dict -> None
    assert client.company_profile("AAPL") is None

    _patch_get(monkeypatch, _FakeResponse({"metric": {"peBasicExclExtraTTM": 30}}))
    assert client.basic_financials("AAPL") == {"metric": {"peBasicExclExtraTTM": 30}}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
