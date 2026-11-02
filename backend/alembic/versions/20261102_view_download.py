"""Migrate source grants to View Documents / Download Originals.

Revision ID: 20261102_view_download
Revises: 20261102_chat_access
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_view_download"
down_revision = "20261102_chat_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute(
        "ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS capability_view BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS capability_download BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Migrate without privilege gain: any prior R/S/P → View; Download stays off.
    op.execute(
        """
        UPDATE qka_next_source_grant
        SET capability_view = (capability_read OR capability_search OR capability_preview),
            capability_download = FALSE
        WHERE TRUE
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("ALTER TABLE qka_next_source_grant DROP COLUMN IF EXISTS capability_download")
    op.execute("ALTER TABLE qka_next_source_grant DROP COLUMN IF EXISTS capability_view")
