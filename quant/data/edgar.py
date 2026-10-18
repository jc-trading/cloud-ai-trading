"""SEC EDGAR shares-outstanding facts — the point-in-time half of market cap.

Only ``companyfacts`` is used: one document per CIK, and its facts carry
``filed``, so a screen for session D can keep exactly the numbers that were
public on D. ``companyconcept`` is not used because it returns empty ``units``
for some CIKs whose companyfacts carries the counts (KO, 2026-10-18). The
``frames`` API has no ``filed`` and would leak later filings into the past.

These APIs carry only non-dimensional facts, so a multi-class issuer that
reports its share counts per class falls back to
``WeightedAverageNumberOfSharesOutstandingBasic``: a period average, not a
point-in-time count.

SEC's fair-access rules: every request names a contact in User-Agent
(``SEC_USER_AGENT`` from .env, else the environment) and stays under
``config.SEC_MAX_RPS``. 429/503 back off and retry; 404 means SEC has no XBRL
facts for the company.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, timedelta

import pandas as pd
from dotenv import dotenv_values

from quant import config
from quant.data import registry
from quant.engine.screener import SHARE_CONCEPTS

logger = logging.getLogger(__name__)

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_RETRY_STATUS = (429, 503)
_BACKOFF_S = (1, 2, 4)
_TIMEOUT_S = 30
_FACT_COLUMNS = ["cik", "concept", "end_date", "filed", "accn", "form", "value"]
_CONCEPT_LEN, _ACCN_LEN, _FORM_LEN = 80, 25, 12    # security_share_facts columns
_BIGINT_MAX = 2**63 - 1
MAX_CONSECUTIVE_FAILURES = 25

try:
    from celery.exceptions import SoftTimeLimitExceeded

    # a celery soft time limit must stop the run, never be logged as one bad CIK
    TIME_LIMIT_ERRORS: tuple[type[BaseException], ...] = (SoftTimeLimitExceeded,)
except ImportError:                       # host research venv: no celery, no limit
    TIME_LIMIT_ERRORS = ()


class EdgarOutage(RuntimeError):
    """Too many CIKs failed in a row: SEC or the database is down, so the run
    stops instead of burning through the rest; unstamped CIKs stay queued."""

    def __init__(self, processed: int, failed: int, consecutive: int):
        super().__init__(processed, failed, consecutive)
        self.processed, self.failed, self.consecutive = processed, failed, consecutive

    def __str__(self) -> str:
        return (f"edgar: {self.consecutive} consecutive CIKs failed, stopped after "
                f"{self.processed} processed ({self.failed} failed)")


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
    headers = {"User-Agent": user_agent(), "Accept": "application/json",
               "Accept-Encoding": "gzip"}
    for attempt in range(len(_BACKOFF_S) + 1):
        _throttle()
        try:
            with _opener(urllib.request.Request(url, headers=headers),
                         timeout=_TIMEOUT_S) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in _RETRY_STATUS and attempt < len(_BACKOFF_S):
                _sleep(_BACKOFF_S[attempt])
                continue
            raise


def _share_count(val) -> int:
    if isinstance(val, (int, float)) and math.isfinite(val):
        return int(val)
    return 0


def _is_date(s) -> bool:
    try:
        return date.fromisoformat(s).isoformat() == s
    except (TypeError, ValueError):
        return False


def _fits_columns(concept: str, f: dict, value: int) -> bool:
    accn, form = f.get("accn"), f.get("form")
    return (value <= _BIGINT_MAX and len(concept) <= _CONCEPT_LEN
            and isinstance(accn, str) and 0 < len(accn) <= _ACCN_LEN
            and (form is None or isinstance(form, str) and len(form) <= _FORM_LEN)
            and _is_date(f.get("end")) and _is_date(f.get("filed")))


def _parse_facts(cik: int, concept: str, rows) -> tuple[list[dict], Counter]:
    """Share-count facts that fit security_share_facts, one per (accn, end): a
    duration concept lists a quarter and a year-to-date span under the same
    filing and period end, and the shortest span (latest ``start``) is kept.
    ``nonpositive`` (0 / fractional / null, rejected by ck_share_fact_value) and
    ``invalid`` (bad date or too long for its column) count the dropped facts."""
    skipped = Counter()
    kept: dict[tuple[str, str], tuple[str, dict]] = {}
    for f in rows:
        value = _share_count(f.get("val"))
        if value <= 0:
            skipped["nonpositive"] += 1
            continue
        if not _fits_columns(concept, f, value):
            skipped["invalid"] += 1
            continue
        key, start = (f["accn"], f["end"]), str(f.get("start") or "")
        if key in kept and kept[key][0] >= start:
            continue
        kept[key] = (start, {"cik": int(cik), "concept": concept, "end_date": f["end"],
                             "filed": f["filed"], "accn": f["accn"],
                             "form": f.get("form"), "value": value})
    return [row for _, row in kept.values()], skipped


def fetch_company_facts(cik: int) -> dict[str, tuple[list[dict], Counter]] | None:
    """Every SHARE_CONCEPTS concept's facts from one companyfacts document;
    None when SEC has no XBRL facts for the CIK."""
    data = get_json(_FACTS_URL.format(cik=int(cik)))
    if data is None:
        return None
    facts = data.get("facts") or {}
    out = {}
    for concept in SHARE_CONCEPTS:
        taxonomy, tag = concept.split(":", 1)
        rows = ((((facts.get(taxonomy) or {}).get(tag) or {}).get("units") or {})
                .get("shares") or [])
        out[concept] = _parse_facts(int(cik), concept, rows)
    return out


def _latest_end(facts: list[dict] | None) -> date | None:
    if not facts:
        return None
    return max(date.fromisoformat(f["end_date"]) for f in facts)


def refresh_ciks(ciks, today: date, *, progress=None) -> dict:
    """Fetch and store share facts for each CIK from one companyfacts
    document: the dei concept first, the other two concepts only when dei is
    missing or stale (multi-class issuers such as GOOGL report the company
    total in us-gaap). A CIK is stamped ``shares_checked_at`` once its fetch
    and insert succeeded, even with no usable count (counted in ``empty``; the
    every-fifth-night rotation checks it again); any other failure only marks
    that CIK failed, so the run goes on and the next one retries the unstamped
    CIKs. ``MAX_CONSECUTIVE_FAILURES`` failures in a row raise ``EdgarOutage``."""
    primary, fallbacks = SHARE_CONCEPTS[0], SHARE_CONCEPTS[1:]
    stale_before = today - timedelta(days=config.SHARES_STALE_DAYS)
    stats = {"ciks": 0, "facts": 0, "failed": 0, "gaap_fallback": 0, "empty": 0,
             "skipped_nonpositive": 0, "skipped_invalid": 0}
    ciks = list(ciks)
    streak = 0
    for i, cik in enumerate(ciks, 1):
        try:
            by_concept = fetch_company_facts(cik) or {}
            facts, skipped = by_concept.get(primary, ([], Counter()))
            facts, skipped = list(facts), Counter(skipped)
            latest = _latest_end(facts)
            if latest is None or latest < stale_before:
                stats["gaap_fallback"] += 1
                for concept in fallbacks:
                    more, more_skipped = by_concept.get(concept, ([], Counter()))
                    facts += more
                    skipped += more_skipped
            if facts:
                registry.run_many(
                    "INSERT INTO security_share_facts (id, cik, concept, end_date, "
                    "filed, accn, form, value, fetched_at) "
                    "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, now()) "
                    "ON CONFLICT (cik, concept, accn, end_date) DO NOTHING",
                    [(f["cik"], f["concept"], f["end_date"], f["filed"], f["accn"],
                      f["form"], f["value"]) for f in facts])
            registry._run("UPDATE securities SET shares_checked_at = now() WHERE cik = %s",
                          (int(cik),))
        except TIME_LIMIT_ERRORS:
            raise
        except Exception as e:
            logger.warning("edgar: CIK %s failed: %s", cik, e)
            stats["failed"] += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                raise EdgarOutage(i, stats["failed"], streak) from e
            continue
        streak = 0
        stats["ciks"] += 1
        stats["facts"] += len(facts)
        latest = _latest_end(facts)
        if latest is None or latest < stale_before:
            stats["empty"] += 1
        stats["skipped_nonpositive"] += skipped["nonpositive"]
        stats["skipped_invalid"] += skipped["invalid"]
        if progress is not None and (i % 250 == 0 or i == len(ciks)):
            progress(f"edgar: {i}/{len(ciks)} CIKs, {stats['facts']} facts, "
                     f"{stats['empty']} empty, "
                     f"{stats['skipped_nonpositive']} non-positive and "
                     f"{stats['skipped_invalid']} invalid skipped, "
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
