"""Fundamentals cache models (Phase 3 FA).

Two local cache tables holding the *slow-moving* company data so the equity
pipeline reads from Postgres instead of burning the limited Finnhub free-tier
quota every cycle. Modeled on the market_data OHLCV cache pattern (a plain cache
table keyed by symbol with a refresh timestamp), per the merge plan:

  - company_fundamentals : one row per symbol — profile (name/industry/sector),
    shares outstanding, S&P 500 membership, average volume, market cap, the
    historical quarterly financials blob, and the next-quarter EPS/revenue
    estimate. Refreshed on a slow schedule (profile weekly, financials on an
    earnings event). `symbol` is UNIQUE — this is a per-company snapshot.
  - earnings_calendar : one row per (symbol, report_date) — the scheduled report
    date + session (bmo/amc), EPS/revenue estimates, and once reported the
    actuals + a status. Refreshed daily pre-market for estimates; actuals filled
    in after the report. UNIQUE (symbol, report_date) so a re-fetch upserts the
    same event instead of duplicating it.

Fast-changing data (live price / price reaction / intraday) is deliberately NOT
cached here — it is fetched live. `last_refreshed_at` is the group refresh
timestamp used to decide staleness before hitting the API again.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Numeric, String, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class CompanyFundamentals(Base):
    """Cached slow-moving company profile + fundamentals (one row per symbol)."""

    __tablename__ = "company_fundamentals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol = Column(String(20), nullable=False, index=True)

    # Profile
    name = Column(String(255), nullable=True)
    industry = Column(String(255), nullable=True)
    sector = Column(String(255), nullable=True)

    # Slow-moving fundamentals
    shares_outstanding = Column(Numeric(precision=20, scale=4), nullable=True)
    is_sp500 = Column(Boolean(), nullable=False, server_default=func.false())
    avg_volume = Column(Numeric(precision=20, scale=2), nullable=True)
    market_cap = Column(Numeric(precision=28, scale=2), nullable=True)

    # Historical quarterly financials (flexible blob, per-quarter rows).
    historical_financials = Column(JSONB(), nullable=True)

    # Next-quarter forward estimates.
    next_quarter_eps_estimate = Column(Numeric(precision=18, scale=4), nullable=True)
    next_quarter_revenue_estimate = Column(Numeric(precision=28, scale=2), nullable=True)

    # Group refresh timestamp — when this cached row was last pulled from source.
    last_refreshed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('symbol', name='uq_company_fundamentals_symbol'),
    )


class EarningsCalendar(Base):
    """Cached earnings-calendar events (one row per symbol + report_date)."""

    __tablename__ = "earnings_calendar"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol = Column(String(20), nullable=False, index=True)
    report_date = Column(Date(), nullable=False, index=True)

    # Session the report drops in: bmo (before market open) / amc (after close).
    time = Column(String(10), nullable=True)

    # Estimates (known ahead) vs. actuals (filled in after the report).
    eps_estimate = Column(Numeric(precision=18, scale=4), nullable=True)
    rev_estimate = Column(Numeric(precision=28, scale=2), nullable=True)
    eps_actual = Column(Numeric(precision=18, scale=4), nullable=True)
    rev_actual = Column(Numeric(precision=28, scale=2), nullable=True)

    # Lifecycle: scheduled / reported / etc.
    status = Column(String(20), nullable=True)

    # Group refresh timestamp — when this cached row was last pulled from source.
    last_refreshed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('symbol', 'report_date', name='uq_earnings_calendar_symbol_report_date'),
    )
