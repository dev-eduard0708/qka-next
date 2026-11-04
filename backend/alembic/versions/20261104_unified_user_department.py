"""Add AllowedEmail.DirectoryDepartment for unified Access user form.

Revision ID: 20261104_unified_user_department
Revises: 20261104_ad_archive
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_unified_user_department"
down_revision: Union[str, None] = "20261104_ad_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            'ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "DirectoryDepartment" '
            "VARCHAR(512) NOT NULL DEFAULT ''"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text('ALTER TABLE allowed_email DROP COLUMN IF EXISTS "DirectoryDepartment"'))
