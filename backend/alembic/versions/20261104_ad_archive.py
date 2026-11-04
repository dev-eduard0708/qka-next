"""AD directory user archive flag (QKA-only).

Revision ID: 20261104_ad_archive
Revises: 20261104_ad_eligibility
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261104_ad_archive"
down_revision = "20261104_ad_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_ad_directory_user
            ADD COLUMN IF NOT EXISTS qka_archived BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE qka_next_ad_directory_user DROP COLUMN IF EXISTS qka_archived"))
