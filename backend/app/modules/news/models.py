"""News ingestion (P6a, M6).

``news_sources`` is the scoring unit: a platform root (``benzinga``) with one
author leaf per byline. ``news_items`` is one deduplicated story; every provider
article that was merged into it is a ``news_item_sources`` row (the dedupe key
is ``(provider, external_id)``). ``news_item_symbols`` holds one row per US
equity the story names, and carries the per-symbol claim the extractor writes
later (P6b) — a story can be bullish for one name and bearish for another.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base

SOURCE_KINDS = ("platform", "author", "company", "feed")
PROVIDERS = ("alpaca", "edgar", "rss")
ORIGINS = ("live", "backfill")
ITEM_STATUSES = ("pending", "irrelevant", "skipped", "extracted", "no_claim", "failed")
RELEVANCE = ("held", "shortlist", "pool", "watchlist", "none")
CLAIM_TYPES = ("price", "non_price")
DIRECTIONS = ("up", "down")
ACTIONS = ("buy", "sell", "review", "ignore")
REVIEW_STATUSES = ("none", "open", "done", "dismissed")
OUTCOME_STATUSES = ("scored", "no_data", "excluded")
OUTCOME_METHODS = ("price", "llm")
VERDICTS = ("happened", "not_happened", "no_evidence")
PLATFORM_BENZINGA = "benzinga"

_CLAIM_COLUMNS = ("claim_type", "direction", "probability", "expected_move_pct",
                  "horizon_days", "anchor_session", "evaluate_after", "suggested_action")


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class NewsSource(Base):
    __tablename__ = "news_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind = Column(String(12), nullable=False)
    name = Column(String(200), nullable=False)
    parent_id = Column(UUID(as_uuid=True),
                       ForeignKey("news_sources.id", ondelete="RESTRICT",
                                  name="fk_news_source_parent"),
                       nullable=True)
    enabled = Column(Boolean(), nullable=False, server_default=text("true"))
    disabled_at = Column(DateTime(timezone=True), nullable=True)
    disabled_by = Column(String(10), nullable=True)
    disabled_reason = Column(String(300), nullable=True)
    score = Column(Numeric(6, 4), nullable=True)
    lift = Column(Numeric(8, 4), nullable=True)
    n_evaluated = Column(Integer, nullable=False, server_default="0")
    hits = Column(Integer, nullable=False, server_default="0")
    auto_disable_after_n = Column(Integer, nullable=False, server_default="20")
    score_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_news_source_kind_name"),
        CheckConstraint(_in("kind", SOURCE_KINDS), name="ck_news_source_kind"),
        CheckConstraint("(enabled = (disabled_at IS NULL)) "
                        "AND ((disabled_at IS NULL) = (disabled_by IS NULL))",
                        name="ck_news_source_disabled"),
        CheckConstraint("disabled_by IS NULL OR disabled_by IN ('auto', 'manual')",
                        name="ck_news_source_disabled_by"),
        CheckConstraint("kind <> 'platform' OR parent_id IS NULL",
                        name="ck_news_source_platform_root"),
    )


class NewsItem(Base):
    __tablename__ = "news_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    market = Column(String(10), nullable=False, server_default="us")
    origin = Column(String(10), nullable=False)
    headline = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    headline_norm = Column(String(300), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    discovered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(String(16), nullable=False, server_default="pending")
    skip_reason = Column(String(40), nullable=True)
    priority = Column(SmallInteger, nullable=True)
    extract_attempts = Column(SmallInteger, nullable=False, server_default="0")
    effective_date = Column(Date, nullable=True)
    llm_feedback = Column(Text, nullable=True)
    llm_call_id = Column(UUID(as_uuid=True),
                         ForeignKey("llm_calls.id", ondelete="SET NULL",
                                    name="fk_news_item_llm_call"),
                         nullable=True)
    extracted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("origin", ORIGINS), name="ck_news_item_origin"),
        CheckConstraint(_in("status", ITEM_STATUSES), name="ck_news_item_status"),
        CheckConstraint("(status IN ('skipped', 'irrelevant')) = (skip_reason IS NOT NULL)",
                        name="ck_news_item_skip"),
        CheckConstraint("(status IN ('extracted', 'no_claim')) = (extracted_at IS NOT NULL)",
                        name="ck_news_item_extracted"),
        Index("ix_news_items_status_priority", "status", "priority", "published_at"),
        Index("ix_news_items_published", "published_at"),
    )


class NewsItemSource(Base):
    __tablename__ = "news_item_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    news_id = Column(UUID(as_uuid=True),
                     ForeignKey("news_items.id", ondelete="CASCADE",
                                name="fk_news_item_source_item"),
                     nullable=False)
    source_id = Column(UUID(as_uuid=True),
                       ForeignKey("news_sources.id", ondelete="RESTRICT",
                                  name="fk_news_item_source_source"),
                       nullable=False)
    provider = Column(String(20), nullable=False)
    external_id = Column(String(100), nullable=False)
    url = Column(Text, nullable=True)
    url_hash = Column(String(64), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False)
    updated_at_src = Column(DateTime(timezone=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_news_item_source_ext"),
        CheckConstraint(_in("provider", PROVIDERS), name="ck_news_item_source_provider"),
        Index("ix_news_item_sources_url_hash", "url_hash"),
        Index("ix_news_item_sources_news", "news_id"),
        Index("ix_news_item_sources_source", "source_id"),
    )


class NewsItemSymbol(Base):
    __tablename__ = "news_item_symbols"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    news_id = Column(UUID(as_uuid=True),
                     ForeignKey("news_items.id", ondelete="CASCADE",
                                name="fk_news_item_symbol_item"),
                     nullable=False)
    symbol = Column(String(20), nullable=False)
    market = Column(String(10), nullable=False, server_default="us")
    relevance = Column(String(10), nullable=True)
    claim_type = Column(String(10), nullable=True)
    claim_text = Column(String(200), nullable=True)
    direction = Column(String(4), nullable=True)
    probability = Column(SmallInteger, nullable=True)
    expected_move_pct = Column(Numeric(6, 2), nullable=True)
    horizon_days = Column(SmallInteger, nullable=True)
    rationale = Column(String(300), nullable=True)
    anchor_session = Column(Date, nullable=True)
    evaluate_after = Column(Date, nullable=True)
    suggested_action = Column(String(8), nullable=True)
    action_reason = Column(String(200), nullable=True)
    review_at = Column(DateTime(timezone=True), nullable=True)
    review_status = Column(String(10), nullable=False, server_default="none")
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True),
                         ForeignKey("users.id", ondelete="SET NULL",
                                    name="fk_news_item_symbol_reviewer"),
                         nullable=True)
    review_note = Column(String(500), nullable=True)
    notified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("news_id", "symbol", name="uq_news_item_symbol"),
        CheckConstraint(" AND ".join(f"(({a} IS NULL) = ({b} IS NULL))"
                                     for a, b in zip(_CLAIM_COLUMNS, _CLAIM_COLUMNS[1:])),
                        name="ck_news_symbol_claim_all"),
        CheckConstraint("(claim_text IS NULL) = (claim_type IS DISTINCT FROM 'non_price')",
                        name="ck_news_symbol_claim_text"),
        CheckConstraint(_in("relevance", RELEVANCE), name="ck_news_symbol_relevance"),
        CheckConstraint(_in("claim_type", CLAIM_TYPES), name="ck_news_symbol_claim_type"),
        CheckConstraint(_in("direction", DIRECTIONS), name="ck_news_symbol_direction"),
        CheckConstraint(_in("suggested_action", ACTIONS), name="ck_news_symbol_action"),
        CheckConstraint(_in("review_status", REVIEW_STATUSES),
                        name="ck_news_symbol_review_status"),
        CheckConstraint("probability BETWEEN 0 AND 100", name="ck_news_symbol_probability"),
        CheckConstraint("expected_move_pct > 0 AND expected_move_pct <= 50",
                        name="ck_news_symbol_move"),
        CheckConstraint("horizon_days BETWEEN 1 AND 60", name="ck_news_symbol_horizon"),
        CheckConstraint("(review_status = 'none') = (review_at IS NULL)",
                        name="ck_news_symbol_review"),
        CheckConstraint("(review_status IN ('done', 'dismissed')) = (reviewed_at IS NOT NULL)",
                        name="ck_news_symbol_reviewed"),
        Index("ix_news_item_symbols_symbol", "symbol"),
        Index("ix_news_item_symbols_review", "review_status", "review_at"),
        Index("ix_news_item_symbols_evaluate", "evaluate_after"),
    )


class NewsOutcome(Base):
    __tablename__ = "news_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    news_symbol_id = Column(UUID(as_uuid=True),
                            ForeignKey("news_item_symbols.id", ondelete="CASCADE",
                                       name="fk_news_outcome_symbol"),
                            nullable=False)
    status = Column(String(10), nullable=False)
    method = Column(String(6), nullable=False)
    scoring_eligible = Column(Boolean(), nullable=False)
    anchor_session = Column(Date, nullable=False)
    end_session = Column(Date, nullable=False)
    anchor_price = Column(Numeric(18, 6), nullable=True)
    end_price = Column(Numeric(18, 6), nullable=True)
    realized_move_pct = Column(Numeric(9, 4), nullable=True)
    spy_move_pct = Column(Numeric(9, 4), nullable=True)
    excess_move_pct = Column(Numeric(9, 4), nullable=True)
    sector_etf = Column(String(10), nullable=True)
    sector_move_pct = Column(Numeric(9, 4), nullable=True)
    hit = Column(Boolean(), nullable=True)
    score = Column(Numeric(6, 4), nullable=True)
    magnitude_error_pct = Column(Numeric(9, 4), nullable=True)
    verdict = Column(String(14), nullable=True)
    evidence = Column(String(300), nullable=True)
    llm_call_id = Column(UUID(as_uuid=True),
                         ForeignKey("llm_calls.id", ondelete="SET NULL",
                                    name="fk_news_outcome_llm_call"),
                         nullable=True)
    reason = Column(String(200), nullable=True)
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("news_symbol_id", name="uq_news_outcome_symbol_row"),
        CheckConstraint(_in("status", OUTCOME_STATUSES), name="ck_news_outcome_status"),
        CheckConstraint(_in("method", OUTCOME_METHODS), name="ck_news_outcome_method"),
        CheckConstraint(_in("verdict", VERDICTS), name="ck_news_outcome_verdict_value"),
        CheckConstraint("((status = 'scored') = (hit IS NOT NULL)) "
                        "AND ((status = 'scored') = (score IS NOT NULL))",
                        name="ck_news_outcome_scored"),
        CheckConstraint("(method = 'llm' AND status = 'scored') = (verdict IS NOT NULL)",
                        name="ck_news_outcome_verdict"),
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_news_outcome_score"),
        CheckConstraint("(status = 'scored') = (reason IS NULL)", name="ck_news_outcome_reason"),
        CheckConstraint("method <> 'price' OR status <> 'scored' OR "
                        "(anchor_price IS NOT NULL AND end_price IS NOT NULL "
                        "AND realized_move_pct IS NOT NULL AND spy_move_pct IS NOT NULL "
                        "AND excess_move_pct IS NOT NULL AND magnitude_error_pct IS NOT NULL)",
                        name="ck_news_outcome_price"),
        Index("ix_news_outcomes_evaluated", "evaluated_at"),
    )


class NewsSourceScoreHistory(Base):
    __tablename__ = "news_source_score_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(UUID(as_uuid=True),
                       ForeignKey("news_sources.id", ondelete="CASCADE",
                                  name="fk_news_source_score_source"),
                       nullable=False)
    as_of = Column(Date, nullable=False)
    n = Column(Integer, nullable=False)
    hits = Column(Integer, nullable=False)
    score = Column(Numeric(6, 4), nullable=False)
    lift = Column(Numeric(8, 4), nullable=False)
    prior_mean = Column(Numeric(6, 4), nullable=False)
    brier = Column(Numeric(6, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "as_of", name="uq_news_source_score_day"),
        CheckConstraint("n >= 1 AND hits >= 0 AND hits <= n", name="ck_news_source_score_counts"),
    )
