"""Add QKA-Next MCP tool audit table on qka_next copies.

Revision ID: 20261102_mcp_audit
Revises: 20261102_multi_synology
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_mcp_audit"
down_revision = "20261102_multi_synology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_mcp_tool_audit (
            id UUID PRIMARY KEY,
            tool_name VARCHAR(64) NOT NULL,
            qka_user_id UUID NULL,
            succeeded BOOLEAN NOT NULL DEFAULT FALSE,
            error_category VARCHAR(128) NULL,
            detail VARCHAR(512) NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_mcp_tool_audit_created ON qka_next_mcp_tool_audit (created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_mcp_tool_audit_tool ON qka_next_mcp_tool_audit (tool_name)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP TABLE IF EXISTS qka_next_mcp_tool_audit")
