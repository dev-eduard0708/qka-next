"""AD eligibility for QKA enrollment + AD-only user support columns.

Revision ID: 20261104_ad_eligibility
Revises: 20261103_ad_windows_integrated
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261104_ad_eligibility"
down_revision = "20261103_ad_windows_integrated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_ad_directory_user
            ADD COLUMN IF NOT EXISTS qka_eligible BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_ad_directory_user
            ADD COLUMN IF NOT EXISTS department_override VARCHAR(512) NOT NULL DEFAULT ''
            """
        )
    )
    bind.execute(
        text(
            """
            ALTER TABLE qka_user
            ADD COLUMN IF NOT EXISTS "DirectoryDepartment" VARCHAR(512) NOT NULL DEFAULT ''
            """
        )
    )
    bind.execute(
        text(
            """
            ALTER TABLE qka_user
            ADD COLUMN IF NOT EXISTS "ContactEmail" VARCHAR(256) NULL
            """
        )
    )
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_chat_access
            ADD COLUMN IF NOT EXISTS qka_user_id UUID NULL
            """
        )
    )
    bind.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ix_qka_next_chat_access_qka_user
            ON qka_next_chat_access (qka_user_id)
            WHERE qka_user_id IS NOT NULL
            """
        )
    )
    # Allow AD-only chat rows without an AllowedEmail (Google allowlist stays separate).
    bind.execute(
        text(
            """
            ALTER TABLE qka_next_chat_access
            ALTER COLUMN allowed_email_id DROP NOT NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("DROP INDEX IF EXISTS ix_qka_next_chat_access_qka_user"))
    bind.execute(text("ALTER TABLE qka_next_chat_access DROP COLUMN IF EXISTS qka_user_id"))
    bind.execute(text('ALTER TABLE qka_user DROP COLUMN IF EXISTS "ContactEmail"'))
    bind.execute(text('ALTER TABLE qka_user DROP COLUMN IF EXISTS "DirectoryDepartment"'))
    bind.execute(text("ALTER TABLE qka_next_ad_directory_user DROP COLUMN IF EXISTS department_override"))
    bind.execute(text("ALTER TABLE qka_next_ad_directory_user DROP COLUMN IF EXISTS qka_eligible"))
