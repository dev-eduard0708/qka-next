"""Allow device registration before employee assignment.

Revision ID: 20261107_device_unassigned
Revises: 20261106_folder_browse
Create Date: 2026-11-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261107_device_unassigned"
down_revision: Union[str, None] = "20261106_folder_browse"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE qka_next_desktop_device ALTER COLUMN qka_user_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE qka_next_desktop_enrollment_code ALTER COLUMN qka_user_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE qka_next_agent_deployment_job ALTER COLUMN assigned_qka_user_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE qka_next_machine_enrollment_token ALTER COLUMN qka_user_id DROP NOT NULL"))


def downgrade() -> None:
    # Non-destructive downgrade omitted — restoring NOT NULL would fail with null rows.
    pass
