"""notification channels / rules / log + llm_calls.billing (P4)

``notification_channels`` holds delivery targets whose secret stays in the
environment (``secret_env_key``, CHECK-restricted to the TELEGRAM_BOT_TOKEN* /
SMTP_APP_PASSWORD* families). ``notification_rules`` routes an event type to a
channel above a minimum severity; ``notification_log`` records every delivery.
Seeds the existing Telegram chat as ``telegram-default`` with one info rule per
event (today's behaviour) and a disabled ``gmail`` SMTP channel.
``llm_calls.billing`` separates API spend from subscription (Claude CLI)
estimates; existing rows become ``api``.

Revision ID: 021
Revises: 020
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '021'
down_revision: Union[str, None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENT_TYPES = (
    'fill_entry', 'fill_exit', 'stop_hit', 'data_fail_closed', 'data_warning',
    'config_rejected', 'daily_summary', 'news_high_probability', 'source_disabled',
    'llm_quota_exhausted',
)


def upgrade() -> None:
    op.add_column('llm_calls', sa.Column('billing', sa.String(length=20),
                                         server_default=sa.text("'api'"), nullable=False))
    op.create_check_constraint('ck_llm_call_billing', 'llm_calls',
                               "billing IN ('api', 'subscription')")

    op.create_table(
        'notification_channels',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('config_json', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('secret_env_key', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_notification_channel_name'),
        sa.CheckConstraint("type IN ('telegram', 'smtp')", name='ck_notification_channel_type'),
        sa.CheckConstraint(
            "(type = 'telegram' AND secret_env_key ~ '^TELEGRAM_BOT_TOKEN(_[A-Z0-9]{1,20})?$')"
            " OR (type = 'smtp' AND secret_env_key ~ '^SMTP_APP_PASSWORD(_[A-Z0-9]{1,20})?$')",
            name='ck_notification_channel_secret_env_key'),
    )

    op.create_table(
        'notification_rules',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('channel_id', sa.UUID(), nullable=False),
        sa.Column('min_severity', sa.String(length=10), server_default='info', nullable=False),
        sa.Column('quiet_hours', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'],
                                name='fk_notification_rule_channel', ondelete='CASCADE'),
        sa.UniqueConstraint('event_type', 'channel_id',
                            name='uq_notification_rule_event_channel'),
        sa.CheckConstraint("min_severity IN ('info', 'warning', 'critical')",
                           name='ck_notification_rule_min_severity'),
    )

    op.create_table(
        'notification_log',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('channel_id', sa.UUID(), nullable=True),
        sa.Column('channel_name', sa.String(length=100), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('message_preview', sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'],
                                name='fk_notification_log_channel', ondelete='SET NULL'),
        sa.CheckConstraint("status IN ('sent', 'failed', 'suppressed')",
                           name='ck_notification_log_status'),
    )
    op.create_index('ix_notification_log_sent_at', 'notification_log', ['sent_at'],
                    unique=False)

    op.execute(
        "INSERT INTO notification_channels (id, type, name, config_json, secret_env_key, enabled) "
        "VALUES "
        "(gen_random_uuid(), 'telegram', 'telegram-default', "
        "'{\"chat_id_env\": \"TELEGRAM_CHAT_ID\"}'::jsonb, 'TELEGRAM_BOT_TOKEN', true), "
        "(gen_random_uuid(), 'smtp', 'gmail', "
        "'{\"host\": \"smtp.gmail.com\", \"port\": 587, \"username_env\": \"SMTP_USER\", "
        "\"to\": []}'::jsonb, 'SMTP_APP_PASSWORD', false)"
    )
    op.execute(
        "INSERT INTO notification_rules (id, event_type, channel_id, min_severity, "
        "quiet_hours, enabled) "
        "SELECT gen_random_uuid(), e.event_type, c.id, 'info', NULL, true "
        "FROM (VALUES " + ", ".join(f"('{e}')" for e in _EVENT_TYPES) + ") AS e(event_type) "
        "CROSS JOIN notification_channels c WHERE c.name = 'telegram-default'"
    )


def downgrade() -> None:
    op.drop_index('ix_notification_log_sent_at', table_name='notification_log')
    op.drop_table('notification_log')
    op.drop_table('notification_rules')
    op.drop_table('notification_channels')
    op.drop_constraint('ck_llm_call_billing', 'llm_calls', type_='check')
    op.drop_column('llm_calls', 'billing')
