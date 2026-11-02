"""Add chat channel access and pending allowlist grants.

Revision ID: 20261102_chat_access
Revises: 20261102_source_grant_rules
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_chat_access"
down_revision = "20261102_source_grant_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_chat_access (
            id UUID PRIMARY KEY,
            allowed_email_id UUID NOT NULL UNIQUE,
            local_chat_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            mcp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by_user_id UUID NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_chat_access_email ON qka_next_chat_access (allowed_email_id)"
    )

    op.execute("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS allowed_email_id UUID NULL")
    op.execute("ALTER TABLE qka_next_source_grant ALTER COLUMN qka_user_id DROP NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_source_grant_allowlist ON qka_next_source_grant (allowed_email_id)"
    )
    # Preserve existing MCP for allowlisted emails; Local AI Chat stays opt-in.
    op.execute(
        """
        INSERT INTO qka_next_chat_access (id, allowed_email_id, local_chat_enabled, mcp_enabled, created_at, updated_at, updated_by_user_id)
        SELECT gen_random_uuid(), ae."Id", FALSE, TRUE, NOW(), NOW(), NULL
        FROM allowed_email ae
        WHERE ae."Enabled" IS TRUE
          AND NOT EXISTS (
            SELECT 1 FROM qka_next_chat_access ca WHERE ca.allowed_email_id = ae."Id"
          )
        """
    )
    # Pending grants: active row may bind to allowlist before Google user exists.
    op.execute("DROP INDEX IF EXISTS ux_qka_next_source_grant_scope_user_active")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_qka_next_source_grant_scope_user_active
        ON qka_next_source_grant (scope_id, qka_user_id)
        WHERE status = 0 AND qka_user_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_qka_next_source_grant_scope_allowlist_active
        ON qka_next_source_grant (scope_id, allowed_email_id)
        WHERE status = 0 AND allowed_email_id IS NOT NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP INDEX IF EXISTS ux_qka_next_source_grant_scope_allowlist_active")
    op.execute("DROP INDEX IF EXISTS ux_qka_next_source_grant_scope_user_active")
    op.execute("ALTER TABLE qka_next_source_grant DROP COLUMN IF EXISTS allowed_email_id")
    op.execute("DROP TABLE IF EXISTS qka_next_chat_access")
