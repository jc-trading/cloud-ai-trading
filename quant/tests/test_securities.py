"""P2 securities universe: asset-type classification (plan C1 order), the
symbol-directory parser, and refresh's upsert / retire / shrink guard."""

from datetime import date
from types import SimpleNamespace

import pytest

from quant.data import securities

ROW = lambda name, etf=False, test=False: {"name": name, "etf": etf, "test": test}  # noqa: E731


@pytest.mark.parametrize("code, row, expected", [
    ("TCBI.PRB", ROW("Texas Capital Bancshares Depositary Shares Preferred"), "preferred"),
    ("DSX.WS", ROW("Diana Shipping inc. Warrants"), "warrant"),
    ("GLED.U", ROW("GalaxyEdge Acquisition Corporation Units"), "unit"),
    ("GLED.RT", ROW("GalaxyEdge Acquisition Corporation Rights"), "right"),
    ("BRK.B", ROW("Berkshire Hathaway Inc. New Common Stock"), "stock"),
    ("AAAP", ROW("Pacer Barings CLO Market Flex ETF", etf=True), "etf"),
    ("AACG", ROW("ATA Creativity Global - American Depositary Shares, each representing "
                 "two common shares"), "adr"),
    ("ZAZZT", ROW("Tick Pilot Test Stock Class A Common Stock", test=True), "test"),
    ("DGZ", ROW("DB Gold Double Short ETN"), "etn"),
    ("XYZN", ROW("Xyz Power Co First Mortgage Bonds"), "debt"),
    ("BRKHU", ROW("Burtech Acquisition Corp II - Units"), "unit"),
    ("BRKHW", ROW("Burtech Acquisition Corp II - Warrants"), "warrant"),
    ("BRKRP", ROW("Bruker Corporation - 6.375% Mandatory Convertible Preferred Stock"),
     "preferred"),
    ("NEWCO", None, "unknown"),
    ("TEST.U", ROW("anything", test=True), "unit"),            # suffix wins
    ("ETFX", ROW("Some Fund", etf=True, test=True), "test"),   # test before etf
])
def test_classify(code, row, expected):
    assert securities.classify(code, "NYSE", row) == expected


def test_code_conventions():
    assert securities.symdir_code("TCBI$B") == "TCBI.PRB"
    assert securities.sec_code("BRK-B") == "BRK.B"


NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAAP|Pacer Barings CLO Market Flex ETF|G|N|N|100|Y|N
ZAZZT|Tick Pilot Test Stock Class A Common Stock|G|Y|N|100|N|N
File Creation Time: 1016202621:31|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|40|N|BRK.B
TCBI$B|Texas Capital Bancshares, Inc. Depositary Shares|F|TCBIpB|N|100|N|TCBI-B
File Creation Time: 1016202621:31||||||
"""


def test_parse_symdir_both_files():
    nas = securities.parse_symdir("nasdaqlisted", NASDAQ)
    oth = securities.parse_symdir("otherlisted", OTHER)
    assert nas["AAAP"] == {"name": "Pacer Barings CLO Market Flex ETF", "etf": True,
                           "test": False}
    assert nas["ZAZZT"]["test"] is True
    assert set(oth) == {"BRK.B", "TCBI.PRB"}


def test_parse_symdir_refuses_a_changed_header():
    with pytest.raises(RuntimeError, match="unexpected header"):
        securities.parse_symdir("otherlisted", "Ticker|Name\nA|x\n")


class _FakeRegistry:
    def __init__(self, active):
        self.active = active
        self.many = []
        self.runs = []

    def _run(self, sql, params, fetch=None):
        self.runs.append((sql, params))
        if sql.startswith("SELECT count(*)"):
            return (self.active,)
        return 7

    def run_many(self, sql, rows):
        self.many.append((sql, rows))
        return len(rows)


def _asset(symbol, exchange="NYSE", tradable=True, name="Acme Inc Common Stock"):
    return SimpleNamespace(symbol=symbol, exchange=SimpleNamespace(value=exchange),
                           tradable=tradable, name=name)


def _fetchers(n):
    assets = [_asset(f"S{i}") for i in range(n)] + [_asset("OTCX", exchange="OTC"),
                                                    _asset("BRK.B")]
    symdir = {a.symbol: ROW(a.name) for a in assets}
    return {"alpaca": lambda: assets, "symdir": lambda: symdir,
            "sec": lambda: {"BRK.B": 1067983}}


def test_refresh_upserts_skips_otc_and_retires_the_unseen(monkeypatch):
    reg = _FakeRegistry(active=10)
    monkeypatch.setattr(securities, "registry", reg)
    out = securities.refresh(date(2026, 10, 17), fetchers=_fetchers(9))
    sql, rows = reg.many[0]
    assert "ON CONFLICT (market, code) DO UPDATE" in sql and "inactive_since = NULL" in sql
    assert [r[0] for r in rows][-1] == "BRK.B" and "OTCX" not in [r[0] for r in rows]
    assert rows[-1] == ("BRK.B", "Acme Inc Common Stock", "NYSE", "stock", True, 1067983,
                        date(2026, 10, 17), date(2026, 10, 17))
    retire_sql, retire_params = reg.runs[-1]
    assert "SET status = 'inactive'" in retire_sql and "last_seen < %s" in retire_sql
    assert retire_params == (date(2026, 10, 17), date(2026, 10, 17))
    assert out["active"] == 10 and out["retired"] == 7 and out["by_type"] == {"stock": 10}


def test_refresh_refuses_a_universe_that_halved_and_writes_nothing(monkeypatch):
    reg = _FakeRegistry(active=1000)
    monkeypatch.setattr(securities, "registry", reg)
    with pytest.raises(RuntimeError, match="refused: 400 active assets vs 1000"):
        securities.refresh(date(2026, 10, 17), fetchers=_fetchers(399))
    assert reg.many == []
    assert all(sql.startswith("SELECT") for sql, _ in reg.runs)


def test_first_refresh_has_no_guard(monkeypatch):
    reg = _FakeRegistry(active=0)
    monkeypatch.setattr(securities, "registry", reg)
    assert securities.refresh(date(2026, 10, 17), fetchers=_fetchers(3))["active"] == 4


def test_fetch_alpaca_assets_keeps_an_exchange_the_sdk_enum_lacks(monkeypatch):
    from alpaca.trading.enums import AssetClass, AssetStatus

    from quant.data.providers import alpaca_rest

    seen = {}

    class _FakeClient:
        def __init__(self, key, sec, **kwargs):
            seen["init"] = (key, sec, kwargs)

        def get_all_assets(self, request):
            seen["request"] = request
            return [{"symbol": "TXCO", "name": "Texas Co Common Stock", "exchange": "TXSE",
                     "tradable": True, "class": "us_equity"},
                    {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc.", "exchange": "NYSE",
                     "tradable": False}]

    monkeypatch.setattr("alpaca.trading.client.TradingClient", _FakeClient)
    monkeypatch.setattr(alpaca_rest, "_keys", lambda: ("k", "s"))
    monkeypatch.setattr(securities, "dotenv_values", lambda path: {})
    monkeypatch.delenv("ALPACA_MODE", raising=False)
    out = securities.fetch_alpaca_assets()
    assert seen["init"] == ("k", "s", {"paper": True, "raw_data": True})
    assert seen["request"].status == AssetStatus.ACTIVE
    assert seen["request"].asset_class == AssetClass.US_EQUITY
    assert [(a.symbol, a.name, a.exchange, a.tradable) for a in out] == [
        ("TXCO", "Texas Co Common Stock", "TXSE", True),
        ("BRK.B", "Berkshire Hathaway Inc.", "NYSE", False)]


def test_refresh_stores_a_plain_string_exchange(monkeypatch):
    reg = _FakeRegistry(active=0)
    monkeypatch.setattr(securities, "registry", reg)
    assets = [SimpleNamespace(symbol="TXCO", name="Texas Co Common Stock", exchange="TXSE",
                              tradable=True),
              SimpleNamespace(symbol="TXNEW", name="Texas New Co", exchange="TXSE",
                              tradable=True)]
    out = securities.refresh(date(2026, 10, 20), fetchers={
        "alpaca": lambda: assets, "symdir": lambda: {"TXCO": ROW("Texas Co Common Stock")},
        "sec": lambda: {}})
    _, rows = reg.many[0]
    assert rows == [
        ("TXCO", "Texas Co Common Stock", "TXSE", "stock", True, None,
         date(2026, 10, 20), date(2026, 10, 20)),
        ("TXNEW", "Texas New Co", "TXSE", "unknown", True, None,
         date(2026, 10, 20), date(2026, 10, 20))]
    assert out["by_type"] == {"stock": 1, "unknown": 1}


# Real symbol-directory rows (cached 2026-10-17) that the first C1 rules misread.
REAL_ROWS = {
    "AMX": ROW("America Movil, S.A.B. de C.V. American Depositary Shares (each representing "
               "the right to receive twenty (20) Series B Shares"),
    "RLX": ROW("RLX Technology Inc. American Depositary Shares, each representing the right "
               "to receive one (1) Class A ordinary share"),
    "WDH": ROW("Waterdrop Inc. American Depositary Shares (each representing the right to "
               "receive 10 Class A Ordinary Shares)"),
    "ITUB": ROW("Itau Unibanco Banco Holding SA American Depositary Shares (Each repstg 500 "
                "Preferred shares)"),
    "PBR.A": ROW("Petroleo Brasileiro S.A. Petrobras American Depositary Shares representing "
                 "Preferred Shares"),
    "CIB": ROW("Grupo Cibest S.A. American Depositary Shares each representing four "
               "Preferred Shares"),
    "PFBC": ROW("Preferred Bank - Common Stock"),
    "BNS": ROW("Bank Nova Scotia Halifax Pfd 3 Ordinary Shares"),
    "OBAI": ROW("Our Bond, Inc. - Common Stock"),
    "SPYU": ROW("MAX S&P 500 4X Leveraged ETNs due October 30, 2043", etf=True),
    "FNGU": ROW("MicroSectors FANG+ 3X Leveraged ETNs", etf=True),
    "ETNG": ROW("Leverage Shares 2X Long ETN Daily ETF", etf=True),
    "CHSCN": ROW("CHS Inc - Preferred Class B Series 2 Reset Rate"),
    "TSM": ROW("Taiwan Semiconductor Manufacturing Company Ltd."),
    "WAFDP": ROW("WaFd, Inc. - Depositary Shares"),
    "CDZIP": ROW("Cadiz, Inc. - Depositary Shares"),
    "MNSBP": ROW("MainStreet Bancshares, Inc. - Depositary Shares"),
    "IRS": ROW("IRSA Inversiones Y Representaciones S.A. Global Depositary Shares (Each "
               "representing ten shares of Common Stock)"),
    "GJH": ROW("Synthetic Fixed-Income Securities Inc 6.375% (STRATS) Cl A-1"),
    "KTN": ROW("Structured Products Corp 8.205% CorTS 8.205% Corporate Backed Trust "
               "Securities (CorTS)"),
    "JBK": ROW("Lehman ABS 3.50 3.50% Adjustable Corp Backed Tr Certs GS Cap I"),
    # preferred-income closed-end funds: common shares whose names never say "common"
    "JPC": ROW("Nuveen Preferred & Income Opportunities Fund"),
    "PFD": ROW("Flaherty & Crumrine Preferred and Income Fund Incorporated"),
    "FFC": ROW("Flaherty & Crumrine Preferred and Income Securities Fund Incorporated"),
    "LDP": ROW("Cohen & Steers Limited Duration Preferred and Income Fund, Inc."),
    "HPS": ROW("John Hancock Preferred Income Fund III Preferred Income Fund III"),
    "ASML": ROW("ASML Holding N.V. - New York Registry Shares"),
}
REAL_EXPECTED = {"AMX": "adr", "RLX": "adr", "WDH": "adr", "ITUB": "adr", "PBR.A": "adr",
                 "CIB": "adr", "PFBC": "stock", "BNS": "stock", "OBAI": "stock",
                 "SPYU": "etn", "FNGU": "etn", "ETNG": "etf", "CHSCN": "preferred",
                 "TSM": "stock", "ASML": "stock", "WAFDP": "preferred",
                 "CDZIP": "preferred", "MNSBP": "preferred", "IRS": "adr", "GJH": "debt",
                 "KTN": "debt", "JBK": "debt", "JPC": "stock", "PFD": "stock",
                 "FFC": "stock", "LDP": "stock", "HPS": "stock"}


@pytest.mark.parametrize("code", sorted(REAL_EXPECTED))
def test_classify_real_names(code):
    assert securities.classify(code, "NYSE", REAL_ROWS[code]) == REAL_EXPECTED[code]


def test_ciks_without_usable_shares_looks_back_the_stale_window(monkeypatch):
    calls = []

    def _run(sql, params, fetch=None):
        calls.append((sql, params))
        return [(5,), (9,)]

    monkeypatch.setattr(securities, "registry", SimpleNamespace(_run=_run))
    assert securities.ciks_without_usable_shares(date(2026, 10, 18)) == [5, 9]
    sql, params = calls[0]
    assert params == (date(2025, 9, 13),)
    assert "NOT EXISTS" in sql and "end_date >= %s" in sql
