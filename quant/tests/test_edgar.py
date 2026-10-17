"""P2 SEC EDGAR client: User-Agent from .env/env, 404 -> None, 429 back-off,
the SEC_MAX_RPS throttle, and the dei -> us-gaap fallback."""

import io
import json
import urllib.error
from datetime import date

import pytest

from quant import config
from quant.data import edgar


class _Clock:
    def __init__(self):
        self.t = 100.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def net(monkeypatch):
    clock = _Clock()
    seen = []
    replies = []

    def opener(req, timeout):
        seen.append(req)
        reply = replies.pop(0) if replies else {"ok": True}
        if isinstance(reply, int):
            raise urllib.error.HTTPError(req.full_url, reply, "err", {}, None)
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(edgar, "_clock", clock.now)
    monkeypatch.setattr(edgar, "_sleep", clock.sleep)
    monkeypatch.setattr(edgar, "_opener", opener)
    monkeypatch.setattr(edgar, "_next_at", None)
    monkeypatch.setattr(edgar, "dotenv_values", lambda path: {})
    monkeypatch.setenv("SEC_USER_AGENT", "tester@example.com")
    return clock, seen, replies


def test_requests_carry_the_user_agent(net):
    clock, seen, replies = net
    assert edgar.get_json("https://data.sec.gov/x.json") == {"ok": True}
    assert seen[0].get_header("User-agent") == "tester@example.com"


def test_env_file_wins_over_the_environment(net, monkeypatch):
    monkeypatch.setattr(edgar, "dotenv_values",
                        lambda path: {"SEC_USER_AGENT": "file@example.com"})
    assert edgar.user_agent() == "file@example.com"


def test_missing_user_agent_raises(net, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT")
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT missing"):
        edgar.get_json("https://data.sec.gov/x.json")
    assert net[1] == []


def test_404_is_none(net):
    net[2].append(404)
    assert edgar.get_json("https://data.sec.gov/x.json") is None


def test_429_backs_off_and_retries(net):
    clock, seen, replies = net
    replies.extend([429, 503, {"done": 1}])
    assert edgar.get_json("https://data.sec.gov/x.json") == {"done": 1}
    assert len(seen) == 3
    assert clock.sleeps[:1] == [1] and 2 in clock.sleeps


def test_429_gives_up_after_three_retries(net):
    net[2].extend([429, 429, 429, 429])
    with pytest.raises(urllib.error.HTTPError):
        edgar.get_json("https://data.sec.gov/x.json")
    assert len(net[1]) == 4


def test_500_is_not_retried(net):
    net[2].append(500)
    with pytest.raises(urllib.error.HTTPError):
        edgar.get_json("https://data.sec.gov/x.json")
    assert len(net[1]) == 1


def test_throttle_spaces_requests(net):
    clock, seen, replies = net
    for _ in range(10):
        edgar.get_json("https://data.sec.gov/x.json")
    assert len(clock.sleeps) == 9
    assert sum(clock.sleeps) >= 9 * (1.0 / config.SEC_MAX_RPS) - 1e-9


def _concept(*facts):
    return {"units": {"shares": [
        {"end": e, "val": v, "accn": a, "filed": f, "form": "10-Q"} for e, v, a, f in facts]}}


def test_fetch_concept_parses_share_facts(net):
    net[2].append(_concept(("2026-06-30", 12_230_000_000, "0001-26-1", "2026-07-25"),
                           ("2026-03-31", 0, "0001-26-0", "2026-04-25")))
    facts = edgar.fetch_concept(1652044, "us-gaap:CommonStockSharesOutstanding")
    assert facts == [{"cik": 1652044, "concept": "us-gaap:CommonStockSharesOutstanding",
                      "end_date": "2026-06-30", "filed": "2026-07-25",
                      "accn": "0001-26-1", "form": "10-Q", "value": 12_230_000_000}]
    assert net[1][0].full_url.endswith(
        "/CIK0001652044/us-gaap/CommonStockSharesOutstanding.json")


class _Reg:
    def __init__(self):
        self.many = []
        self.runs = []

    def run_many(self, sql, rows):
        self.many.append(rows)

    def _run(self, sql, params, fetch=None):
        self.runs.append(params)


def test_refresh_falls_back_to_us_gaap_when_dei_is_missing_or_stale(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].extend([
        404,                                                              # cik 1 dei
        _concept(("2026-06-30", 500, "a-1", "2026-07-20")),               # cik 1 gaap
        _concept(("2026-06-30", 900, "b-1", "2026-07-20")),               # cik 2 dei fresh
        _concept(("2024-01-31", 100, "c-0", "2024-02-20")),               # cik 3 dei stale
        _concept(("2026-06-30", 300, "c-1", "2026-07-20")),               # cik 3 gaap
    ])
    stats = edgar.refresh_ciks([1, 2, 3], date(2026, 10, 16))
    assert stats == {"ciks": 3, "facts": 4, "failed": 0, "gaap_fallback": 2}
    assert [len(rows) for rows in reg.many] == [1, 1, 2]
    assert reg.runs == [(1,), (2,), (3,)]                 # shares_checked_at stamped


def test_a_failed_cik_is_not_stamped(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(500)
    assert edgar.refresh_ciks([7], date(2026, 10, 16))["failed"] == 1
    assert reg.runs == [] and reg.many == []


def test_a_time_limit_stops_refresh_instead_of_skipping_the_cik(net, monkeypatch):
    class _Limit(Exception):
        pass

    def limit(cik, concept):
        raise _Limit()

    monkeypatch.setattr(edgar, "TIME_LIMIT_ERRORS", (_Limit,))
    monkeypatch.setattr(edgar, "fetch_concept", limit)
    monkeypatch.setattr(edgar, "registry", _Reg())
    with pytest.raises(_Limit):
        edgar.refresh_ciks([1, 2], date(2026, 10, 16))
