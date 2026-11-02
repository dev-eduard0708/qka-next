"""User directory display names + ChatGPT Business membership tracking.

Revision ID: 20261102_user_directory
Revises: 20261102_view_download
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_user_directory"
down_revision = "20261102_view_download"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    # Admin-provided directory name + optional Google profile name (separate).
    op.execute('ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "DisplayName" VARCHAR(256) NOT NULL DEFAULT \'\'')
    op.execute('ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "GoogleProfileName" VARCHAR(256) NOT NULL DEFAULT \'\'')

    # Backfill display name from local-part when empty.
    op.execute(
        """
        UPDATE allowed_email
        SET "DisplayName" = split_part("EmailNormalized", '@', 1)
        WHERE COALESCE(TRIM("DisplayName"), '') = ''
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_chatgpt_membership (
            id UUID PRIMARY KEY,
            allowed_email_id UUID NOT NULL UNIQUE,
            status INTEGER NOT NULL DEFAULT 0,
            source VARCHAR(64) NOT NULL DEFAULT 'unknown',
            member_name VARCHAR(256) NOT NULL DEFAULT '',
            workspace_role VARCHAR(64) NOT NULL DEFAULT '',
            seat_type VARCHAR(64) NOT NULL DEFAULT '',
            verified_at TIMESTAMPTZ NULL,
            last_import_batch_id UUID NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by_user_id UUID NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_chatgpt_membership_email ON qka_next_chatgpt_membership (allowed_email_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_membership_import_batch (
            id UUID PRIMARY KEY,
            source_label VARCHAR(256) NOT NULL DEFAULT 'csv_import',
            file_name VARCHAR(512) NOT NULL DEFAULT '',
            row_count INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0,
            member_count INTEGER NOT NULL DEFAULT 0,
            nonmember_count INTEGER NOT NULL DEFAULT 0,
            mark_missing_nonmember BOOLEAN NOT NULL DEFAULT FALSE,
            notes VARCHAR(2000) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL,
            created_by_user_id UUID NULL
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP TABLE IF EXISTS qka_next_membership_import_batch")
    op.execute("DROP TABLE IF EXISTS qka_next_chatgpt_membership")
    op.execute('ALTER TABLE allowed_email DROP COLUMN IF EXISTS "GoogleProfileName"')
    op.execute('ALTER TABLE allowed_email DROP COLUMN IF EXISTS "DisplayName"')
