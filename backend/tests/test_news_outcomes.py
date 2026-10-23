"""P7a price-claim outcomes: the SPY-relative hit rule on synthetic daily bars
(pure), and the news_outcomes / news_source_score_history constraints against
real PostgreSQL (rolled back)."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.news import outcomes
from app.modules.news.ingest import platform_source
from app.modules.news.models import (
    NewsItem, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory
)
from tests._pg_rollback import pg_session_rollback  # noqa: F401

A, E = date(2026, 10, 12), date(2026, 10, 14)

INDUSTRIES = (
    "Aerospace & Defense", "Airlines", "Auto Components", "Automobiles", "Banking",
    "Beverages", "Biotechnology", "Building", "Chemicals", "Commercial Services & Supplies",
    "Communications", "Construction", "Consumer products", "Distributors",
    "Diversified Consumer Services", "Electrical Equipment", "Energy", "Financial Services",
    "Food Products", "Health Care", "Hotels, Restaurants & Leisure", "Industrial Conglomerates",
    "Insurance", "Leisure Products", "Life Sciences Tools & Services",
    "Logistics & Transportation", "Machinery", "Marine", "Media", "Metals & Mining",
    "Packaging", "Paper & Forest", "Pharmaceuticals", "Professional Services", "Real Estate",
    "Retail", "Road & Rail", "Semiconductors", "Technology", "Telecommunication",
    "Textiles, Apparel & Luxury Goods", "Tobacco", "Trading Companies & Distributors",
    "Transportation Infrastructure", "Utilities",
)


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(settings, "NEWS_HIT_MIN_EXCESS_PCT", 1.0)


def _p(anchor_open: float, end_close: float) -> dict:
    return {A: (anchor_open, anchor_open), E: (end_close, end_close)}


def _eval(prices, spy=None, *, direction="up", expected=4.0, **kw):
    return outcomes.evaluate_price(direction=direction, expected=expected, anchor=A, end=E,
                                   prices=prices, spy=_p(100, 100) if spy is None else spy,
                                   **kw)


def _bars(rows: dict, *, unadjusted=False) -> pd.DataFrame:
    df = pd.DataFrame({
        "ts": [pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC") for d in rows],
        "open": [o for o, _ in rows.values()],
        "close": [c for _, c in rows.values()],
    })
    df.attrs["unadjusted"] = unadjusted
    return df


def test_o1_up_hit_against_spy():
    r = _eval(_p(100, 105), _p(100, 101), expected=5.0)
    assert r["status"] == "scored" and r["reason"] is None and r["hit"] is True
    assert (r["realized_move_pct"], r["spy_move_pct"], r["excess_move_pct"]) == (5.0, 1.0, 4.0)
    assert (r["score"], r["magnitude_error_pct"]) == (0.8, -1.0)


def test_o2_down_hit():
    r = _eval(_p(100, 95), _p(100, 101), direction="down", expected=8.0)
    assert r["hit"] is True and r["excess_move_pct"] == -6.0
    assert (r["score"], r["magnitude_error_pct"]) == (0.75, -2.0)


def test_o3_small_claim_needs_one_point_of_excess():
    r = _eval(_p(100, 100.9), expected=1.0)
    assert r["status"] == "scored" and r["hit"] is False and r["score"] == 0.0


@pytest.mark.parametrize("end_close, hit", [(102.9, False), (103.1, True)])
def test_o4_large_claim_needs_half_the_move(end_close, hit):
    r = _eval(_p(100, end_close), expected=6.0)
    assert r["hit"] is hit
    assert r["score"] == (round(3.1 / 6, 4) if hit else 0.0)


def test_o5_absolute_gain_that_trails_spy_is_a_miss():
    r = _eval(_p(100, 103), _p(100, 104), expected=2.0)
    assert r["realized_move_pct"] == 3.0 and r["hit"] is False and r["score"] == 0.0


def test_o6_unadjusted_bars_are_no_data():
    assert outcomes.daily_prices(_bars({A: (1, 2)}, unadjusted=True)) is None
    assert _eval(None) == {"status": "no_data", "reason": "unadjusted"}


@pytest.mark.parametrize("prices, spy, reason", [
    ({E: (1.0, 1.0)}, _p(100, 100), "no anchor bar"),
    ({A: (1.0, 1.0)}, _p(100, 100), "no end bar"),
    (_p(100, 105), {A: (100.0, 100.0)}, "no SPY bar"),
    (_p(100, 105), {}, "no SPY bar"),
])
def test_o7_missing_bars(prices, spy, reason):
    assert _eval(prices, spy) == {"status": "no_data", "reason": reason}


def test_o7_spy_none_is_no_data():
    r = outcomes.evaluate_price(direction="up", expected=4.0, anchor=A, end=E,
                                prices=_p(100, 105), spy=None)
    assert r == {"status": "no_data", "reason": "no SPY bar"}


def test_o8_anchor_open_to_end_close():
    df = _bars({A: (100.0, 200.0), date(2026, 10, 13): (150.0, 150.0), E: (300.0, 110.0)})
    prices = outcomes.daily_prices(df)
    assert prices[A] == (100.0, 200.0) and prices[E] == (300.0, 110.0)
    r = _eval(prices, expected=10.0)
    assert (r["anchor_price"], r["end_price"], r["realized_move_pct"]) == (100.0, 110.0, 10.0)


def test_o8_empty_bars_are_an_empty_map():
    assert outcomes.daily_prices(_bars({})) == {}


def test_o9_score_is_capped_at_one():
    r = _eval(_p(100, 120), expected=4.0)
    assert r["hit"] is True and r["score"] == 1.0 and r["magnitude_error_pct"] == 16.0


def test_o10_sector_is_diagnostic_only():
    assert outcomes.sector_etf("ZQXP", {"ZQXP": "N/A"}) is None
    assert outcomes.sector_etf("ZQXP", {}) is None
    assert outcomes.sector_etf("ZQXP", {"ZQXP": "Semiconductors"}) == "XLK"
    missing = _eval(_p(100, 105), sector={A: (50.0, 50.0)}, etf="XLK")
    assert (missing["sector_etf"], missing["sector_move_pct"]) == ("XLK", None)
    full = _eval(_p(100, 105), sector=_p(50, 51), etf="XLK")
    assert full["sector_move_pct"] == 2.0
    assert full["hit"] == missing["hit"] and full["score"] == missing["score"]


def test_o11_sector_map_covers_every_industry():
    assert set(outcomes.SECTOR_ETF) == set(INDUSTRIES) and len(INDUSTRIES) == 45
    assert set(outcomes.SECTOR_ETF.values()) == {
        "XLK", "XLF", "XLV", "XLE", "XLU", "XLRE", "XLY", "XLC", "XLP", "XLB", "XLI"}
    for unmapped in ("N/A", "unknown", None):
        assert outcomes.SECTOR_ETF.get(unmapped) is None


def test_o12_threshold_comes_from_rules(monkeypatch):
    assert _eval(_p(100, 104), expected=4.0)["hit"] is True
    monkeypatch.setattr(settings, "NEWS_HIT_MIN_EXCESS_PCT", 5.0)
    assert _eval(_p(100, 104), expected=4.0)["hit"] is False


def test_x01_hit_is_judged_on_the_unrounded_excess():
    r = _eval(_p(100, 100.99996), expected=1.0)
    assert r["excess_move_pct"] == 1.0 and r["hit"] is False and r["score"] == 0.0


@pytest.mark.parametrize("prices, spy", [
    ({A: (0.0, 1.0), E: (105.0, 105.0)}, _p(100, 100)),
    ({A: (100.0, 100.0), E: (105.0, float("nan"))}, _p(100, 100)),
    (_p(100, 105), {A: (-1.0, 100.0), E: (100.0, 100.0)}),
    (_p(100, 105), {A: (100.0, 100.0), E: (100.0, float("inf"))}),
], ids=["zero_anchor_open", "nan_end_close", "negative_spy_open", "inf_spy_close"])
def test_bad_prices_are_no_data(prices, spy):
    assert _eval(prices, spy) == {"status": "no_data", "reason": "bad price"}


def test_bad_sector_prices_only_drop_the_diagnostic():
    r = _eval(_p(100, 105), sector={A: (0.0, 0.0), E: (51.0, 51.0)}, etf="XLK")
    assert r["status"] == "scored" and r["sector_move_pct"] is None


@pytest_asyncio.fixture
async def db(pg_session_rollback):
    s = pg_session_rollback
    now = datetime.now(timezone.utc)
    item = NewsItem(id=uuid4(), origin="live", headline=f"ZQXP7 {uuid4().hex}",
                    headline_norm=f"zqxp7 {uuid4().hex}", published_at=now - timedelta(days=5))
    s.add(item)
    await s.flush()
    rows = [NewsItemSymbol(id=uuid4(), news_id=item.id, symbol=f"ZQXP{i}", claim_type="price",
                           direction="up", probability=60, expected_move_pct=4,
                           horizon_days=3, anchor_session=A, evaluate_after=E,
                           suggested_action="ignore") for i in range(2)]
    s.add_all(rows)
    source = NewsSource(id=uuid4(), kind="author", name=f"benzinga/ZQXP7 {uuid4().hex[:8]}",
                        parent_id=(await platform_source(s)).id)
    s.add(source)
    await s.flush()
    s.info.update(rows=rows, source=source)
    return s


def _scored(**over) -> dict:
    return {"status": "scored", "method": "price", "scoring_eligible": True,
            "anchor_session": A, "end_session": E, "anchor_price": 100, "end_price": 105,
            "realized_move_pct": 5, "spy_move_pct": 1, "excess_move_pct": 4,
            "magnitude_error_pct": 0, "hit": True, "score": 1, **over}


async def _insert(db, model, **values):
    async with db.begin_nested():
        db.add(model(id=uuid4(), **values))
        await db.flush()


async def _expect_violation(db, constraint: str, model, **values):
    with pytest.raises(IntegrityError) as e:
        await _insert(db, model, **values)
    assert constraint in str(e.value)


@pytest.mark.asyncio
async def test_valid_outcomes_insert(db):
    rows = db.info["rows"]
    await _insert(db, NewsOutcome, news_symbol_id=rows[0].id, **_scored())
    await _insert(db, NewsOutcome, news_symbol_id=rows[1].id, status="no_data", method="price",
                  scoring_eligible=True, anchor_session=A, end_session=E, reason="no end bar")
    await _insert(db, NewsSourceScoreHistory, source_id=db.info["source"].id, as_of=E, n=1,
                  hits=1, score=0.5, lift=1.25, prior_mean=0.4)


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint, values", [
    ("ck_news_outcome_scored", _scored(hit=None)),
    ("ck_news_outcome_score", _scored(score=1.5)),
    ("ck_news_outcome_verdict", _scored(verdict="happened")),
    ("ck_news_outcome_verdict", _scored(method="llm")),
    ("ck_news_outcome_reason", {"status": "no_data", "method": "price",
                                "scoring_eligible": True, "anchor_session": A,
                                "end_session": E}),
    ("ck_news_outcome_price", _scored(anchor_price=None)),
    ("ck_news_outcome_status", _scored(status="bogus", hit=None, score=None, reason="x")),
], ids=["n1_scored_without_hit", "n2_score_above_one", "n4_price_with_verdict",
        "n5_llm_scored_without_verdict", "n6_no_data_without_reason",
        "n7_price_scored_without_anchor", "n8_bogus_status"])
async def test_outcome_constraints(db, constraint, values):
    await _expect_violation(db, constraint, NewsOutcome,
                            news_symbol_id=db.info["rows"][0].id, **values)


@pytest.mark.asyncio
async def test_n3_one_outcome_per_symbol_row(db):
    row = db.info["rows"][0]
    await _insert(db, NewsOutcome, news_symbol_id=row.id, **_scored())
    await _expect_violation(db, "uq_news_outcome_symbol_row", NewsOutcome,
                            news_symbol_id=row.id, **_scored(hit=False, score=0))


@pytest.mark.asyncio
async def test_n9_one_history_row_per_source_and_day(db):
    values = {"source_id": db.info["source"].id, "as_of": E, "n": 2, "hits": 1, "score": 0.4,
              "lift": 1.0, "prior_mean": 0.4}
    await _insert(db, NewsSourceScoreHistory, **values)
    await _expect_violation(db, "uq_news_source_score_day", NewsSourceScoreHistory, **values)


@pytest.mark.asyncio
async def test_n10_history_hits_cannot_exceed_n(db):
    await _expect_violation(db, "ck_news_source_score_counts", NewsSourceScoreHistory,
                            source_id=db.info["source"].id, as_of=E, n=2, hits=3, score=0.4,
                            lift=1.0, prior_mean=0.4)
