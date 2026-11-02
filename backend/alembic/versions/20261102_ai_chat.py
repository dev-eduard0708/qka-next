"""Add QKA-Next native OpenAI chat tables on qka_next copies.

Revision ID: 20261102_ai_chat
Revises: 20261102_mcp_audit
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_ai_chat"
down_revision = "20261102_mcp_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_ai_conversation (
            id UUID PRIMARY KEY,
            qka_user_id UUID NOT NULL,
            title VARCHAR(256) NOT NULL DEFAULT 'New chat',
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_ai_conversation_user ON qka_next_ai_conversation (qka_user_id, updated_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_ai_message (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES qka_next_ai_conversation(id) ON DELETE CASCADE,
            role VARCHAR(32) NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'complete',
            error VARCHAR(1024) NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_ai_message_conversation ON qka_next_ai_message (conversation_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_ai_message_attachment (
            id UUID PRIMARY KEY,
            message_id UUID NOT NULL REFERENCES qka_next_ai_message(id) ON DELETE CASCADE,
            kind VARCHAR(32) NOT NULL,
            document_id UUID NULL,
            sample_id UUID NULL,
            display_name VARCHAR(512) NOT NULL DEFAULT '',
            source_ref VARCHAR(2048) NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_ai_message_attachment_message ON qka_next_ai_message_attachment (message_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_ai_sample_upload (
            id UUID PRIMARY KEY,
            uploaded_by_user_id UUID NOT NULL,
            file_name VARCHAR(512) NOT NULL,
            extension VARCHAR(32) NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            extracted_text TEXT NULL,
            content_sha256 VARCHAR(64) NULL,
            status INTEGER NOT NULL DEFAULT 0,
            safe_error VARCHAR(1024) NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_ai_sample_upload_user ON qka_next_ai_sample_upload (uploaded_by_user_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qka_next_ai_usage_audit (
            id UUID PRIMARY KEY,
            qka_user_id UUID NULL,
            conversation_id UUID NULL,
            model VARCHAR(128) NOT NULL DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            succeeded BOOLEAN NOT NULL DEFAULT FALSE,
            error_category VARCHAR(128) NULL,
            detail VARCHAR(512) NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_qka_next_ai_usage_audit_created ON qka_next_ai_usage_audit (created_at DESC)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP TABLE IF EXISTS qka_next_ai_usage_audit")
    op.execute("DROP TABLE IF EXISTS qka_next_ai_sample_upload")
    op.execute("DROP TABLE IF EXISTS qka_next_ai_message_attachment")
    op.execute("DROP TABLE IF EXISTS qka_next_ai_message")
    op.execute("DROP TABLE IF EXISTS qka_next_ai_conversation")
