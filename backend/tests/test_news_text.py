"""P6a news text helpers: URL normalisation / hash, headline normalisation,
the similarity ratio and the symbol shape filter."""

import hashlib

import pytest

from app.modules.news.text import (
    SYMBOL_RE, normalize_headline, normalize_url, similar, url_hash
)

BASE = "https://benzinga.com/news/26/10/123/apple-beats"


def test_utm_params_and_fragment_are_dropped():
    assert normalize_url(BASE + "?utm_source=x&utm_medium=y#comments") == BASE


def test_non_utm_query_params_are_kept():
    assert normalize_url(BASE + "?id=7&utm_campaign=z") == BASE + "?id=7"


def test_www_is_stripped():
    assert normalize_url("https://www.benzinga.com/news/26/10/123/apple-beats") == BASE


def test_trailing_slash_is_stripped():
    assert normalize_url(BASE + "/") == BASE


def test_scheme_and_host_are_lowercased_but_path_is_not():
    assert normalize_url("HTTPS://WWW.Benzinga.COM/News/A") == "https://benzinga.com/News/A"


def test_url_hash_is_sha256_of_normalized_url():
    assert url_hash(BASE + "/?utm_source=x") == hashlib.sha256(BASE.encode()).hexdigest()
    assert url_hash("https://www.benzinga.com/news/26/10/123/apple-beats#x") == url_hash(BASE)


@pytest.mark.parametrize("empty", [None, ""])
def test_url_hash_of_empty_url_is_none(empty):
    assert url_hash(empty) is None


def test_normalize_headline_lowercases_and_collapses_punctuation():
    assert normalize_headline("  Apple's Q3: Beats -- EPS $1.20!  ") == "apple s q3 beats eps 1 20"


def test_normalize_headline_caps_at_300_chars():
    assert len(normalize_headline("a" * 400)) == 300


def test_similar_ratio_boundary_at_092():
    a = "abcdefghijklmnopqrstuvwxy"
    assert similar(a, "abcdefghijklmnopqrstuvw12") == pytest.approx(0.92)
    assert similar(a, "abcdefghijklmnopqrstuvw12") >= 0.92
    assert similar(a, "abcdefghijklmnopqrstuv123") < 0.92


def test_similar_identical_is_one():
    assert similar("apple beats", "apple beats") == 1.0


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "F", "GOOGL"])
def test_symbol_regex_accepts_equity_codes(symbol):
    assert SYMBOL_RE.match(symbol)


@pytest.mark.parametrize("symbol", ["btcusd", "BTC/USD", "", "1ABC", "ABCDEFGHIJK"])
def test_symbol_regex_rejects_non_equity_codes(symbol):
    assert not SYMBOL_RE.match(symbol)
