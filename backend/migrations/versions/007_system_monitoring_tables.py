"""Create system monitoring tables for health checks and metrics

Revision ID: 007
Revises: 006
Create Date: 2026-04-13 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create system_logs table
    op.create_table(
        'system_logs',
        sa.Column('id', UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('category', sa.String(50), nullable=False, index=True),  # market_data, trading, schedule, system
        sa.Column('level', sa.String(20), nullable=False, index=True),  # INFO, WARNING, ERROR, DEBUG, CRITICAL
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('task_name', sa.String(255), nullable=True, index=True),
        sa.Column('symbol', sa.String(20), nullable=True, index=True),
        sa.Column('signal_type', sa.String(50), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),  # started, completed, failed
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('event_metadata', JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes for system_logs
    op.create_index(
        'ix_system_logs_timestamp',
        'system_logs',
        ['timestamp']
    )
    op.create_index(
        'ix_system_logs_category_timestamp',
        'system_logs',
        ['category', 'timestamp']
    )
    op.create_index(
        'ix_system_logs_task_name_timestamp',
        'system_logs',
        ['task_name', 'timestamp']
    )

    # Create system_metrics table
    op.create_table(
        'system_metrics',
        sa.Column('id', UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cpu_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('memory_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('disk_percent', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('load_average_1', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('load_average_5', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('load_average_15', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('container_metrics', JSON(), nullable=True),  # Docker container metrics
        sa.Column('task_health', JSON(), nullable=True),  # Task health status
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create index for system_metrics
    op.create_index(
        'ix_system_metrics_timestamp',
        'system_metrics',
        ['timestamp']
    )

    # Create task_status table
    op.create_table(
        'task_status',
        sa.Column('id', UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('task_name', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, index=True),  # online, offline, running, idle, failed
        sa.Column('is_healthy', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_execution_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_execution_duration_ms', sa.Integer(), nullable=True),
        sa.Column('next_execution_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error_message', sa.Text(), nullable=True),
        sa.Column('total_executions', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('failed_executions', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('success_rate', sa.Numeric(precision=5, scale=2), nullable=True),  # percentage
        sa.Column('schedule_interval', sa.String(100), nullable=True),  # e.g., "60" for 60 seconds
        sa.Column('schedule_type', sa.String(50), nullable=True),  # periodic, crontab, schedule
        sa.Column('task_metadata', JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create index for task_status
    op.create_index(
        'ix_task_status_updated_at',
        'task_status',
        ['updated_at']
    )


def downgrade() -> None:
    # Drop task_status table
    op.drop_index('ix_task_status_updated_at', table_name='task_status')
    op.drop_table('task_status')

    # Drop system_metrics table
    op.drop_index('ix_system_metrics_timestamp', table_name='system_metrics')
    op.drop_table('system_metrics')

    # Drop system_logs table
    op.drop_index('ix_system_logs_task_name_timestamp', table_name='system_logs')
    op.drop_index('ix_system_logs_category_timestamp', table_name='system_logs')
    op.drop_index('ix_system_logs_timestamp', table_name='system_logs')
    op.drop_table('system_logs')
