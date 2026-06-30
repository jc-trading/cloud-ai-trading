"""Extend ai_analysis_results into the unified Decision shape.

Grill decision #1: reuse and extend AIAnalysisResult as the single "Decision"
record instead of standing up a new table. This migration adds the Decision
columns on top of the existing analysis row:

  - asset_class      crypto/equity. NOT NULL with server_default 'crypto' so all
                     pre-existing rows (crypto-only era) survive as crypto.
  - verdict          independent go/no-go/watch enum, deliberately SEPARATE from
                     `action` (HOLD != no-go). NOT NULL, server_default 'watch'
                     (neutral / non-committal) so legacy rows get a sane verdict
                     without inventing a go/no-go signal we never computed.
  - verdict_reason   free text rationale for the verdict (nullable).
  - data_completeness JSONB per-field presence map. Carries field-level granularity
                     for Phase 3 equity FA (e.g. "EPS missing -> auto no-go").
                     NOT NULL, server_default '{}' for legacy rows.
  - ai_invoked       boolean: was AI actually called for this decision. NOT NULL,
                     server_default true — every legacy row WAS produced by an AI
                     call, so backfilling true is faithful (transparency goal).
  - ai_skip_reason   why AI was / was not invoked (nullable text).
  - position_id      nullable FK -> positions.id (ON DELETE SET NULL): the position
                     this decision relates to, if any.

Enum persistence follows the convention realigned in 009 / used project-wide:
values stored as the lowercase .value (native_enum=False, plain VARCHAR), so the
columns here are String, not a PG ENUM type.

Crypto-first: equity stays a future phase; only the column scaffolding lands now.

Revision ID: 010
Revises: 009
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # asset_class: NOT NULL, legacy rows -> 'crypto' via server_default.
    op.add_column(
        'ai_analysis_results',
        sa.Column('asset_class', sa.String(20), nullable=False, server_default='crypto'),
    )

    # verdict: independent go/no-go/watch. NOT NULL, legacy rows -> 'watch'.
    op.add_column(
        'ai_analysis_results',
        sa.Column('verdict', sa.String(20), nullable=False, server_default='watch'),
    )
    op.add_column(
        'ai_analysis_results',
        sa.Column('verdict_reason', sa.Text(), nullable=True),
    )

    # data_completeness: per-field presence map. NOT NULL, legacy rows -> {}.
    op.add_column(
        'ai_analysis_results',
        sa.Column('data_completeness', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    # ai transparency: every legacy row was AI-produced, so ai_invoked -> true.
    op.add_column(
        'ai_analysis_results',
        sa.Column('ai_invoked', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'ai_analysis_results',
        sa.Column('ai_skip_reason', sa.Text(), nullable=True),
    )

    # nullable FK to positions (decision may not be tied to a position).
    op.add_column(
        'ai_analysis_results',
        sa.Column('position_id', UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_ai_analysis_results_position_id',
        'ai_analysis_results',
        'positions',
        ['position_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_ai_analysis_results_position_id',
        'ai_analysis_results',
        ['position_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_ai_analysis_results_position_id', table_name='ai_analysis_results')
    op.drop_constraint('fk_ai_analysis_results_position_id', 'ai_analysis_results', type_='foreignkey')
    op.drop_column('ai_analysis_results', 'position_id')
    op.drop_column('ai_analysis_results', 'ai_skip_reason')
    op.drop_column('ai_analysis_results', 'ai_invoked')
    op.drop_column('ai_analysis_results', 'data_completeness')
    op.drop_column('ai_analysis_results', 'verdict_reason')
    op.drop_column('ai_analysis_results', 'verdict')
    op.drop_column('ai_analysis_results', 'asset_class')
