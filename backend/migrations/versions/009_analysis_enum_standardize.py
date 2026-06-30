"""Standardize ai_analysis_results.analysis_type / action enum persistence.

analysis/models.py previously used bare SAEnum(AnalysisType) / SAEnum(TradeAction)
(native_enum=True default, no values_callable), so SQLAlchemy persisted the enum
NAME ('BUY', 'SCHEDULED', ...). The whole project otherwise stores the lowercase
.value via SAEnum(..., values_callable=lambda e: [m.value for m in e],
native_enum=False) (see auth/models.py UserRole, exchange/models.py). The model
was realigned to that pattern; the columns are already plain VARCHAR (migration
001 created them as String(20)/String(10), no PG ENUM type exists).

This migration reconciles any pre-existing rows that still hold the uppercase
NAME so reads under the new values_callable mapping don't raise LookupError, and
so get_summary's .value grouping returns correct buy/sell/hold counts. The
canonical .value is exactly lower(NAME) for both enums, so LOWER() is the right,
idempotent transform (already-lowercase rows are untouched by the WHERE guard).

Must land before the Decision verdict enum is added.

Revision ID: 009
Revises: 008
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reconcile stale NAME-cased rows ('BUY' -> 'buy', 'SCHEDULED' -> 'scheduled', ...).
    # Guarded + idempotent: only rows whose value differs from its lowercase form are touched.
    op.execute(
        """
        UPDATE ai_analysis_results
        SET action = LOWER(action)
        WHERE action <> LOWER(action)
        """
    )
    op.execute(
        """
        UPDATE ai_analysis_results
        SET analysis_type = LOWER(analysis_type)
        WHERE analysis_type <> LOWER(analysis_type)
        """
    )


def downgrade() -> None:
    # Data normalization is not reversibly mappable (original NAME casing was a
    # uniform UPPER of the value); intentionally a no-op.
    pass
