"""Add watchlist_id to technical_indicators for better query performance

Revision ID: 005
Revises: 004
Create Date: 2026-04-13 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add watchlist_id column to technical_indicators
    op.add_column(
        'technical_indicators',
        sa.Column('watchlist_id', sa.UUID(), nullable=False, server_default=sa.text('NULL'))
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_technical_indicators_watchlist_id',
        'technical_indicators',
        'watchlists',
        ['watchlist_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add index for query performance
    op.create_index(
        'ix_technical_indicators_watchlist_id',
        'technical_indicators',
        ['watchlist_id']
    )


def downgrade() -> None:
    # Remove index
    op.drop_index('ix_technical_indicators_watchlist_id', table_name='technical_indicators')

    # Remove foreign key
    op.drop_constraint(
        'fk_technical_indicators_watchlist_id',
        'technical_indicators',
        type_='foreignkey'
    )

    # Remove column
    op.drop_column('technical_indicators', 'watchlist_id')
