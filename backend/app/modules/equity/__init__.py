"""Equity (US-stocks) analysis module (Phase 3 merge — ported from CRT).

Layers, smallest/purest first:
  * `scoring.py`     — deterministic entry scoring: structured earnings / momentum
    inputs -> explainable 0-100 composite + go/watch/no-go verdict + hard vetoes.
  * `universe.py`    — daily candidate selection: S&P 500 ∩ liquidity (price >=$10,
    avg_volume >=1M) universe, and today's pool = recent (1-3 trading-day) earnings
    reporters ∪ a standing ~15-name watchlist. Reads cache only (no Finnhub/Claude).
  * `risk_config.py` — equity-specific risk limits + exit rules (5% size / -2%
    daily / weekly <=3 / 3-5 concurrency / -7% · 10% trail · 15d · earnings-<5d ·
    +25% trim) and the hard-veto pre-entry gate. Kept SEPARATE from the crypto
    `RiskLimit` DB model — this is pure in-code config.
  * `research.py`    — the agent that turns one candidate into a persisted Decision.

The scoring / universe / risk-config layers are pure (or cache-only) so they can
be unit-tested and reasoned about in isolation.
"""
