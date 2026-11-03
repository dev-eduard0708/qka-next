"""Widen AD protocol column for windows_integrated auth mode.

Revision ID: 20261103_ad_windows_integrated
Revises: 20261103_ad_integration
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261103_ad_windows_integrated"
down_revision = "20261103_ad_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_ad_connection
            ALTER COLUMN protocol TYPE VARCHAR(32),
            ALTER COLUMN protocol SET DEFAULT 'windows_integrated'
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_ad_connection
            ALTER COLUMN protocol TYPE VARCHAR(16),
            ALTER COLUMN protocol SET DEFAULT 'ldaps'
            """
        )
    )
