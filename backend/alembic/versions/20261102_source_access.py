"""Add QKA-Next source access management tables on qka_next copies.

Revision ID: 20261102_source_access
Revises: 20261102_ai_chat
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_source_access"
down_revision = "20261102_ai_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_source_scope (
            id UUID PRIMARY KEY,
            approved_share_id UUID NOT NULL,
            folder_prefix_normalized VARCHAR(2048) NOT NULL,
            trust_class INTEGER NOT NULL DEFAULT 0,
            status INTEGER NOT NULL DEFAULT 0,
            display_name VARCHAR(512) NOT NULL DEFAULT '',
            source_owner_name VARCHAR(256) NOT NULL DEFAULT '',
            source_owner_email VARCHAR(256) NOT NULL DEFAULT '',
            approval_reference VARCHAR(512) NOT NULL DEFAULT '',
            approval_notes VARCHAR(2000) NOT NULL DEFAULT '',
            approved_at TIMESTAMPTZ NULL,
            approved_by_user_id UUID NULL,
            service_read_verified BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_qka_next_source_scope_share_prefix_active
        ON qka_next_source_scope (approved_share_id, folder_prefix_normalized)
        WHERE status = 0
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_source_scope_share ON qka_next_source_scope (approved_share_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_source_grant (
            id UUID PRIMARY KEY,
            scope_id UUID NOT NULL REFERENCES qka_next_source_scope(id) ON DELETE CASCADE,
            qka_user_id UUID NOT NULL,
            capability_read BOOLEAN NOT NULL DEFAULT TRUE,
            capability_search BOOLEAN NOT NULL DEFAULT TRUE,
            capability_preview BOOLEAN NOT NULL DEFAULT TRUE,
            status INTEGER NOT NULL DEFAULT 0,
            reason VARCHAR(1024) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            created_by_user_id UUID NULL,
            updated_by_user_id UUID NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_qka_next_source_grant_scope_user_active
        ON qka_next_source_grant (scope_id, qka_user_id)
        WHERE status = 0
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_source_grant_user ON qka_next_source_grant (qka_user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_source_grant_audit (
            id UUID PRIMARY KEY,
            action VARCHAR(64) NOT NULL,
            actor_user_id UUID NULL,
            target_user_id UUID NULL,
            scope_id UUID NULL,
            grant_id UUID NULL,
            detail VARCHAR(2000) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_source_grant_audit_created ON qka_next_source_grant_audit (created_at DESC)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP TABLE IF EXISTS qka_next_source_grant_audit")
    op.execute("DROP TABLE IF EXISTS qka_next_source_grant")
    op.execute("DROP TABLE IF EXISTS qka_next_source_scope")
