"""Additive Synology connector columns on qka_next copies only.

Revision ID: 20261101_synology
Revises:
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261101_synology"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")
    op.execute('ALTER TABLE synology_connection_settings ADD COLUMN IF NOT EXISTS "ConnectionName" varchar(256)')
    op.execute('ALTER TABLE synology_file_inventory ADD COLUMN IF NOT EXISTS "LastSeenJobId" uuid')


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute('ALTER TABLE synology_connection_settings DROP COLUMN IF EXISTS "ConnectionName"')
