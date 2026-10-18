"""P2 SEC EDGAR client: User-Agent from .env/env, 404 -> None, 429 back-off,
the SEC_MAX_RPS throttle, gzip, and the dei -> us-gaap fallbacks."""

import gzip
import io
import json
import urllib.error
from collections import Counter
from datetime import date

import pytest

from quant import config
from quant.data import edgar

DEI = "dei:EntityCommonStockSharesOutstanding"
GAAP = "us-gaap:CommonStockSharesOutstanding"
WAB = "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic"


class _Resp(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


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
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, _Resp):
            return reply
        return _Resp(json.dumps(reply).encode())

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


def test_get_json_asks_for_gzip_and_inflates_it(net):
    net[2].append(_Resp(gzip.compress(json.dumps({"facts": {"dei": {}}}).encode()),
                        {"Content-Encoding": "gzip"}))
    assert edgar.get_json("https://data.sec.gov/x.json") == {"facts": {"dei": {}}}
    assert net[1][0].get_header("Accept-encoding") == "gzip"


def test_throttle_spaces_requests(net):
    clock, seen, replies = net
    for _ in range(10):
        edgar.get_json("https://data.sec.gov/x.json")
    assert len(clock.sleeps) == 9
    assert sum(clock.sleeps) >= 9 * (1.0 / config.SEC_MAX_RPS) - 1e-9


def _doc(dei=(), gaap=(), wab=()):
    """A companyfacts document. dei / gaap facts are (end, val, accn, filed),
    wab facts (start, end, val, accn, filed); a dict is used as given."""
    def shares(facts, keys):
        return {"units": {"shares": [
            f if isinstance(f, dict) else {"form": "10-Q", **dict(zip(keys, f))}
            for f in facts]}}

    point = ("end", "val", "accn", "filed")
    return {"facts": {
        "dei": {"EntityCommonStockSharesOutstanding": shares(dei, point)},
        "us-gaap": {"CommonStockSharesOutstanding": shares(gaap, point),
                    "WeightedAverageNumberOfSharesOutstandingBasic":
                        shares(wab, ("start",) + point)}}}


def test_fetch_company_facts_parses_every_share_concept(net):
    net[2].append(_doc(dei=[("2026-06-30", 12_230_000_000, "0001-26-1", "2026-07-25"),
                            ("2026-03-31", 0, "0001-26-0", "2026-04-25")],
                       gaap=[("2026-06-30", 12_200_000_000, "0001-26-1", "2026-07-25")],
                       wab=[("2026-04-01", "2026-06-30", 12_100_000_000, "0001-26-1",
                             "2026-07-25")]))
    out = edgar.fetch_company_facts(1652044)
    assert tuple(out) == (DEI, GAAP, WAB)
    facts, skipped = out[DEI]
    assert skipped == {"nonpositive": 1}
    assert facts == [{"cik": 1652044, "concept": DEI,
                      "end_date": "2026-06-30", "filed": "2026-07-25",
                      "accn": "0001-26-1", "form": "10-Q", "value": 12_230_000_000}]
    assert [f["value"] for f in out[GAAP][0]] == [12_200_000_000]
    assert [f["value"] for f in out[WAB][0]] == [12_100_000_000]
    assert len(net[1]) == 1
    assert net[1][0].full_url.endswith("/companyfacts/CIK0001652044.json")


class _Reg:
    """Stands in for registry and enforces the security_share_facts columns."""

    def __init__(self, fail_insert=(), fail_stamp=()):
        self.many = []
        self.runs = []
        self.fail_insert = set(fail_insert)
        self.fail_stamp = set(fail_stamp)

    def run_many(self, sql, rows):
        for cik, concept, end, filed, accn, form, value in rows:
            assert 0 < value <= 2**63 - 1 and len(concept) <= 80, rows
            assert len(accn) <= 25 and (form is None or len(form) <= 12), rows
            assert date.fromisoformat(end) and date.fromisoformat(filed), rows
            if cik in self.fail_insert:
                raise RuntimeError(f"insert failed for {cik}")
        self.many.append(rows)

    def _run(self, sql, params, fetch=None):
        if params[0] in self.fail_stamp:
            raise RuntimeError(f"update failed for {params[0]}")
        self.runs.append(params)


