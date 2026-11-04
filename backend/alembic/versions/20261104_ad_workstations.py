"""AD workstation inventory for Desktop Agent administration.

Revision ID: 20261104_ad_workstations
Revises: 20261104_desktop_agent
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_ad_workstations"
down_revision: Union[str, None] = "20261104_desktop_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_workstation (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                object_guid VARCHAR(64) NOT NULL,
                object_sid VARCHAR(256) NOT NULL DEFAULT '',
                computer_name VARCHAR(256) NOT NULL DEFAULT '',
                dns_hostname VARCHAR(512) NOT NULL DEFAULT '',
                distinguished_name VARCHAR(2048) NOT NULL DEFAULT '',
                ou_path VARCHAR(2048) NOT NULL DEFAULT '',
                operating_system VARCHAR(512) NOT NULL DEFAULT '',
                account_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                is_present BOOLEAN NOT NULL DEFAULT TRUE,
                is_server_or_dc BOOLEAN NOT NULL DEFAULT FALSE,
                assigned_qka_user_id UUID NULL,
                desktop_device_id UUID NULL,
                resolved_ip VARCHAR(64) NOT NULL DEFAULT '',
                reachability_status INTEGER NOT NULL DEFAULT 0,
                reachability_detail VARCHAR(1024) NOT NULL DEFAULT '',
                last_checked_at TIMESTAMPTZ NULL,
                last_seen_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_ad_workstation_connection_id "
            "ON qka_next_ad_workstation (connection_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_ad_workstation_object_guid "
            "ON qka_next_ad_workstation (object_guid)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_ad_workstation_computer_name "
            "ON qka_next_ad_workstation (computer_name)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_ad_workstation_ou_path "
            "ON qka_next_ad_workstation (ou_path)"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_qka_next_ad_workstation_conn_guid "
            "ON qka_next_ad_workstation (connection_id, object_guid)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_ad_workstation"))
