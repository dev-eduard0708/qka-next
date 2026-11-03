"""Sync job extraction batch linkage and run stage for incremental indexing progress.

Revision ID: 20261103_sync_index_progress
Revises: 20261103_extraction_queue
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261103_sync_index_progress"
down_revision = "20261103_extraction_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute('ALTER TABLE synology_sync_job ADD COLUMN IF NOT EXISTS "ExtractionBatchId" UUID NULL')
    op.execute(
        'ALTER TABLE synology_sync_job ADD COLUMN IF NOT EXISTS "RunStage" VARCHAR(32) NOT NULL DEFAULT \'discovering\''
    )
    op.execute('ALTER TABLE synology_extraction_batch ADD COLUMN IF NOT EXISTS "SyncJobId" UUID NULL')
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_sync_job_extraction_batch ON synology_sync_job ("ExtractionBatchId")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_extraction_batch_sync_job ON synology_extraction_batch ("SyncJobId")'
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
    op.execute("DROP INDEX IF EXISTS ix_synology_extraction_batch_sync_job")
    op.execute("DROP INDEX IF EXISTS ix_synology_sync_job_extraction_batch")
    op.execute('ALTER TABLE synology_extraction_batch DROP COLUMN IF EXISTS "SyncJobId"')
    op.execute('ALTER TABLE synology_sync_job DROP COLUMN IF EXISTS "RunStage"')
    op.execute('ALTER TABLE synology_sync_job DROP COLUMN IF EXISTS "ExtractionBatchId"')
