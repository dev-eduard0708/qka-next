"""Pending Google identity link requests for dual AD+Google employees.

Revision ID: 20261104_pending_google_link
Revises: 20261104_workstation_ou_scope
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_pending_google_link"
down_revision: Union[str, None] = "20261104_workstation_ou_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_pending_google_link (
                id UUID PRIMARY KEY,
                target_qka_user_id UUID NOT NULL,
                google_sub VARCHAR(128) NOT NULL,
                google_email_normalized VARCHAR(256) NOT NULL,
                google_profile_name VARCHAR(256) NOT NULL DEFAULT '',
                status INTEGER NOT NULL DEFAULT 0,
                conflict BOOLEAN NOT NULL DEFAULT FALSE,
                conflict_reason VARCHAR(1024) NOT NULL DEFAULT '',
                conflicting_qka_user_id UUID NULL,
                external_identity_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL,
                resolved_at TIMESTAMPTZ NULL,
                resolved_by_user_id UUID NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_pending_google_link_target "
            "ON qka_next_pending_google_link (target_qka_user_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_pending_google_link_sub "
            "ON qka_next_pending_google_link (google_sub)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_pending_google_link_email "
            "ON qka_next_pending_google_link (google_email_normalized)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_qka_next_pending_google_link_status "
            "ON qka_next_pending_google_link (status)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS qka_next_pending_google_link"))
