"""Extend source grants with path/file-type rule filters and richer audit.

Revision ID: 20261102_source_grant_rules
Revises: 20261102_source_access
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_source_grant_rules"
down_revision = "20261102_source_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS path_mode INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS include_subfolders BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS selected_paths_json TEXT NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS file_types_json TEXT NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS employee_email VARCHAR(256) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS share_name VARCHAR(256) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS before_detail TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS after_detail TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute(
        """
        ALTER TABLE qka_next_source_grant
          DROP COLUMN IF EXISTS path_mode,
          DROP COLUMN IF EXISTS include_subfolders,
          DROP COLUMN IF EXISTS selected_paths_json,
          DROP COLUMN IF EXISTS file_types_json
        """
    )
    op.execute(
        """
        ALTER TABLE qka_next_source_grant_audit
          DROP COLUMN IF EXISTS employee_email,
          DROP COLUMN IF EXISTS share_name,
          DROP COLUMN IF EXISTS before_detail,
          DROP COLUMN IF EXISTS after_detail
        """
    )
