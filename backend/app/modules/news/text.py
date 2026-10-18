"""Pure text helpers for news dedupe: URL / headline normalisation and the
symbol shape filter."""

import difflib
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_url(u: str) -> str:
    parts = urlsplit(u.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_")])
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), query, ""))


def url_hash(u: str | None) -> str | None:
    if not u:
        return None
    return hashlib.sha256(normalize_url(u).encode()).hexdigest()


def normalize_headline(h: str) -> str:
    return _NON_ALNUM.sub(" ", h.lower()).strip()[:300]


def similar(a_norm: str, b_norm: str) -> float:
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()
