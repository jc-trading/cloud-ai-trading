"""Equity (US-stocks) analysis module (Phase 3 merge — ported from CRT).

Currently holds the deterministic, dependency-free **entry scoring** layer:
`scoring.py` turns structured earnings / momentum inputs into an explainable
0-100 composite + a go/watch/no-go verdict for the transparency dashboard.

Nothing in here touches the DB, the network, or Claude — the scoring is a set
of pure functions so it can be unit-tested and reasoned about in isolation. The
data-sourcing (Finnhub / cache tables) and the persistence (Decision rows) live
in their own modules and feed structured values into these functions.
"""
