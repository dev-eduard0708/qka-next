"""Add install progress fields to agent deployment jobs.

Revision ID: 20261108_deploy_progress
Revises: 20261107_device_unassigned
Create Date: 2026-11-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_deploy_progress"
down_revision: Union[str, None] = "20261107_device_unassigned"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE qka_next_agent_deployment_job "
            "ADD COLUMN IF NOT EXISTS install_started_at TIMESTAMPTZ NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE qka_next_agent_deployment_job "
            "ADD COLUMN IF NOT EXISTS msi_completed_at TIMESTAMPTZ NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE qka_next_agent_deployment_job "
            "ADD COLUMN IF NOT EXISTS msi_exit_code INTEGER NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE qka_next_agent_deployment_job "
            "ADD COLUMN IF NOT EXISTS last_result VARCHAR(256) NOT NULL DEFAULT ''"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE qka_next_agent_deployment_job DROP COLUMN IF EXISTS last_result"))
    op.execute(sa.text("ALTER TABLE qka_next_agent_deployment_job DROP COLUMN IF EXISTS msi_exit_code"))
    op.execute(sa.text("ALTER TABLE qka_next_agent_deployment_job DROP COLUMN IF EXISTS msi_completed_at"))
    op.execute(sa.text("ALTER TABLE qka_next_agent_deployment_job DROP COLUMN IF EXISTS install_started_at"))