def test_refresh_falls_back_to_us_gaap_when_dei_is_missing_or_stale(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    clock, seen, replies = net
    replies.extend([
        _doc(gaap=[("2026-06-30", 500, "a-1", "2026-07-20")]),           # cik 1 no dei
        _doc(dei=[("2026-06-30", 900, "b-1", "2026-07-20")]),            # cik 2 dei fresh
        _doc(dei=[("2024-01-31", 100, "c-0", "2024-02-20")],             # cik 3 dei stale
             gaap=[("2026-06-30", 300, "c-1", "2026-07-20")]),
    ])
    stats = edgar.refresh_ciks([1, 2, 3], date(2026, 10, 16))
    assert stats == {"ciks": 3, "facts": 4, "failed": 0, "gaap_fallback": 2, "empty": 0,
                     "skipped_nonpositive": 0, "skipped_invalid": 0}
    assert len(seen) == 3
    assert [len(rows) for rows in reg.many] == [1, 1, 2]
    assert reg.runs == [(1,), (2,), (3,)]                 # shares_checked_at stamped


def test_non_positive_share_counts_are_skipped_and_counted(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    lines = []
    net[2].extend([
        _doc(dei=[("2026-06-30", 900, "b-1", "2026-07-20"),              # cik 1 dei fresh
                  ("2026-03-31", 0, "b-0", "2026-04-20"),
                  ("2025-12-31", None, "b-9", "2026-01-20")]),
        _doc(gaap=[("2026-06-30", 300, "c-1", "2026-07-20"),             # cik 2 no dei
                   ("2023-06-14", 0.4, "c-0", "2024-11-19"),
                   ("2023-03-31", -5, "c-8", "2023-05-01"),
                   ("2022-12-31", "n/a", "c-7", "2023-02-01")]),
    ])
    stats = edgar.refresh_ciks([1, 2], date(2026, 10, 16), progress=lines.append)
    assert stats == {"ciks": 2, "facts": 2, "failed": 0, "gaap_fallback": 1, "empty": 0,
                     "skipped_nonpositive": 5, "skipped_invalid": 0}
    assert [[r[6] for r in rows] for rows in reg.many] == [[900], [300]]
    assert reg.runs == [(1,), (2,)]
    assert lines == ["edgar: 2/2 CIKs, 2 facts, 0 empty, 5 non-positive and 0 invalid "
                     "skipped, 0 failed"]


def _fact(accn="a-1", form="10-Q", val=900, end="2026-06-30", filed="2026-07-20"):
    return {"end": end, "val": val, "accn": accn, "filed": filed, "form": form}


@pytest.mark.parametrize("bad", [
    {"accn": "0" * 26},
    {"accn": ""},
    {"accn": None},
    {"form": "F" * 13},
    {"form": 10},
    {"val": 2**63},
    {"val": 1e19},
    {"end": "2023-13-40"},
    {"end": "2023-W24-3"},
    {"filed": None},
    {"filed": "19 Nov 2024"},
])
def test_facts_that_do_not_fit_the_columns_are_skipped_and_counted(net, monkeypatch, bad):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(_doc(dei=[_fact(accn="ok-1"), _fact(**{"accn": "bad-1", **bad})]))
    stats = edgar.refresh_ciks([1], date(2026, 10, 16))
    assert stats["skipped_invalid"] == 1 and stats["facts"] == 1 and stats["failed"] == 0
    assert [r[4] for r in reg.many[0]] == ["ok-1"]
    assert reg.runs == [(1,)]


def test_facts_at_the_column_limits_are_kept():
    facts, skipped = edgar._parse_facts(1, DEI, [
        _fact(accn="0" * 25, form="F" * 12, val=2**63 - 1), _fact(accn="x", form=None)])
    assert skipped == {} and [f["value"] for f in facts] == [2**63 - 1, 900]


def test_an_overlong_concept_drops_every_fact():
    facts, skipped = edgar._parse_facts(1, "dei:" + "C" * 77, [_fact()])
    assert facts == [] and skipped == {"invalid": 1}


def test_a_year_to_date_span_alone_is_kept():
    facts, skipped = edgar._parse_facts(1, WAB, [{**_fact(val=2538), "start": "2026-01-01"}])
    assert [f["value"] for f in facts] == [2538] and skipped == {}


def test_instant_duplicates_keep_the_first():
    facts, _ = edgar._parse_facts(1, DEI, [_fact(val=111), _fact(val=222), _fact(val=333)])
    assert [f["value"] for f in facts] == [111]


def test_multi_class_issuer_falls_back_to_weighted_average_basic(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    q2, k = "0001628280-26-050705", "0001628280-26-004211"
    net[2].append(_doc(wab=[("2026-01-01", "2026-06-30", 2_538_000_000, q2, "2026-07-30"),
                            ("2026-04-01", "2026-06-30", 2_543_000_000, q2, "2026-07-30"),
                            ("2025-01-01", "2025-12-31", 2_521_000_000, k, "2026-01-29")]))
    stats = edgar.refresh_ciks([1326801], date(2026, 10, 18))
    assert stats["gaap_fallback"] == 1 and stats["empty"] == 0 and stats["facts"] == 2
    assert sorted((r[4], r[2], r[6]) for r in reg.many[0]) == [
        (k, "2025-12-31", 2_521_000_000), (q2, "2026-06-30", 2_543_000_000)]
    assert {r[1] for r in reg.many[0]} == {WAB}


def test_fresh_dei_stores_no_fallback_concepts(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(_doc(dei=[("2026-06-30", 900, "a-1", "2026-07-20")],
                       gaap=[("2026-06-30", 800, "a-1", "2026-07-20")],
                       wab=[("2026-04-01", "2026-06-30", 700, "a-1", "2026-07-20")]))
    stats = edgar.refresh_ciks([1], date(2026, 10, 16))
    assert stats["gaap_fallback"] == 0
    assert {r[1] for r in reg.many[0]} == {DEI}


def test_stale_dei_stores_both_fallbacks(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(_doc(dei=[("2024-01-31", 900, "a-0", "2024-02-20")],
                       gaap=[("2026-06-30", 800, "a-1", "2026-07-20")],
                       wab=[("2026-04-01", "2026-06-30", 700, "a-1", "2026-07-20")]))
    stats = edgar.refresh_ciks([1], date(2026, 10, 16))
    assert stats["gaap_fallback"] == 1 and stats["facts"] == 3
    assert {r[1] for r in reg.many[0]} == {DEI, GAAP, WAB}


@pytest.mark.parametrize("fail", [{"fail_insert": [1]}, {"fail_stamp": [1]}])
def test_a_db_error_fails_only_that_cik(net, monkeypatch, fail):
    reg = _Reg(**fail)
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].extend([_doc(dei=[("2026-06-30", 900, "a-1", "2026-07-20")]),
                   _doc(dei=[("2026-06-30", 800, "b-1", "2026-07-20")])])
    stats = edgar.refresh_ciks([1, 2], date(2026, 10, 16))
    assert stats["failed"] == 1 and stats["ciks"] == 1 and stats["facts"] == 1
    assert reg.runs == [(2,)]                             # cik 1 left unstamped
    assert reg.many[-1][0][0] == 2


@pytest.mark.parametrize("dei_reply", [
    urllib.error.URLError("[Errno -2] Name or service not known"),
    ConnectionRefusedError(111, "Connection refused"),
])
def test_a_network_failure_leaves_the_cik_unchecked(net, monkeypatch, dei_reply):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].extend([dei_reply, _doc(dei=[("2026-06-30", 900, "b-1", "2026-07-20")])])
    stats = edgar.refresh_ciks([7, 8], date(2026, 10, 16))
    assert stats["failed"] == 1 and stats["ciks"] == 1
    assert reg.runs == [(8,)]                             # only cik 8 stamped


def test_503_after_retries_leaves_the_cik_unchecked(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].extend([503, 503, 503, 503])
    assert edgar.refresh_ciks([7], date(2026, 10, 16))["failed"] == 1
    assert reg.runs == [] and reg.many == []


@pytest.mark.parametrize("reply", [
    404,
    {"facts": {}},
    _doc(dei=[("2010-06-30", 100, "o-1", "2010-07-20")]),
])
def test_a_cik_without_share_concepts_is_stamped(net, monkeypatch, reply):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(reply)
    stats = edgar.refresh_ciks([7], date(2026, 10, 16))
    assert stats["ciks"] == 1 and stats["empty"] == 1 and stats["failed"] == 0
    assert reg.runs == [(7,)]


def _fetch_failing(fail):
    def fetch(cik):
        if cik in fail:
            raise urllib.error.URLError("[Errno 111] Connection refused")
        return {DEI: ([{"cik": cik, "concept": DEI, "end_date": "2026-06-30",
                        "filed": "2026-07-20", "accn": f"a-{cik}", "form": "10-Q",
                        "value": 900}], Counter())}
    return fetch


def test_consecutive_failures_raise_an_outage(monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    n = edgar.MAX_CONSECUTIVE_FAILURES
    monkeypatch.setattr(edgar, "fetch_company_facts", _fetch_failing(set(range(3, 3 + n))))
    with pytest.raises(edgar.EdgarOutage) as exc:
        edgar.refresh_ciks(range(1, n + 10), date(2026, 10, 16))
    assert (exc.value.processed, exc.value.failed, exc.value.consecutive) == (n + 2, n, n)
    assert isinstance(exc.value, RuntimeError)
    assert reg.runs == [(1,), (2,)]                       # failed and later CIKs unstamped


def test_a_success_resets_the_failure_streak(monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    n = edgar.MAX_CONSECUTIVE_FAILURES
    fail = set(range(1, n)) | set(range(n + 1, 2 * n))   # 24, one success, 24
    monkeypatch.setattr(edgar, "fetch_company_facts", _fetch_failing(fail))
    stats = edgar.refresh_ciks(range(1, 2 * n + 1), date(2026, 10, 16))
    assert stats["failed"] == 2 * (n - 1) and stats["ciks"] == 2
    assert reg.runs == [(n,), (2 * n,)]


def test_db_errors_count_toward_the_outage(monkeypatch):
    n = edgar.MAX_CONSECUTIVE_FAILURES
    reg = _Reg(fail_insert=range(1, n + 1))
    monkeypatch.setattr(edgar, "registry", reg)
    monkeypatch.setattr(edgar, "fetch_company_facts", _fetch_failing(set()))
    with pytest.raises(edgar.EdgarOutage):
        edgar.refresh_ciks(range(1, n + 5), date(2026, 10, 16))
    assert reg.runs == []


def test_a_failed_cik_is_not_stamped(net, monkeypatch):
    reg = _Reg()
    monkeypatch.setattr(edgar, "registry", reg)
    net[2].append(500)
    assert edgar.refresh_ciks([7], date(2026, 10, 16))["failed"] == 1
    assert reg.runs == [] and reg.many == []


def test_a_time_limit_stops_refresh_instead_of_skipping_the_cik(net, monkeypatch):
    class _Limit(Exception):
        pass

    def limit(cik):
        raise _Limit()

    monkeypatch.setattr(edgar, "TIME_LIMIT_ERRORS", (_Limit,))
    monkeypatch.setattr(edgar, "fetch_company_facts", limit)
    monkeypatch.setattr(edgar, "registry", _Reg())
    with pytest.raises(_Limit):
        edgar.refresh_ciks([1, 2], date(2026, 10, 16))


def test_a_time_limit_during_the_insert_stops_refresh(net, monkeypatch):
    class _Limit(Exception):
        pass

    reg = _Reg()

    def limit(sql, rows):
        raise _Limit()

    reg.run_many = limit
    monkeypatch.setattr(edgar, "TIME_LIMIT_ERRORS", (_Limit,))
    monkeypatch.setattr(edgar, "registry", reg)
    monkeypatch.setattr(edgar, "fetch_company_facts", _fetch_failing(set()))
    with pytest.raises(_Limit):
        edgar.refresh_ciks([1, 2], date(2026, 10, 16))
    assert reg.runs == []
