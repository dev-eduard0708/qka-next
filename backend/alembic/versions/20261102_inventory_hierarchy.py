"""Persist Synology folder hierarchy on synology_file_inventory.

Revision ID: 20261102_inventory_hierarchy
Revises: 20261102_user_directory
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261102_inventory_hierarchy"
down_revision = "20261102_user_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute('ALTER TABLE synology_file_inventory ADD COLUMN IF NOT EXISTS "IsDirectory" BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute(
        'ALTER TABLE synology_file_inventory ADD COLUMN IF NOT EXISTS "ParentPathNormalized" VARCHAR(2048) NOT NULL DEFAULT \'\''
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_synology_inventory_share_present
        ON synology_file_inventory ("ApprovedShareId", "IsPresent")
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_synology_inventory_parent
        ON synology_file_inventory ("ApprovedShareId", "ParentPathNormalized", "IsPresent")
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_synology_inventory_dir
        ON synology_file_inventory ("ApprovedShareId", "IsDirectory", "IsPresent")
        """
    )

    op.execute(
        r"""
        UPDATE synology_file_inventory
        SET "ParentPathNormalized" = CASE
            WHEN position(E'\\' in "RelativePathNormalized") > 0
            THEN left(
                "RelativePathNormalized",
                length("RelativePathNormalized") - strpos(reverse("RelativePathNormalized"), E'\\')
            )
            ELSE ''
        END
        WHERE COALESCE("ParentPathNormalized", '') = ''
        """
    )

    op.execute(
        r"""
        WITH RECURSIVE prefixes AS (
            SELECT DISTINCT "ApprovedShareId" AS share_id, "ParentPathNormalized"::varchar(2048) AS path
            FROM synology_file_inventory
            WHERE "ParentPathNormalized" <> ''
            UNION
            SELECT
                share_id,
                (
                    CASE
                        WHEN position(E'\\' in path) > 0
                        THEN left(path, length(path) - strpos(reverse(path), E'\\'))
                        ELSE ''
                    END
                )::varchar(2048)
            FROM prefixes
            WHERE position(E'\\' in path) > 0
        )
        INSERT INTO synology_file_inventory (
            "Id",
            "ApprovedShareId",
            "RelativePath",
            "RelativePathNormalized",
            "FileName",
            "Extension",
            "FileTypeCategory",
            "SizeBytes",
            "SyncStatus",
            "AclCaptureStatus",
            "IsPresent",
            "CreatedAt",
            "UpdatedAt",
            "IsDirectory",
            "ParentPathNormalized"
        )
        SELECT
            gen_random_uuid(),
            p.share_id,
            p.path,
            p.path,
            reverse(split_part(reverse(p.path), E'\\', 1)),
            '',
            0,
            0,
            0,
            0,
            TRUE,
            NOW(),
            NOW(),
            TRUE,
            CASE
                WHEN position(E'\\' in p.path) > 0
                THEN left(p.path, length(p.path) - strpos(reverse(p.path), E'\\'))
                ELSE ''
            END
        FROM prefixes p
        WHERE p.path <> ''
          AND NOT EXISTS (
              SELECT 1
              FROM synology_file_inventory i
              WHERE i."ApprovedShareId" = p.share_id
                AND i."RelativePathNormalized" = p.path
          )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP INDEX IF EXISTS ix_synology_inventory_dir")
    op.execute("DROP INDEX IF EXISTS ix_synology_inventory_parent")
    op.execute("DROP INDEX IF EXISTS ix_synology_inventory_share_present")
    # Keep folder rows and columns; dropping them would destroy hierarchy data.
