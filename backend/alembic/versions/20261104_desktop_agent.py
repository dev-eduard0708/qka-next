"""Windows Desktop Agent device enrollment and telemetry tables.

Revision ID: 20261104_desktop_agent
Revises: 20261104_mcp_user_activity
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_desktop_agent"
down_revision: Union[str, None] = "20261104_mcp_user_activity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_enrollment_code (
                id UUID PRIMARY KEY,
                qka_user_id UUID NOT NULL,
                code_hash VARCHAR(128) NOT NULL UNIQUE,
                label VARCHAR(256) NOT NULL DEFAULT '',
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                created_by_user_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_device (
                id UUID PRIMARY KEY,
                qka_user_id UUID NOT NULL,
                device_key_hash VARCHAR(128) NOT NULL UNIQUE,
                computer_name VARCHAR(256) NOT NULL DEFAULT '',
                device_fingerprint VARCHAR(256) NOT NULL DEFAULT '',
                domain_joined_name VARCHAR(256) NOT NULL DEFAULT '',
                agent_version VARCHAR(64) NOT NULL DEFAULT '',
                status INTEGER NOT NULL DEFAULT 0,
                indexing_state INTEGER NOT NULL DEFAULT 0,
                read_only_verified BOOLEAN NOT NULL DEFAULT FALSE,
                read_only_detail VARCHAR(1024) NOT NULL DEFAULT '',
                last_error VARCHAR(2000) NOT NULL DEFAULT '',
                files_total INTEGER NOT NULL DEFAULT 0,
                files_indexed INTEGER NOT NULL DEFAULT 0,
                files_pending INTEGER NOT NULL DEFAULT 0,
                files_failed INTEGER NOT NULL DEFAULT 0,
                files_by_type_json TEXT NOT NULL DEFAULT '{}',
                last_heartbeat_at TIMESTAMPTZ NULL,
                last_index_at TIMESTAMPTZ NULL,
                enrolled_at TIMESTAMPTZ NULL,
                revoked_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_device_folder (
                id UUID PRIMARY KEY,
                device_id UUID NOT NULL,
                path_normalized VARCHAR(2048) NOT NULL,
                include_subfolders BOOLEAN NOT NULL DEFAULT TRUE,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_desktop_device_user ON qka_next_desktop_device (qka_user_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_desktop_folder_device ON qka_next_desktop_device_folder (device_id)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_device_folder"))
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_device"))
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_enrollment_code"))
