"""Share-level QKA Managed access model (not enabled for existing sources).

Revision ID: 20261102_qka_managed_share
Revises: 20261102_inventory_hierarchy
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_qka_managed_share"
down_revision = "20261102_inventory_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "AccessModel" INTEGER NOT NULL DEFAULT 0')
    op.execute(
        'ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedApprovalReference" VARCHAR(512) NOT NULL DEFAULT \'\''
    )
    op.execute(
        'ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedOwnerName" VARCHAR(256) NOT NULL DEFAULT \'\''
    )
    op.execute(
        'ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedOwnerEmail" VARCHAR(256) NOT NULL DEFAULT \'\''
    )
    op.execute(
        'ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedNotes" VARCHAR(2000) NOT NULL DEFAULT \'\''
    )
    op.execute(
        'ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedServiceReadVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedEnabledAt" TIMESTAMPTZ NULL')
    op.execute('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedEnabledByUserId" UUID NULL')


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")
    # Non-destructive: keep columns.
