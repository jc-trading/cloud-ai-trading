"""SEC EDGAR shares-outstanding facts — the point-in-time half of market cap.

Only ``companyconcept`` is used: its facts carry ``filed``, so a screen for
session D can keep exactly the numbers that were public on D. The ``frames``
API has no ``filed`` and would leak later filings into the past.

SEC's fair-access rules: every request names a contact in User-Agent
(``SEC_USER_AGENT`` from .env, else the environment) and stays under
``config.SEC_MAX_RPS``. 429/503 back off and retry; 404 means the company
never reported that concept.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import pandas as pd
from dotenv import dotenv_values

from quant import config
from quant.data import registry
from quant.engine.screener import SHARE_CONCEPTS

logger = logging.getLogger(__name__)

_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"
_RETRY_STATUS = (429, 503)
_BACKOFF_S = (1, 2, 4)
_TIMEOUT_S = 30
_FACT_COLUMNS = ["cik", "concept", "end_date", "filed", "accn", "form", "value"]

try:
    from celery.exceptions import SoftTimeLimitExceeded

    # a celery soft time limit must stop the run, never be logged as one bad CIK
    TIME_LIMIT_ERRORS: tuple[type[BaseException], ...] = (SoftTimeLimitExceeded,)
except ImportError:                       # host research venv: no celery, no limit
    TIME_LIMIT_ERRORS = ()

_clock = time.monotonic
_sleep = time.sleep
_opener = urllib.request.urlopen
_next_at: float | None = None


def user_agent() -> str:
    cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
    ua = cfg.get("SEC_USER_AGENT") or os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError("SEC_USER_AGENT missing")
    return ua


def _throttle() -> None:
    global _next_at
    now = _clock()
    if _next_at is not None and now < _next_at:
        _sleep(_next_at - now)
        now = _next_at
    _next_at = now + 1.0 / config.SEC_MAX_RPS


def get_json(url: str):
    """GET a sec.gov JSON document under the rate limit. Returns None on 404."""
    headers = {"User-Agent": user_agent(), "Accept": "application/json"}
    for attempt in range(len(_BACKOFF_S) + 1):
        _throttle()
        try:
            with _opener(urllib.request.Request(url, headers=headers),
                         timeout=_TIMEOUT_S) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in _RETRY_STATUS and attempt < len(_BACKOFF_S):
                _sleep(_BACKOFF_S[attempt])
                continue
            raise


def fetch_concept(cik: int, concept: str) -> list[dict] | None:
    """Every share-count fact the company filed for one concept, or None when
    SEC has no such concept for it."""
    taxonomy, tag = concept.split(":", 1)
    data = get_json(_CONCEPT_URL.format(cik=int(cik), taxonomy=taxonomy, tag=tag))
    if data is None:
        return None
    out = []
    for f in (data.get("units") or {}).get("shares", []):
        val = f.get("val")
        if not isinstance(val, (int, float)) or val <= 0 or not f.get("filed") \
                or not f.get("end") or not f.get("accn"):
            continue
        out.append({"cik": int(cik), "concept": concept, "end_date": f["end"],
                    "filed": f["filed"], "accn": f["accn"], "form": f.get("form"),
                    "value": int(val)})
    return out


def _latest_end(facts: list[dict] | None) -> date | None:
    if not facts:
        return None
    return max(date.fromisoformat(f["end_date"]) for f in facts)


def refresh_ciks(ciks, today: date, *, progress=None) -> dict:
    """Fetch and store share facts for each CIK: the dei concept first, the
    us-gaap fallback only when dei is missing or stale (multi-class issuers
    such as GOOGL report the company total there). A CIK is stamped
    ``shares_checked_at`` once both lookups it needed succeeded, so an
    interrupted run resumes on the unstamped ones."""
    dei, gaap = SHARE_CONCEPTS
    stale_before = today - timedelta(days=config.SHARES_STALE_DAYS)
    stats = {"ciks": 0, "facts": 0, "failed": 0, "gaap_fallback": 0}
    ciks = list(ciks)
    for i, cik in enumerate(ciks, 1):
        try:
            facts = fetch_concept(cik, dei) or []
            latest = _latest_end(facts)
            if latest is None or latest < stale_before:
                stats["gaap_fallback"] += 1
                facts += fetch_concept(cik, gaap) or []
        except TIME_LIMIT_ERRORS:
            raise
        except Exception as e:
            logger.warning("edgar: CIK %s failed: %s", cik, e)
            stats["failed"] += 1
            continue
        if facts:
            registry.run_many(
                "INSERT INTO security_share_facts (id, cik, concept, end_date, filed, "
                "accn, form, value, fetched_at) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (cik, concept, accn, end_date) DO NOTHING",
                [(f["cik"], f["concept"], f["end_date"], f["filed"], f["accn"],
                  f["form"], f["value"]) for f in facts])
        registry._run("UPDATE securities SET shares_checked_at = now() WHERE cik = %s",
                      (int(cik),))
        stats["ciks"] += 1
        stats["facts"] += len(facts)
        if progress is not None and (i % 250 == 0 or i == len(ciks)):
            progress(f"edgar: {i}/{len(ciks)} CIKs, {stats['facts']} facts, "
                     f"{stats['failed']} failed")
    return stats


def load_facts(ciks) -> pd.DataFrame:
    ciks = sorted({int(c) for c in ciks if c is not None})
    if not ciks:
        return pd.DataFrame(columns=_FACT_COLUMNS)
    rows = registry._run(
        "SELECT cik, concept, end_date, filed, accn, form, value "
        "FROM security_share_facts WHERE cik = ANY(%s)", (ciks,), fetch="all")
    return pd.DataFrame([tuple(r) for r in rows], columns=_FACT_COLUMNS)
