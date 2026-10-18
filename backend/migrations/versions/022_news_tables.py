"""news sources / items / item sources / item symbols (P6a)

``news_sources`` is the scoring unit: a ``benzinga`` platform root (seeded
here) with one author leaf per byline. ``news_items`` is one deduplicated story;
each provider article merged into it is a ``news_item_sources`` row, unique on
``(provider, external_id)``. ``news_item_symbols`` is one row per US equity the
story names and carries the per-symbol claim written later by the extractor.

Revision ID: 022
Revises: 021
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '022'
down_revision: Union[str, None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_sources',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('parent_id', sa.UUID(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disabled_by', sa.String(length=10), nullable=True),
        sa.Column('disabled_reason', sa.String(length=300), nullable=True),
        sa.Column('score', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('lift', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('n_evaluated', sa.Integer(), server_default='0', nullable=False),
        sa.Column('hits', sa.Integer(), server_default='0', nullable=False),
        sa.Column('auto_disable_after_n', sa.Integer(), server_default='20', nullable=False),
        sa.Column('score_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['parent_id'], ['news_sources.id'],
                                name='fk_news_source_parent', ondelete='RESTRICT'),
        sa.UniqueConstraint('kind', 'name', name='uq_news_source_kind_name'),
        sa.CheckConstraint("kind IN ('platform', 'author', 'company', 'feed')",
                           name='ck_news_source_kind'),
        sa.CheckConstraint("(enabled = (disabled_at IS NULL)) "
                           "AND ((disabled_at IS NULL) = (disabled_by IS NULL))",
                           name='ck_news_source_disabled'),
        sa.CheckConstraint("disabled_by IS NULL OR disabled_by IN ('auto', 'manual')",
                           name='ck_news_source_disabled_by'),
        sa.CheckConstraint("kind <> 'platform' OR parent_id IS NULL",
                           name='ck_news_source_platform_root'),
    )

    op.create_table(
        'news_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('market', sa.String(length=10), server_default='us', nullable=False),
        sa.Column('origin', sa.String(length=10), nullable=False),
        sa.Column('headline', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('headline_norm', sa.String(length=300), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('discovered_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('skip_reason', sa.String(length=40), nullable=True),
        sa.Column('priority', sa.SmallInteger(), nullable=True),
        sa.Column('extract_attempts', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('effective_date', sa.Date(), nullable=True),
        sa.Column('llm_feedback', sa.Text(), nullable=True),
        sa.Column('llm_call_id', sa.UUID(), nullable=True),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['llm_call_id'], ['llm_calls.id'],
                                name='fk_news_item_llm_call', ondelete='SET NULL'),
        sa.CheckConstraint("origin IN ('live', 'backfill')", name='ck_news_item_origin'),
        sa.CheckConstraint("status IN ('pending', 'irrelevant', 'skipped', 'extracted', "
                           "'no_claim', 'failed')", name='ck_news_item_status'),
        sa.CheckConstraint("(status IN ('skipped', 'irrelevant')) = (skip_reason IS NOT NULL)",
                           name='ck_news_item_skip'),
        sa.CheckConstraint("(status IN ('extracted', 'no_claim')) = (extracted_at IS NOT NULL)",
                           name='ck_news_item_extracted'),
    )
    op.create_index('ix_news_items_status_priority', 'news_items',
                    ['status', 'priority', 'published_at'], unique=False)
    op.create_index('ix_news_items_published', 'news_items', ['published_at'], unique=False)

    op.create_table(
        'news_item_sources',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('news_id', sa.UUID(), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('external_id', sa.String(length=100), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('url_hash', sa.String(length=64), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at_src', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['news_id'], ['news_items.id'],
                                name='fk_news_item_source_item', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_id'], ['news_sources.id'],
                                name='fk_news_item_source_source', ondelete='RESTRICT'),
        sa.UniqueConstraint('provider', 'external_id', name='uq_news_item_source_ext'),
        sa.CheckConstraint("provider IN ('alpaca', 'edgar', 'rss')",
                           name='ck_news_item_source_provider'),
    )
    op.create_index('ix_news_item_sources_url_hash', 'news_item_sources', ['url_hash'],
                    unique=False)
    op.create_index('ix_news_item_sources_news', 'news_item_sources', ['news_id'],
                    unique=False)
    op.create_index('ix_news_item_sources_source', 'news_item_sources', ['source_id'],
                    unique=False)

    op.create_table(
        'news_item_symbols',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('news_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('market', sa.String(length=10), server_default='us', nullable=False),
        sa.Column('relevance', sa.String(length=10), nullable=True),
        sa.Column('claim_type', sa.String(length=10), nullable=True),
        sa.Column('claim_text', sa.String(length=200), nullable=True),
        sa.Column('direction', sa.String(length=4), nullable=True),
        sa.Column('probability', sa.SmallInteger(), nullable=True),
        sa.Column('expected_move_pct', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('horizon_days', sa.SmallInteger(), nullable=True),
        sa.Column('rationale', sa.String(length=300), nullable=True),
        sa.Column('anchor_session', sa.Date(), nullable=True),
        sa.Column('evaluate_after', sa.Date(), nullable=True),
        sa.Column('suggested_action', sa.String(length=8), nullable=True),
        sa.Column('action_reason', sa.String(length=200), nullable=True),
        sa.Column('review_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_status', sa.String(length=10), server_default='none',
                  nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', sa.UUID(), nullable=True),
        sa.Column('review_note', sa.String(length=500), nullable=True),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['news_id'], ['news_items.id'],
                                name='fk_news_item_symbol_item', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'],
                                name='fk_news_item_symbol_reviewer', ondelete='SET NULL'),
        sa.UniqueConstraint('news_id', 'symbol', name='uq_news_item_symbol'),
        sa.CheckConstraint(
            "((claim_type IS NULL) = (direction IS NULL))"
            " AND ((direction IS NULL) = (probability IS NULL))"
            " AND ((probability IS NULL) = (expected_move_pct IS NULL))"
            " AND ((expected_move_pct IS NULL) = (horizon_days IS NULL))"
            " AND ((horizon_days IS NULL) = (anchor_session IS NULL))"
            " AND ((anchor_session IS NULL) = (evaluate_after IS NULL))"
            " AND ((evaluate_after IS NULL) = (suggested_action IS NULL))",
            name='ck_news_symbol_claim_all'),
        sa.CheckConstraint("(claim_text IS NULL) = (claim_type IS DISTINCT FROM 'non_price')",
                           name='ck_news_symbol_claim_text'),
        sa.CheckConstraint("relevance IN ('held', 'shortlist', 'pool', 'watchlist', 'none')",
                           name='ck_news_symbol_relevance'),
        sa.CheckConstraint("claim_type IN ('price', 'non_price')",
                           name='ck_news_symbol_claim_type'),
        sa.CheckConstraint("direction IN ('up', 'down')", name='ck_news_symbol_direction'),
        sa.CheckConstraint("suggested_action IN ('buy', 'sell', 'review', 'ignore')",
                           name='ck_news_symbol_action'),
        sa.CheckConstraint("review_status IN ('none', 'open', 'done', 'dismissed')",
                           name='ck_news_symbol_review_status'),
        sa.CheckConstraint("probability BETWEEN 0 AND 100", name='ck_news_symbol_probability'),
        sa.CheckConstraint("expected_move_pct > 0 AND expected_move_pct <= 50",
                           name='ck_news_symbol_move'),
        sa.CheckConstraint("horizon_days BETWEEN 1 AND 60", name='ck_news_symbol_horizon'),
        sa.CheckConstraint("(review_status = 'none') = (review_at IS NULL)",
                           name='ck_news_symbol_review'),
        sa.CheckConstraint("(review_status IN ('done', 'dismissed')) = (reviewed_at IS NOT NULL)",
                           name='ck_news_symbol_reviewed'),
    )
    op.create_index('ix_news_item_symbols_symbol', 'news_item_symbols', ['symbol'],
                    unique=False)
    op.create_index('ix_news_item_symbols_review', 'news_item_symbols',
                    ['review_status', 'review_at'], unique=False)
    op.create_index('ix_news_item_symbols_evaluate', 'news_item_symbols',
                    ['evaluate_after'], unique=False)

    op.execute("INSERT INTO news_sources (id, kind, name) "
               "VALUES (gen_random_uuid(), 'platform', 'benzinga')")


def downgrade() -> None:
    op.drop_index('ix_news_item_symbols_evaluate', table_name='news_item_symbols')
    op.drop_index('ix_news_item_symbols_review', table_name='news_item_symbols')
    op.drop_index('ix_news_item_symbols_symbol', table_name='news_item_symbols')
    op.drop_table('news_item_symbols')
    op.drop_index('ix_news_item_sources_source', table_name='news_item_sources')
    op.drop_index('ix_news_item_sources_news', table_name='news_item_sources')
    op.drop_index('ix_news_item_sources_url_hash', table_name='news_item_sources')
    op.drop_table('news_item_sources')
    op.drop_index('ix_news_items_published', table_name='news_items')
    op.drop_index('ix_news_items_status_priority', table_name='news_items')
    op.drop_table('news_items')
    op.drop_table('news_sources')
