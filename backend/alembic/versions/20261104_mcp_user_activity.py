"""Add MCP connection/activity timestamps on qka_user.

Revision ID: 20261104_mcp_user_activity
Revises: 20261104_unified_user_department
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_mcp_user_activity"
down_revision: Union[str, None] = "20261104_unified_user_department"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            'ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "McpConnectedAt" TIMESTAMPTZ NULL'
        )
    )
    bind.execute(
        sa.text(
            'ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "McpLastActivityAt" TIMESTAMPTZ NULL'
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text('ALTER TABLE qka_user DROP COLUMN IF EXISTS "McpLastActivityAt"'))
    bind.execute(sa.text('ALTER TABLE qka_user DROP COLUMN IF EXISTS "McpConnectedAt"'))
