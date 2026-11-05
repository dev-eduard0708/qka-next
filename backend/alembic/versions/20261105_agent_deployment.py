"""Agent deployment jobs and machine-bound enrollment tokens.

Revision ID: 20261105_agent_deployment
Revises: 20261104_pending_google_link
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_agent_deployment"
down_revision: Union[str, None] = "20261104_pending_google_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text('ALTER TABLE qka_next_desktop_device ADD COLUMN IF NOT EXISTS ad_computer_sid VARCHAR(256) NOT NULL DEFAULT \'\''))
    op.execute(sa.text('ALTER TABLE qka_next_desktop_device ADD COLUMN IF NOT EXISTS ad_computer_guid VARCHAR(64) NOT NULL DEFAULT \'\''))
    op.execute(sa.text("ALTER TABLE qka_next_desktop_device ADD COLUMN IF NOT EXISTS workstation_id UUID NULL"))
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_device_ad_computer_sid "
            "ON qka_next_desktop_device (ad_computer_sid)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_device_ad_computer_guid "
            "ON qka_next_desktop_device (ad_computer_guid)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_device_workstation_id "
            "ON qka_next_desktop_device (workstation_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_agent_deployment_job (
                id UUID PRIMARY KEY,
                workstation_id UUID NOT NULL,
                assigned_qka_user_id UUID NOT NULL,
                requested_by_user_id UUID NOT NULL,
                approved_by_user_id UUID NULL,
                status INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error VARCHAR(2000) NOT NULL DEFAULT '',
                msi_version VARCHAR(64) NOT NULL DEFAULT '',
                engine VARCHAR(64) NOT NULL DEFAULT 'blocked',
                enrollment_mode VARCHAR(64) NOT NULL DEFAULT 'machine_identity',
                gpo_target_group VARCHAR(512) NOT NULL DEFAULT '',
                desktop_device_id UUID NULL,
                confirmation_phrase VARCHAR(128) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                approved_at TIMESTAMPTZ NULL,
                scheduled_at TIMESTAMPTZ NULL,
                installed_at TIMESTAMPTZ NULL,
                cancelled_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_agent_deployment_job_workstation "
            "ON qka_next_agent_deployment_job (workstation_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_agent_deployment_job_status "
            "ON qka_next_agent_deployment_job (status)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_machine_enrollment_token (
                id UUID PRIMARY KEY,
                job_id UUID NOT NULL,
                workstation_id UUID NOT NULL,
                qka_user_id UUID NOT NULL,
                ad_computer_sid VARCHAR(256) NOT NULL,
                ad_computer_guid VARCHAR(64) NOT NULL DEFAULT '',
                token_hash VARCHAR(128) NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_machine_enrollment_token_sid "
            "ON qka_next_machine_enrollment_token (ad_computer_sid)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_machine_enrollment_token"))
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_agent_deployment_job"))
