"""Workstation-specific AD OU sync scope.

Revision ID: 20261104_workstation_ou_scope
Revises: 20261104_ad_workstations
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_workstation_ou_scope"
down_revision: Union[str, None] = "20261104_ad_workstations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_workstation_ou_scope (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                distinguished_name VARCHAR(2048) NOT NULL,
                name VARCHAR(512) NOT NULL DEFAULT '',
                selected BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_ad_workstation_ou_scope_connection_id "
            "ON qka_next_ad_workstation_ou_scope (connection_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_qka_next_ad_workstation_ou_scope_conn_dn "
            "ON qka_next_ad_workstation_ou_scope (connection_id, distinguished_name)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_ad_workstation_ou_scope"))
