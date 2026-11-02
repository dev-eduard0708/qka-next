"""Document multi Synology connection support on qka_next copies.

Revision ID: 20261102_multi_synology
Revises: 20261101_synology
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_multi_synology"
down_revision = "20261101_synology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")
    # ConnectionName already added in 20261101. Ensure index helpers for multi-row usage.
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_connection_settings_name ON synology_connection_settings ("ConnectionName")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_connection_settings_path ON synology_connection_settings ("ConfiguredSharedFolderPath")'
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP INDEX IF EXISTS ix_synology_connection_settings_name")
    op.execute("DROP INDEX IF EXISTS ix_synology_connection_settings_path")
