"""MCP document handoff and indexing confirmation tables.

Revision ID: 20261103_mcp_capabilities
Revises: 20261103_sync_index_progress
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261103_mcp_capabilities"
down_revision = "20261103_sync_index_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_mcp_document_handoff (
                id UUID PRIMARY KEY,
                qka_user_id UUID NOT NULL,
                inventory_file_id UUID NOT NULL,
                purpose VARCHAR(32) NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_mcp_document_handoff_user "
            "ON qka_next_mcp_document_handoff (qka_user_id)"
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_mcp_document_handoff_file "
            "ON qka_next_mcp_document_handoff (inventory_file_id)"
        )
    )
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_mcp_indexing_confirmation (
                id UUID PRIMARY KEY,
                qka_user_id UUID NOT NULL,
                operation VARCHAR(64) NOT NULL,
                source_id UUID NULL,
                document_ids_json TEXT NOT NULL DEFAULT '[]',
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL,
                created_by_user_id UUID NOT NULL
            )
            """
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_mcp_indexing_confirmation_user "
            "ON qka_next_mcp_indexing_confirmation (qka_user_id)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("DROP TABLE IF EXISTS qka_next_mcp_indexing_confirmation"))
    bind.execute(text("DROP TABLE IF EXISTS qka_next_mcp_document_handoff"))
