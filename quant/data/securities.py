"""The whole-US-equity universe: one ``securities`` row per tradable listing.

Sources, joined on the Alpaca code:
  - Alpaca assets (``status=active``, ``us_equity``) — what can be traded; OTC
    listings are skipped
  - Nasdaq Trader symbol directory (nasdaqlisted / otherlisted) — the only free
    source that flags ETFs and test issues, which Alpaca assets do not
  - SEC company_tickers_exchange.json — the CIK each code files under

Code conventions: Alpaca uses ``.`` for classes (BRK.B); the symbol directory
writes preferreds as ``$X`` (mapped to ``.PRX``) and SEC uses ``-`` (mapped to
``.``). The raw downloads are cached under ``cat-data/universe/symdir/`` with
a ``*.source.json`` beside each, like the S&P 500 changelog.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import date, datetime, timezone

from dotenv import dotenv_values

from quant import config
from quant.data import registry

SYMDIR_URLS = {
    "nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
    "otherlisted": "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
}
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SYMDIR_DIR = config.UNIVERSE_DIR / "symdir"
ASSET_TYPES = ("stock", "adr", "etf", "etn", "preferred", "warrant", "unit", "right",
               "debt", "test", "unknown")
ACTIVE_DROP_GUARD = 0.5

# symdir file -> (symbol column, ETF column, test-issue column)
_SYMDIR_COLUMNS = {
    "nasdaqlisted": ("Symbol", "ETF", "Test Issue"),
    "otherlisted": ("ACT Symbol", "ETF", "Test Issue"),
}
_SUFFIX_TYPES = (
    (re.compile(r"\.PR[A-Z0-9]*$"), "preferred"),
    (re.compile(r"\.WS[A-Z0-9]*$|\.W$"), "warrant"),
    (re.compile(r"\.U$"), "unit"),
    (re.compile(r"\.RT$|\.R$"), "right"),
)
_ETN_NAME = re.compile(r"\betns?\b|exchange traded notes?")
_ETF_NAME = re.compile(r"\betfs?\b")
_NAME_TYPES = (
    (re.compile(r"\bunits?\b"), "unit"),
    (re.compile(r"warrant"), "warrant"),
    # "rights" or a bare "right", but not an ADS's "the right to receive N shares"
    (re.compile(r"\brights\b|\bright\b(?! to receive)"), "right"),
    (_ETN_NAME, "etn"),
    # structured trust certificates (STRATS, CorTS, corp-backed trust certs)
    (re.compile(r"\bstrats\b|\bcorts\b|structured products|trust certificates"
                r"|\btr certs\b|corp(orate)? backed trust"), "debt"),
    # before preferred: an ADS over preferred shares is still an ADS (ITUB, PBR.A)
    (re.compile(r"(american|global) deposit[ao]ry|\bads\b|\badrs?\b"), "adr"),
    # an issuer whose name says Preferred/Pfd/Bond but lists its common (PFBC, BNS, OBAI)
    (re.compile(r"common stock|common shares|ordinary shares?"), "stock"),
    (re.compile(r"preferred (stock|shares?|securities|class|series)"
                r"|depositary shares"
                r"|series [a-z0-9]+\b.*preferred|\bpfd\b|preference shares?"), "preferred"),
    (re.compile(r"\bnotes?\b|debenture|\bbonds?\b"), "debt"),
)


def classify(code: str, exchange: str | None, symdir_row: dict | None) -> str:
    """Asset type of one Alpaca code (plan C1 order): code suffix, then the
    symbol directory's test-issue and ETF flags (an ETF-flagged note is an
    ETN), then the security name."""
    for pattern, kind in _SUFFIX_TYPES:
        if pattern.search(code):
            return kind
    if symdir_row is None:
        return "unknown"
    if symdir_row.get("test"):
        return "test"
    name = (symdir_row.get("name") or "").lower()
    if symdir_row.get("etf"):
        # "... Long ETN Daily ETF" is a fund on Eaton (ETN), not a note
        return "etn" if _ETN_NAME.search(name) and not _ETF_NAME.search(name) else "etf"
    for pattern, kind in _NAME_TYPES:
        if pattern.search(name):
            return kind
    return "stock"


def symdir_code(raw: str) -> str:
    return raw.strip().upper().replace("$", ".PR")


def sec_code(raw: str) -> str:
    return raw.strip().upper().replace("-", ".")


def _cache(name: str, url: str, raw: bytes) -> None:
    SYMDIR_DIR.mkdir(parents=True, exist_ok=True)
    path = SYMDIR_DIR / name
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    (SYMDIR_DIR / f"{path.stem}.source.json").write_text(json.dumps({
        "source_url": url,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "bytes": len(raw),
    }, indent=2))


def parse_symdir(name: str, text: str) -> dict[str, dict]:
    """Rows of one symbol-directory file keyed by Alpaca-style code. Raises on
    a header it does not recognise — a silently mis-parsed directory would
    reclassify the whole market."""
    sym_col, etf_col, test_col = _SYMDIR_COLUMNS[name]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split("|") if lines else []
    missing = [c for c in (sym_col, "Security Name", etf_col, test_col) if c not in header]
    if missing:
        raise RuntimeError(f"symbol directory {name}: unexpected header, missing {missing}")
    idx = {c: header.index(c) for c in (sym_col, "Security Name", etf_col, test_col)}
    out: dict[str, dict] = {}
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        cells = ln.split("|")
        if len(cells) < len(header):
            continue
        out[symdir_code(cells[idx[sym_col]])] = {
            "name": cells[idx["Security Name"]].strip(),
            "etf": cells[idx[etf_col]].strip() == "Y",
            "test": cells[idx[test_col]].strip() == "Y",
        }
    return out


def fetch_symdir() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for name, url in SYMDIR_URLS.items():
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read()
        parsed = parse_symdir(name, raw.decode("utf-8", errors="replace"))
        if len(parsed) < 1000:
            raise RuntimeError(f"symbol directory {name} looks wrong ({len(parsed)} rows)")
        _cache(f"{name}.txt", url, raw)
        for code, row in parsed.items():
            rows.setdefault(code, row)
    return rows


def fetch_sec_ciks() -> dict[str, int]:
    from quant.data import edgar

    data = edgar.get_json(SEC_TICKERS_URL)
    if not data or "fields" not in data:
        raise RuntimeError("SEC company_tickers_exchange.json looks wrong")
    _cache("company_tickers_exchange.json", SEC_TICKERS_URL, json.dumps(data).encode())
    fields = data["fields"]
    i_cik, i_ticker = fields.index("cik"), fields.index("ticker")
    out: dict[str, int] = {}
    for row in data.get("data", []):
        if row[i_ticker]:
            out.setdefault(sec_code(row[i_ticker]), int(row[i_cik]))
    return out


def fetch_alpaca_assets() -> list:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    from quant.data.providers.alpaca_rest import _keys

    key, sec = _keys()
    mode = (dotenv_values(str(config.REPO_ROOT / ".env")).get("ALPACA_MODE")
            or os.environ.get("ALPACA_MODE") or "paper")
    client = TradingClient(key, sec, paper=(mode != "live"))
    return client.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,
                                                  asset_class=AssetClass.US_EQUITY))


_DEFAULT_FETCHERS = {"alpaca": fetch_alpaca_assets, "symdir": fetch_symdir,
                     "sec": fetch_sec_ciks}


def _value(x):
    return getattr(x, "value", x)


def refresh(today: date, *, fetchers: dict | None = None) -> dict:
    """Upsert every active non-OTC Alpaca listing and retire the rows not seen
    today. Refuses to write anything when the active count would fall below
    half of the current one — a broken source must not wipe the universe."""
    f = {**_DEFAULT_FETCHERS, **(fetchers or {})}
    assets = [a for a in f["alpaca"]() if str(_value(a.exchange) or "") != "OTC"]
    symdir = f["symdir"]()
    ciks = f["sec"]()

    previous = registry._run(
        "SELECT count(*) FROM securities WHERE market = 'us' AND status = 'active'",
        (), fetch="one")[0]
    if previous and len(assets) < previous * ACTIVE_DROP_GUARD:
        raise RuntimeError(f"securities refresh refused: {len(assets)} active assets "
                           f"vs {previous} currently active")

    rows, by_type = [], {}
    for a in assets:
        code = a.symbol.strip().upper()
        exchange = str(_value(a.exchange) or "") or None
        kind = classify(code, exchange, symdir.get(code))
        by_type[kind] = by_type.get(kind, 0) + 1
        rows.append((code, (a.name or "")[:300] or None, exchange, kind,
                     bool(a.tradable), ciks.get(code), today, today))
    registry.run_many(
        "INSERT INTO securities (id, market, code, name, exchange, asset_type, status, "
        "tradable, cik, first_seen, last_seen, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'us', %s, %s, %s, %s, 'active', %s, %s, %s, %s, "
        "now(), now()) "
        "ON CONFLICT (market, code) DO UPDATE SET name = EXCLUDED.name, "
        "exchange = EXCLUDED.exchange, asset_type = EXCLUDED.asset_type, "
        "tradable = EXCLUDED.tradable, cik = EXCLUDED.cik, status = 'active', "
        "last_seen = EXCLUDED.last_seen, inactive_since = NULL, updated_at = now()",
        rows)
    retired = registry._run(
        "UPDATE securities SET status = 'inactive', inactive_since = %s, "
        "updated_at = now() "
        "WHERE market = 'us' AND status = 'active' AND last_seen < %s",
        (today, today))
    return {"active": len(rows), "previous": previous, "retired": retired,
            "by_type": dict(sorted(by_type.items()))}


def eligible_codes(types=config.ELIGIBLE_ASSET_TYPES) -> list[str]:
    rows = registry._run(
        "SELECT code FROM securities WHERE market = 'us' AND status = 'active' "
        "AND tradable AND asset_type = ANY(%s) ORDER BY code",
        (list(types),), fetch="all")
    return [r[0] for r in rows]


def cik_by_code() -> dict[str, int | None]:
    rows = registry._run(
        "SELECT code, cik FROM securities WHERE market = 'us' AND status = 'active'",
        (), fetch="all")
    return {r[0]: r[1] for r in rows}


def asset_types() -> dict[str, str]:
    rows = registry._run(
        "SELECT code, asset_type FROM securities WHERE market = 'us' AND status = 'active'",
        (), fetch="all")
    return {r[0]: r[1] for r in rows}


def ciks_to_check(session: date, limit: int) -> list[int]:
    """Tonight's EDGAR work list: stock CIKs never checked first, then the
    fifth of the rest whose turn it is (cik % 5 == session ordinal % 5)."""
    rows = registry._run(
        "SELECT cik, bool_and(shares_checked_at IS NOT NULL) AS checked "
        "FROM securities WHERE market = 'us' AND status = 'active' "
        "AND asset_type = 'stock' AND cik IS NOT NULL GROUP BY cik "
        "HAVING NOT bool_and(shares_checked_at IS NOT NULL) OR cik %% 5 = %s "
        "ORDER BY 2, 1 LIMIT %s",
        (session.toordinal() % 5, limit), fetch="all")
    return [r[0] for r in rows]


def unchecked_ciks() -> list[int]:
    rows = registry._run(
        "SELECT DISTINCT cik FROM securities WHERE market = 'us' AND status = 'active' "
        "AND asset_type = 'stock' AND cik IS NOT NULL AND shares_checked_at IS NULL "
        "ORDER BY cik", (), fetch="all")
    return [r[0] for r in rows]
