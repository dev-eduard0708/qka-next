"""Desktop workstation inventory + folder browse cache.

Revision ID: 20261110_desktop_inventory
Revises: 20261108_deploy_progress
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_desktop_inventory"
down_revision: Union[str, None] = "20261108_deploy_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_device_inventory (
                device_id UUID PRIMARY KEY,
                workstation_id UUID NULL,
                hostname VARCHAR(256) NOT NULL DEFAULT '',
                domain_name VARCHAR(256) NOT NULL DEFAULT '',
                os_edition VARCHAR(256) NOT NULL DEFAULT '',
                os_version VARCHAR(128) NOT NULL DEFAULT '',
                os_build VARCHAR(64) NOT NULL DEFAULT '',
                manufacturer VARCHAR(256) NOT NULL DEFAULT '',
                model VARCHAR(256) NOT NULL DEFAULT '',
                serial_number VARCHAR(256) NOT NULL DEFAULT '',
                cpu_model VARCHAR(512) NOT NULL DEFAULT '',
                cpu_physical_cores INTEGER NOT NULL DEFAULT 0,
                cpu_logical_processors INTEGER NOT NULL DEFAULT 0,
                ram_bytes BIGINT NOT NULL DEFAULT 0,
                agent_version VARCHAR(64) NOT NULL DEFAULT '',
                disks_json TEXT NOT NULL DEFAULT '[]',
                volumes_json TEXT NOT NULL DEFAULT '[]',
                known_folders_json TEXT NOT NULL DEFAULT '[]',
                hardware_hash VARCHAR(128) NOT NULL DEFAULT '',
                volumes_hash VARCHAR(128) NOT NULL DEFAULT '',
                hardware_synced_at TIMESTAMPTZ NULL,
                volumes_synced_at TIMESTAMPTZ NULL,
                refresh_requested_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_device_inventory_ws "
            "ON qka_next_desktop_device_inventory (workstation_id)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_desktop_folder_cache (
                id UUID PRIMARY KEY,
                device_id UUID NOT NULL,
                parent_path VARCHAR(2048) NOT NULL DEFAULT '',
                nodes_json TEXT NOT NULL DEFAULT '[]',
                error VARCHAR(2000) NOT NULL DEFAULT '',
                content_hash VARCHAR(128) NOT NULL DEFAULT '',
                synced_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_qka_next_desktop_folder_cache_device_parent "
            "ON qka_next_desktop_folder_cache (device_id, parent_path)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_desktop_folder_cache_device "
            "ON qka_next_desktop_folder_cache (device_id)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_folder_cache"))
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_desktop_device_inventory"))
