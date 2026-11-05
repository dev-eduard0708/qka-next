"""Desktop folder browse jobs + machine enrollment proof_hash.

Revision ID: 20261106_folder_browse
Revises: 20261105_agent_deployment
Create Date: 2026-11-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_folder_browse"
down_revision: Union[str, None] = "20261105_agent_deployment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE qka_next_machine_enrollment_token "
            "ADD COLUMN IF NOT EXISTS proof_hash VARCHAR(128) NOT NULL DEFAULT ''"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_folder_browse (
                id UUID PRIMARY KEY,
                device_id UUID NOT NULL,
                requested_by_user_id UUID NOT NULL,
                employee_ad_sid VARCHAR(256) NOT NULL DEFAULT '',
                parent_path VARCHAR(2048) NOT NULL DEFAULT '',
                status INTEGER NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL DEFAULT '[]',
                error VARCHAR(2000) NOT NULL DEFAULT '',
                max_entries INTEGER NOT NULL DEFAULT 200,
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_folder_browse_device "
            "ON qka_next_desktop_folder_browse (device_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_folder_browse_status "
            "ON qka_next_desktop_folder_browse (status)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_folder_browse"))
