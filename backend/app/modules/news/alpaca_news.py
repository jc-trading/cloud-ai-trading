"""Alpaca news REST client (stdlib urllib so every request carries a timeout —
alpaca-py's NewsClient has none)."""

import http.client
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

from quant.data.providers.alpaca_rest import _keys

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
_PAGE_LIMIT = 50
_BACKOFF_S = (2, 4)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retryable(e: urllib.error.HTTPError) -> bool:
    return e.code == 429 or e.code >= 500


def _get_page(url, headers, timeout, opener, sleep) -> dict:
    for attempt in range(len(_BACKOFF_S) + 1):
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if _retryable(e) and attempt < len(_BACKOFF_S):
                sleep(_BACKOFF_S[attempt])
                continue
            raise


def fetch(start: datetime, end: datetime, *, max_pages: int, timeout: float,
          opener=urllib.request.urlopen, keys=None,
          sleep=time.sleep) -> tuple[list[dict], bool, str | None]:
    """Page through /v1beta1/news oldest-first. Never raises on a network or
    HTTP failure: it stops paging and returns what it already has, so the
    caller's cursor still advances over the pages that did arrive. Returns
    ``(items, truncated, error)``; ``truncated`` means a page token was left."""
    key, secret = keys or _keys()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "Accept": "application/json"}
    params = {"start": _rfc3339(start), "end": _rfc3339(end),
              "limit": _PAGE_LIMIT, "sort": "asc"}
    items: list[dict] = []
    token = None
    for _ in range(max_pages):
        query = {**params, "page_token": token} if token else params
        try:
            page = _get_page(f"{NEWS_URL}?{urlencode(query)}", headers, timeout,
                             opener, sleep)
        except (OSError, http.client.HTTPException, ValueError) as e:
            return items, True, f"{type(e).__name__}: {e}"[:300]
        items.extend(page.get("news") or [])
        token = page.get("next_page_token")
        if not token:
            return items, False, None
    return items, True, None
