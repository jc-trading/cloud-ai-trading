"""Create risk management tables (risk_limits, position_metrics, drawdown_records)

These tables are defined in app/modules/risk/models.py but were never created by
migrations 001-007, while the risk module is live-wired (main.py / celery import,
risk_tasks select(RiskLimit), Watchlist cascades into them). On a fresh DB this made
dropping a watchlist or running a risk task raise UndefinedTable. Hand-written to
match risk/models.py column-for-column (no --autogenerate).

Revision ID: 008
Revises: 007
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # risk_limits  (RiskLimit) - portfolio-level risk limit configuration
    # ------------------------------------------------------------------
    op.create_table(
        'risk_limits',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('watchlist_id', UUID(as_uuid=True), nullable=False),

        # Per-position limits
        sa.Column('max_position_size_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('max_loss_per_trade_percent', sa.Numeric(precision=5, scale=2), nullable=False),

        # Portfolio-level limits
        sa.Column('max_portfolio_loss_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('daily_loss_limit_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('max_open_positions', sa.Integer(), nullable=False),
        sa.Column('max_concentration_percent', sa.Numeric(precision=5, scale=2), nullable=False),

        # Signal quality threshold
        sa.Column('min_signal_strength', sa.Integer(), nullable=False),
        sa.Column('min_confidence_threshold', sa.Numeric(precision=5, scale=2), nullable=False),

        # Time-based limits
        sa.Column('max_position_age_days', sa.Integer(), nullable=False),
        sa.Column('max_consecutive_losses', sa.Integer(), nullable=False),

        # Position sizing configuration
        sa.Column('risk_level', sa.String(20), nullable=False),
        sa.Column('position_sizing_method', sa.String(20), nullable=False),

        # Status
        sa.Column('enabled', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),

        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_risk_limits_watchlist_id', 'risk_limits', ['watchlist_id'])

    # ------------------------------------------------------------------
    # position_metrics  (PositionMetric) - real-time per-position metrics
    # ------------------------------------------------------------------
    op.create_table(
        'position_metrics',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('position_id', UUID(as_uuid=True), nullable=False),
        sa.Column('risk_limit_id', UUID(as_uuid=True), nullable=True),

        # Current P&L tracking
        sa.Column('current_pnl', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('pnl_percent', sa.Numeric(precision=8, scale=4), nullable=False),

        # Excursion tracking (Max Favorable/Adverse)
        sa.Column('max_favorable_excursion', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('max_adverse_excursion', sa.Numeric(precision=20, scale=8), nullable=False),

        # Price and time tracking
        sa.Column('current_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('days_in_trade', sa.Integer(), nullable=False),

        # Risk metrics
        sa.Column('position_size_percent', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('concentration_impact', sa.Numeric(precision=5, scale=2), nullable=True),

        # Exit information
        sa.Column('exit_reason', sa.String(50), nullable=True),

        # Timestamps
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),

        sa.ForeignKeyConstraint(['position_id'], ['positions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['risk_limit_id'], ['risk_limits.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_position_metrics_position_id', 'position_metrics', ['position_id'])
    op.create_index('ix_position_metrics_recorded_at', 'position_metrics', ['recorded_at'])

    # ------------------------------------------------------------------
    # drawdown_records  (DrawdownRecord) - historical portfolio drawdown
    # ------------------------------------------------------------------
    op.create_table(
        'drawdown_records',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('risk_limit_id', UUID(as_uuid=True), nullable=False),
        sa.Column('watchlist_id', UUID(as_uuid=True), nullable=False),

        # Portfolio value tracking
        sa.Column('peak_equity', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('trough_equity', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('current_equity', sa.Numeric(precision=20, scale=8), nullable=False),

        # Drawdown metrics
        sa.Column('max_drawdown_percent', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column('current_drawdown_percent', sa.Numeric(precision=8, scale=4), nullable=False),

        # Additional metrics
        sa.Column('unrealized_pnl', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('realized_pnl', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('total_pnl', sa.Numeric(precision=20, scale=8), nullable=True),

        # Timestamps
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),

        sa.ForeignKeyConstraint(['risk_limit_id'], ['risk_limits.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_drawdown_records_risk_limit_id', 'drawdown_records', ['risk_limit_id'])
    op.create_index('ix_drawdown_records_watchlist_id', 'drawdown_records', ['watchlist_id'])
    op.create_index('ix_drawdown_records_recorded_at', 'drawdown_records', ['recorded_at'])


def downgrade() -> None:
    # drawdown_records
    op.drop_index('ix_drawdown_records_recorded_at', table_name='drawdown_records')
    op.drop_index('ix_drawdown_records_watchlist_id', table_name='drawdown_records')
    op.drop_index('ix_drawdown_records_risk_limit_id', table_name='drawdown_records')
    op.drop_table('drawdown_records')

    # position_metrics
    op.drop_index('ix_position_metrics_recorded_at', table_name='position_metrics')
    op.drop_index('ix_position_metrics_position_id', table_name='position_metrics')
    op.drop_table('position_metrics')

    # risk_limits
    op.drop_index('ix_risk_limits_watchlist_id', table_name='risk_limits')
    op.drop_table('risk_limits')
