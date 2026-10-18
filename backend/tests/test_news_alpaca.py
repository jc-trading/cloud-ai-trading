"""P6a Alpaca news client: query / headers, paging, retry and partial-failure
behaviour. Every test injects a fake opener and explicit keys — ``_keys()``
would read the real repo .env on the host."""

import io
import json
import urllib.error
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from app.modules.news import alpaca_news

START = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 15, 13, 15, 30, tzinfo=timezone.utc)
KEYS = ("k", "s")


def _page(ids, token):
    return {"news": [{"id": i, "headline": f"h{i}"} for i in ids], "next_page_token": token}


class _Opener:
    """Replays a script of responses: a dict is a JSON page, an exception is raised."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return io.BytesIO(json.dumps(step).encode())


def _http_error(code):
    return urllib.error.HTTPError(alpaca_news.NEWS_URL, code, "err", {}, None)


def _fetch(opener, max_pages=20, timeout=20.0):
    return alpaca_news.fetch(START, END, max_pages=max_pages, timeout=timeout,
                             opener=opener, keys=KEYS, sleep=lambda s: None)


def _query(request):
    return {k: v[0] for k, v in parse_qs(urlsplit(request.full_url).query).items()}


def test_query_and_headers():
    opener = _Opener(_page([1], None))
    _fetch(opener)
    request, _ = opener.calls[0]
    assert request.full_url.startswith(alpaca_news.NEWS_URL + "?")
    assert _query(request) == {"start": "2026-10-15T13:00:00Z", "end": "2026-10-15T13:15:30Z",
                               "limit": "50", "sort": "asc"}
    assert request.get_header("Apca-api-key-id") == "k"
    assert request.get_header("Apca-api-secret-key") == "s"


def test_page_token_is_forwarded():
    opener = _Opener(_page([1], "tok1"), _page([2], None))
    _fetch(opener)
    assert "page_token" not in _query(opener.calls[0][0])
    assert _query(opener.calls[1][0])["page_token"] == "tok1"


def test_stops_at_max_pages_and_reports_truncated():
    opener = _Opener(_page([1], "a"), _page([2], "b"), _page([3], "c"))
    items, truncated, error = _fetch(opener, max_pages=2)
    assert [i["id"] for i in items] == [1, 2]
    assert truncated is True and error is None
    assert len(opener.calls) == 2


def test_last_page_without_token_is_not_truncated():
    opener = _Opener(_page([1], "a"), _page([2], None))
    items, truncated, error = _fetch(opener, max_pages=2)
    assert [i["id"] for i in items] == [1, 2]
    assert truncated is False and error is None


def test_429_then_success_retries():
    slept = []
    opener = _Opener(_http_error(429), _page([1], None))
    items, truncated, error = alpaca_news.fetch(START, END, max_pages=5, timeout=20.0,
                                                opener=opener, keys=KEYS, sleep=slept.append)
    assert [i["id"] for i in items] == [1]
    assert (truncated, error) == (False, None)
    assert slept == [2]


def test_503_three_times_gives_up_without_raising():
    slept = []
    opener = _Opener(_http_error(503), _http_error(503), _http_error(503))
    items, truncated, error = alpaca_news.fetch(START, END, max_pages=5, timeout=20.0,
                                                opener=opener, keys=KEYS, sleep=slept.append)
    assert items == [] and truncated is True
    assert error.startswith("HTTPError")
    assert len(opener.calls) == 3
    assert slept == [2, 4]


def test_non_retryable_http_error_is_not_retried():
    opener = _Opener(_http_error(403))
    items, truncated, error = _fetch(opener)
    assert (items, truncated) == ([], True) and error.startswith("HTTPError")
    assert len(opener.calls) == 1


def test_url_error_on_page_two_keeps_page_one():
    opener = _Opener(_page([1, 2], "a"), urllib.error.URLError("dns down"))
    items, truncated, error = _fetch(opener)
    assert [i["id"] for i in items] == [1, 2]
    assert truncated is True
    assert error.startswith("URLError")
    assert len(opener.calls) == 2


def test_timeout_is_not_retried():
    opener = _Opener(TimeoutError("timed out"))
    assert _fetch(opener) == ([], True, "TimeoutError: timed out")
    assert len(opener.calls) == 1


def test_bad_json_stops_without_raising():
    class _BadJson(_Opener):
        def __call__(self, request, timeout=None):
            self.calls.append((request, timeout))
            return io.BytesIO(b"<html>")

    items, truncated, error = _fetch(_BadJson())
    assert (items, truncated) == ([], True) and error.startswith("JSONDecodeError")


def test_error_text_is_capped_at_300():
    opener = _Opener(urllib.error.URLError("x" * 1000))
    assert len(_fetch(opener)[2]) == 300


def test_timeout_is_passed_to_opener():
    opener = _Opener(_page([1], "a"), _page([2], None))
    _fetch(opener, timeout=7.5)
    assert [t for _, t in opener.calls] == [7.5, 7.5]
