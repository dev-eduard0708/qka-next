"""Content extraction queue, structured chunks, OCR metadata.

Revision ID: 20261103_extraction_queue
Revises: 20261102_qka_managed_share
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261103_extraction_queue"
down_revision = "20261102_qka_managed_share"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic upgrade against {db}.")

    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ChunksJson" TEXT NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractionMethod" VARCHAR(64) NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractorVersion" INTEGER NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "OcrUsed" BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "Truncated" BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "Incomplete" BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "AttemptCount" INTEGER NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "NextAttemptAt" TIMESTAMPTZ NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "LeaseOwner" VARCHAR(256) NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "LeaseExpiresAt" TIMESTAMPTZ NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "SourceLastWriteUtc" TIMESTAMPTZ NULL')
    op.execute('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractionNotes" VARCHAR(2000) NULL')
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS synology_extraction_batch (
            "Id" UUID PRIMARY KEY,
            "Action" VARCHAR(64) NOT NULL,
            "Status" INTEGER NOT NULL DEFAULT 0,
            "ActorUserId" UUID NULL,
            "ApprovedShareId" UUID NULL,
            "FolderPrefixNormalized" VARCHAR(2048) NOT NULL DEFAULT '',
            "ForceOutsidePilot" BOOLEAN NOT NULL DEFAULT FALSE,
            "QueuedCount" INTEGER NOT NULL DEFAULT 0,
            "ProcessedCount" INTEGER NOT NULL DEFAULT 0,
            "SucceededCount" INTEGER NOT NULL DEFAULT 0,
            "FailedCount" INTEGER NOT NULL DEFAULT 0,
            "SkippedCount" INTEGER NOT NULL DEFAULT 0,
            "UnsupportedCount" INTEGER NOT NULL DEFAULT 0,
            "CancelRequested" BOOLEAN NOT NULL DEFAULT FALSE,
            "SafeError" VARCHAR(1024) NULL,
            "CreatedAt" TIMESTAMPTZ NOT NULL,
            "StartedAt" TIMESTAMPTZ NULL,
            "CompletedAt" TIMESTAMPTZ NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS synology_extraction_queue (
            "Id" UUID PRIMARY KEY,
            "BatchId" UUID NULL,
            "InventoryFileId" UUID NOT NULL,
            "ApprovedShareId" UUID NOT NULL,
            "Status" INTEGER NOT NULL DEFAULT 0,
            "ForceOutsidePilot" BOOLEAN NOT NULL DEFAULT FALSE,
            "AttemptCount" INTEGER NOT NULL DEFAULT 0,
            "Priority" INTEGER NOT NULL DEFAULT 100,
            "LeaseOwner" VARCHAR(256) NULL,
            "LeaseExpiresAt" TIMESTAMPTZ NULL,
            "NextAttemptAt" TIMESTAMPTZ NULL,
            "SafeError" VARCHAR(1024) NULL,
            "CreatedAt" TIMESTAMPTZ NOT NULL,
            "UpdatedAt" TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_extraction_queue_status ON synology_extraction_queue ("Status", "NextAttemptAt")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_synology_extraction_queue_inventory ON synology_extraction_queue ("InventoryFileId")'
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar_one()
    if not str(db).startswith("qka_next"):
        raise RuntimeError(f"Refusing Alembic downgrade against {db}.")
