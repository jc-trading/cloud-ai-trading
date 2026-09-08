"""GET /api/v1/market/{symbol}/bars — the read-only store window (方案 Phase 7).

The store is real (bars written into a temp BARS_ROOT through store.write_bars);
only PostgreSQL is faked, because the endpoint reads market_data_files purely to
stamp the envelope with provenance.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import UserRole
from app.modules.market.router import router as market_router
from quant import config as qconfig
from quant.data import store


class _FakeRegistry:
    def get_row(self, symbol, timeframe, period_key):
        return None

    def upsert(self, *a, **kw):
        pass


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self.row = row

    async def execute(self, stmt):
        return _FakeResult(self.row)


_PROVENANCE = ("alpaca:sip", datetime(2026, 3, 4, 21, 0, tzinfo=timezone.utc))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(qconfig, "BARS_ROOT", tmp_path)
    monkeypatch.setattr(store, "_registry", _FakeRegistry())

    ts = pd.date_range(pd.Timestamp("2026-03-02 09:00", tz="America/New_York"),
                       periods=600, freq="1min").tz_convert("UTC")
    n = len(ts)
    store.write_bars("ZZTEST", "1min", "2026-03-02", pd.DataFrame({
        "ts": ts,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
        "vwap": [100.2 + i for i in range(n)],
        "trade_count": [10 + i for i in range(n)],
    }), provider="alpaca:sip")

    api = FastAPI()
    api.include_router(market_router, prefix="/api/v1")
    api.dependency_overrides[get_db] = lambda: _FakeSession(_PROVENANCE)
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role=UserRole.BASIC, is_active=True)
    with TestClient(api) as c:
        yield c


class TestBarsEndpoint:
    def test_envelope_shape(self, client):
        r = client.get("/api/v1/market/ZZTEST/bars",
                       params={"timeframe": "1min", "start": "2026-03-01",
                               "end": "2026-03-03", "limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "ZZTEST"
        assert body["timeframe"] == "1min"
        assert body["provider"] == "alpaca:sip"
        assert body["last_ts"].startswith("2026-03-04T21:00:00")
        assert len(body["bars"]) == 5
        assert set(body["bars"][0]) == {"t", "o", "h", "l", "c", "v", "vwap", "n"}

    def test_returns_the_stored_session_including_pre_and_post(self, client):
        """No session filter here — the endpoint hands back what the file holds."""
        r = client.get("/api/v1/market/ZZTEST/bars",
                       params={"timeframe": "1min", "start": "2026-03-01",
                               "end": "2026-03-03", "limit": 5000})
        bars = r.json()["bars"]
        assert len(bars) == 600
        assert bars[0]["t"].endswith("14:00:00Z")   # 09:00 ET pre-market

    def test_limit_takes_the_tail(self, client):
        r = client.get("/api/v1/market/ZZTEST/bars",
                       params={"timeframe": "1min", "limit": 3})
        bars = r.json()["bars"]
        assert len(bars) == 3
        assert bars[-1]["c"] == 699.5

    def test_unknown_symbol_is_an_empty_envelope(self, client):
        r = client.get("/api/v1/market/NOSUCH/bars", params={"timeframe": "daily"})
        assert r.status_code == 200
        assert r.json()["bars"] == []

    def test_bad_timeframe_is_rejected(self, client):
        assert client.get("/api/v1/market/ZZTEST/bars",
                          params={"timeframe": "5m"}).status_code == 422

    def test_limit_ceiling_is_5000(self, client):
        assert client.get("/api/v1/market/ZZTEST/bars",
                          params={"limit": 5001}).status_code == 422

    def test_crypto_pair_is_rejected(self, client):
        assert client.get("/api/v1/market/BTC/USDT/bars").status_code == 422

    def test_requires_authentication(self, client):
        client.app.dependency_overrides.pop(get_current_user)
        try:
            assert client.get("/api/v1/market/ZZTEST/bars").status_code == 422
        finally:
            client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
                role=UserRole.BASIC, is_active=True)
