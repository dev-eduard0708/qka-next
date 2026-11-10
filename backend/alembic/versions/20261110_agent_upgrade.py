"""Desktop agent upgrade jobs.

Revision ID: 20261110_agent_upgrade
Revises: 20261110_desktop_inventory
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_agent_upgrade"
down_revision: Union[str, None] = "20261110_desktop_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_agent_upgrade_job (
                id UUID PRIMARY KEY,
                device_id UUID NOT NULL,
                workstation_id UUID NULL,
                requested_by_user_id UUID NOT NULL,
                status INTEGER NOT NULL DEFAULT 0,
                from_version VARCHAR(64) NOT NULL DEFAULT '',
                target_version VARCHAR(64) NOT NULL DEFAULT '',
                msi_version VARCHAR(64) NOT NULL DEFAULT '',
                last_error VARCHAR(2000) NOT NULL DEFAULT '',
                msi_exit_code INTEGER NULL,
                last_result VARCHAR(256) NOT NULL DEFAULT '',
                token_hash VARCHAR(128) NOT NULL DEFAULT '',
                proof_hash VARCHAR(128) NOT NULL DEFAULT '',
                ad_computer_sid VARCHAR(256) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                scheduled_at TIMESTAMPTZ NULL,
                upgrading_at TIMESTAMPTZ NULL,
                verifying_at TIMESTAMPTZ NULL,
                completed_at TIMESTAMPTZ NULL,
                failed_at TIMESTAMPTZ NULL,
                cancelled_at TIMESTAMPTZ NULL,
                expires_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_agent_upgrade_job_device "
            "ON qka_next_desktop_agent_upgrade_job (device_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_agent_upgrade_job_status "
            "ON qka_next_desktop_agent_upgrade_job (status)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_agent_upgrade_job"))
