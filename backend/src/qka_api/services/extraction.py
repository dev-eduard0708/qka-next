"""Background document extraction queue — never runs inside search/fetch requests."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_, select, text as sql_text
from sqlalchemy.orm import Session

from qka_api.extract import (
    EXTRACT_FAILED,
    EXTRACT_PENDING,
    EXTRACT_SKIPPED,
    EXTRACT_SUCCEEDED,
    EXTRACT_UNSUPPORTED,
    EXTRACTOR_VERSION,
    extract_document,
    is_extractable,
    is_supported,
)
from qka_api.ocr_engine import ocr_status
from qka_api.services.preview_engine import libreoffice_status
from qka_api.smb import SmbCredentials, SmbGateway
from qka_shared.config import Settings
from qka_shared.models import (
    SynologyApprovedShare,
    SynologyConnectionSettings,
    SynologyDocumentContent,
    SynologyExtractionBatch,
    SynologyExtractionQueue,
    SynologyFileInventory,
)
from qka_shared.secrets import unprotect_secret
from qka_shared.unc import build_unc

log = logging.getLogger("qka.extraction")

QUEUE_PENDING = 0
QUEUE_PROCESSING = 1
QUEUE_DONE = 2
QUEUE_FAILED = 3
QUEUE_SKIPPED = 4
QUEUE_UNSUPPORTED = 5

BATCH_QUEUED = 0
BATCH_RUNNING = 1
BATCH_COMPLETED = 2
BATCH_FAILED = 3
BATCH_CANCELLED = 4

INSTANCE_ID = f"{os.environ.get('COMPUTERNAME', 'qka-next')}:extract:{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pilot_path(settings: Settings, relative_path_normalized: str) -> bool:
    """Legacy EDUARDTESTCLOUD-style prefix check (used when indexing_scope_mode=pilot)."""
    prefix = (settings.pilot_folder_prefix or "").replace("/", "\\").strip("\\").upper()
    path_key = (relative_path_normalized or "").upper()
    return (not prefix) or path_key == prefix or path_key.startswith(prefix + "\\")


def indexing_scope_mode(settings: Settings) -> str:
    mode = (getattr(settings, "indexing_scope_mode", None) or "connection").strip().lower()
    return mode if mode in {"connection", "pilot"} else "connection"


def in_indexing_scope(settings: Settings, relative_path_normalized: str) -> bool:
    """Whether a discovered inventory path is eligible for automatic content indexing.

    connection mode: Sync already limited discovery to the configured connection share/folders,
    so every present inventory file is in scope.
    pilot mode: restrict to QKA_PILOT_FOLDER_PREFIX (legacy).
    """
    if indexing_scope_mode(settings) == "connection":
        return True
    return _pilot_path(settings, relative_path_normalized)


def connection_scope_forces_outside_pilot(settings: Settings) -> bool:
    """Sync/connection indexing must bypass legacy pilot skip gates."""
    return indexing_scope_mode(settings) == "connection"


def _credentials(settings: Settings, connection: SynologyConnectionSettings) -> SmbCredentials | None:
    if int(connection.auth_mode or 0) == 0:
        return None
    secret = unprotect_secret(settings, connection.service_account_secret_protected)
    return SmbCredentials(
        username=connection.service_account_user_name or "",
        password=secret or "",
    )


def _connection_for_share(session: Session, share_id: uuid.UUID) -> SynologyConnectionSettings | None:
    """Resolve connection via PrimaryApprovedShareId (same as synology sync)."""
    return session.execute(
        select(SynologyConnectionSettings).where(SynologyConnectionSettings.primary_approved_share_id == share_id)
    ).scalars().first()


def dependency_health(settings: Settings) -> dict[str, Any]:
    ocr = ocr_status(settings)
    lo = libreoffice_status(settings)
    return {
        "ocr": ocr,
        "libreOffice": lo,
        "extractorVersion": EXTRACTOR_VERSION,
        "supportedExtensions": sorted(
            {
                "txt",
                "csv",
                "md",
                "log",
                "docx",
                "xlsx",
                "xlsm",
                "pdf",
                "pptx",
                "png",
                "jpg",
                "jpeg",
                "tif",
                "tiff",
                "doc",
                "xls",
                "ppt",
            }
        ),
        "limits": {
            "maxPages": settings.extraction_max_pages,
            "maxFileBytes": settings.extraction_max_file_bytes,
            "maxChars": settings.extraction_max_chars,
            "concurrency": settings.extraction_concurrency,
            "maxAttempts": settings.extraction_max_attempts,
            "ocrTimeoutSeconds": settings.ocr_timeout_seconds,
            "ocrMaxPixels": settings.ocr_max_pixels,
        },
    }


def content_indexing_stats(session: Session, *, share_id: uuid.UUID | None = None) -> dict[str, Any]:
    filters = []
    if share_id is not None:
        filters.append(SynologyDocumentContent.approved_share_id == share_id)
    rows = session.execute(
        select(SynologyDocumentContent.status, func.count())
        .where(*filters)
        .group_by(SynologyDocumentContent.status)
    ).all()
    by_status = {int(status): int(count) for status, count in rows}
    inventory_total = session.execute(
        select(func.count())
        .select_from(SynologyFileInventory)
        .where(
            SynologyFileInventory.is_present.is_(True),
            SynologyFileInventory.is_directory.is_(False),
            *([SynologyFileInventory.approved_share_id == share_id] if share_id else []),
        )
    ).scalar_one()
    content_total = sum(by_status.values())
    queue_pending = session.execute(
        select(func.count())
        .select_from(SynologyExtractionQueue)
        .where(SynologyExtractionQueue.status.in_([QUEUE_PENDING, QUEUE_PROCESSING]))
    ).scalar_one()
    return {
        "inventoryFiles": int(inventory_total or 0),
        "contentRows": content_total,
        "missingContentRows": max(0, int(inventory_total or 0) - content_total),
        "pending": by_status.get(EXTRACT_PENDING, 0),
        "indexed": by_status.get(EXTRACT_SUCCEEDED, 0),
        "failed": by_status.get(EXTRACT_FAILED, 0),
        "unsupported": by_status.get(EXTRACT_UNSUPPORTED, 0),
        "skipped": by_status.get(EXTRACT_SKIPPED, 0),
        "queuePending": int(queue_pending or 0),
        "extractorVersion": EXTRACTOR_VERSION,
    }


def ensure_content_row(
    session: Session,
    settings: Settings,
    row: SynologyFileInventory,
    *,
    force_outside_pilot: bool = False,
) -> SynologyDocumentContent:
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == row.id)
    ).scalar_one_or_none()
    now = _now()
    scoped = in_indexing_scope(settings, row.relative_path_normalized) or force_outside_pilot
    if content is None:
        content = SynologyDocumentContent(
            id=uuid.uuid4(),
            inventory_file_id=row.id,
            approved_share_id=row.approved_share_id,
            relative_path_normalized=row.relative_path_normalized,
            file_name=row.file_name,
            extension=row.extension,
            created_at=now,
            updated_at=now,
            status=EXTRACT_PENDING,
        )
        session.add(content)
    content.file_name = row.file_name
    content.extension = row.extension
    content.relative_path_normalized = row.relative_path_normalized
    content.approved_share_id = row.approved_share_id
    content.pilot_scoped = scoped
    content.non_confidential = scoped
    content.source_last_write_utc = row.last_write_utc
    content.updated_at = now
    if not scoped:
        content.status = EXTRACT_SKIPPED
        content.extracted_text = None
        content.chunks_json = None
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Outside the approved extraction scope."
        content.extraction_method = None
        _clear_content_vector(session, content.id)
    return content


def _clear_content_vector(session: Session, content_id: uuid.UUID) -> None:
    session.flush()
    session.execute(
        sql_text('UPDATE synology_document_content SET search_vector = NULL WHERE "Id" = CAST(:id AS uuid)'),
        {"id": str(content_id)},
    )


def _update_search_vector(session: Session, content: SynologyDocumentContent) -> None:
    session.flush()
    # Prefer simple configuration that does not English-stem Arabic text away.
    # 'simple' dictionary keeps tokens intact for EN/AR lexical search.
    session.execute(
        sql_text(
            'UPDATE synology_document_content SET search_vector = '
            "to_tsvector('simple', coalesce(\"ExtractedText\", '')) "
            'WHERE "Id" = CAST(:id AS uuid)'
        ),
        {"id": str(content.id)},
    )


def needs_extraction_retry(
    session: Session,
    settings: Settings,
    row: SynologyFileInventory,
    *,
    force_outside_pilot: bool = False,
) -> bool:
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == row.id)
    ).scalar_one_or_none()
    if content is None:
        return True
    status = int(content.status or 0)
    scoped = in_indexing_scope(settings, row.relative_path_normalized) or force_outside_pilot
    if status == EXTRACT_SKIPPED and scoped:
        return True
    if status in {EXTRACT_PENDING, EXTRACT_FAILED}:
        return is_extractable(row.extension)
    if status == EXTRACT_UNSUPPORTED:
        return is_supported(row.extension) and int(content.extractor_version or 0) < EXTRACTOR_VERSION
    if status == EXTRACT_SUCCEEDED:
        # Re-extract when the source file changed since last successful index.
        if row.last_write_utc and content.source_last_write_utc and row.last_write_utc != content.source_last_write_utc:
            return True
        if int(content.extractor_version or 0) != EXTRACTOR_VERSION:
            return True
        marker = content.safe_error or ""
        if marker.startswith("extractor_v=") and marker.split("|", 1)[0] != f"extractor_v={EXTRACTOR_VERSION}":
            return True
        if not (content.extracted_text or "").strip():
            return True
        return False
    return False


def enqueue_inventory_ids(
    session: Session,
    settings: Settings,
    inventory_ids: Iterable[uuid.UUID],
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    force_outside_pilot: bool = False,
    batch_id: uuid.UUID | None = None,
) -> SynologyExtractionBatch:
    now = _now()
    batch = None
    if batch_id is not None:
        batch = session.get(SynologyExtractionBatch, batch_id)
    if batch is None:
        batch = SynologyExtractionBatch(
            id=uuid.uuid4(),
            action=action,
            status=BATCH_QUEUED,
            actor_user_id=actor_user_id,
            force_outside_pilot=force_outside_pilot,
            created_at=now,
        )
        session.add(batch)
        session.flush()
    queued = 0
    for inventory_id in inventory_ids:
        row = session.get(SynologyFileInventory, inventory_id)
        if row is None or not row.is_present or row.is_directory:
            continue
        content = ensure_content_row(session, settings, row, force_outside_pilot=force_outside_pilot)
        if not force_outside_pilot and not in_indexing_scope(settings, row.relative_path_normalized):
            content.status = EXTRACT_SKIPPED
            batch.skipped_count += 1
            continue
        if not is_extractable(row.extension):
            content.status = EXTRACT_UNSUPPORTED
            content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|File type is not supported for extraction."
            content.extractor_version = EXTRACTOR_VERSION
            batch.unsupported_count += 1
            _clear_content_vector(session, content.id)
            continue
        # Invalidate stale text while reprocessing so fetch does not serve old content as current.
        content.status = EXTRACT_PENDING
        content.extracted_text = None
        content.chunks_json = None
        content.extracted_at = None
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Queued for extraction."
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        existing = session.execute(
            select(SynologyExtractionQueue).where(
                SynologyExtractionQueue.inventory_file_id == row.id,
                SynologyExtractionQueue.status.in_([QUEUE_PENDING, QUEUE_PROCESSING]),
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.force_outside_pilot = force_outside_pilot or existing.force_outside_pilot
            existing.batch_id = batch.id
            existing.updated_at = now
            existing.next_attempt_at = now
            queued += 1
            continue
        session.add(
            SynologyExtractionQueue(
                id=uuid.uuid4(),
                batch_id=batch.id,
                inventory_file_id=row.id,
                approved_share_id=row.approved_share_id,
                status=QUEUE_PENDING,
                force_outside_pilot=force_outside_pilot,
                priority=50 if force_outside_pilot else 100,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        queued += 1
    batch.queued_count += queued
    session.flush()
    return batch


def start_extract_missing(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID,
    share_id: uuid.UUID | None = None,
    folder_prefix: str | None = None,
    force_outside_pilot: bool = False,
) -> SynologyExtractionBatch:
    """Queue files that lack successful extraction. Does not auto-scan the entire NAS beyond filters."""
    prefix = (folder_prefix or "").replace("/", "\\").strip("\\").upper()
    now = _now()
    batch = SynologyExtractionBatch(
        id=uuid.uuid4(),
        action="extract_missing",
        status=BATCH_QUEUED,
        actor_user_id=actor_user_id,
        approved_share_id=share_id,
        folder_prefix_normalized=prefix,
        force_outside_pilot=force_outside_pilot,
        created_at=now,
    )
    session.add(batch)
    session.flush()

    query = select(SynologyFileInventory).where(
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.is_directory.is_(False),
    )
    if share_id is not None:
        query = query.where(SynologyFileInventory.approved_share_id == share_id)
    if prefix:
        query = query.where(
            or_(
                SynologyFileInventory.relative_path_normalized == prefix,
                SynologyFileInventory.relative_path_normalized.startswith(prefix + "\\"),
            )
        )
    # Cap targeted missing extraction to avoid accidental full-NAS storms.
    rows = session.execute(query.limit(500)).scalars().all()
    ids: list[uuid.UUID] = []
    for row in rows:
        if needs_extraction_retry(session, settings, row, force_outside_pilot=force_outside_pilot):
            # Only "missing" for extract_missing: no content or pending/failed/skipped-in-scope.
            content = session.execute(
                select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == row.id)
            ).scalar_one_or_none()
            if content is None or int(content.status or 0) in {
                EXTRACT_PENDING,
                EXTRACT_FAILED,
                EXTRACT_SKIPPED,
            }:
                ids.append(row.id)
            elif int(content.status or 0) == EXTRACT_SUCCEEDED and not (content.extracted_text or "").strip():
                ids.append(row.id)
    return enqueue_inventory_ids(
        session,
        settings,
        ids,
        actor_user_id=actor_user_id,
        action="extract_missing",
        force_outside_pilot=force_outside_pilot,
        batch_id=batch.id,
    )


def start_retry_failed(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID,
    share_id: uuid.UUID | None = None,
) -> SynologyExtractionBatch:
    query = (
        select(SynologyDocumentContent.inventory_file_id)
        .where(SynologyDocumentContent.status == EXTRACT_FAILED)
        .limit(500)
    )
    if share_id is not None:
        query = query.where(SynologyDocumentContent.approved_share_id == share_id)
    ids = [row[0] for row in session.execute(query).all()]
    return enqueue_inventory_ids(
        session,
        settings,
        ids,
        actor_user_id=actor_user_id,
        action="retry_failed",
        force_outside_pilot=True,
    )


def start_reindex_selected(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID,
    inventory_ids: list[uuid.UUID],
    force_outside_pilot: bool = True,
) -> SynologyExtractionBatch:
    return enqueue_inventory_ids(
        session,
        settings,
        inventory_ids[:200],
        actor_user_id=actor_user_id,
        action="reindex_selected",
        force_outside_pilot=force_outside_pilot,
    )


def start_reindex_folder(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID,
    share_id: uuid.UUID,
    folder_prefix: str,
    force_outside_pilot: bool = True,
) -> SynologyExtractionBatch:
    prefix = (folder_prefix or "").replace("/", "\\").strip("\\").upper()
    if not prefix:
        raise ValueError("folderPrefix is required for folder reindex.")
    now = _now()
    batch = SynologyExtractionBatch(
        id=uuid.uuid4(),
        action="reindex_folder",
        status=BATCH_QUEUED,
        actor_user_id=actor_user_id,
        approved_share_id=share_id,
        folder_prefix_normalized=prefix,
        force_outside_pilot=force_outside_pilot,
        created_at=now,
    )
    session.add(batch)
    session.flush()
    rows = session.execute(
        select(SynologyFileInventory.id)
        .where(
            SynologyFileInventory.approved_share_id == share_id,
            SynologyFileInventory.is_present.is_(True),
            SynologyFileInventory.is_directory.is_(False),
            or_(
                SynologyFileInventory.relative_path_normalized == prefix,
                SynologyFileInventory.relative_path_normalized.startswith(prefix + "\\"),
            ),
        )
        .limit(500)
    ).all()
    ids = [row[0] for row in rows]
    return enqueue_inventory_ids(
        session,
        settings,
        ids,
        actor_user_id=actor_user_id,
        action="reindex_folder",
        force_outside_pilot=force_outside_pilot,
        batch_id=batch.id,
    )


def serialize_batch(batch: SynologyExtractionBatch) -> dict[str, Any]:
    return {
        "id": str(batch.id),
        "action": batch.action,
        "status": batch.status,
        "queuedCount": batch.queued_count,
        "processedCount": batch.processed_count,
        "succeededCount": batch.succeeded_count,
        "failedCount": batch.failed_count,
        "skippedCount": batch.skipped_count,
        "unsupportedCount": batch.unsupported_count,
        "folderPrefixNormalized": batch.folder_prefix_normalized,
        "forceOutsidePilot": batch.force_outside_pilot,
        "safeError": batch.safe_error,
        "createdAt": batch.created_at.isoformat() if batch.created_at else None,
        "startedAt": batch.started_at.isoformat() if batch.started_at else None,
        "completedAt": batch.completed_at.isoformat() if batch.completed_at else None,
    }


def _claim_queue_item(session: Session, settings: Settings) -> SynologyExtractionQueue | None:
    now = _now()
    # Recover expired leases.
    expired = session.execute(
        select(SynologyExtractionQueue).where(
            SynologyExtractionQueue.status == QUEUE_PROCESSING,
            SynologyExtractionQueue.lease_expires_at.is_not(None),
            SynologyExtractionQueue.lease_expires_at < now,
        ).limit(20)
    ).scalars().all()
    for item in expired:
        item.status = QUEUE_PENDING
        item.lease_owner = None
        item.lease_expires_at = None
        item.safe_error = "Recovered after extraction lease expiry."
        item.updated_at = now
        item.next_attempt_at = now
    session.flush()

    item = session.execute(
        select(SynologyExtractionQueue)
        .where(
            SynologyExtractionQueue.status == QUEUE_PENDING,
            or_(
                SynologyExtractionQueue.next_attempt_at.is_(None),
                SynologyExtractionQueue.next_attempt_at <= now,
            ),
        )
        .order_by(SynologyExtractionQueue.priority.asc(), SynologyExtractionQueue.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if item is None:
        return None
    item.status = QUEUE_PROCESSING
    item.lease_owner = INSTANCE_ID
    item.lease_expires_at = now + timedelta(minutes=int(settings.extraction_lease_minutes or 15))
    item.attempt_count = int(item.attempt_count or 0) + 1
    item.updated_at = now
    if item.batch_id:
        batch = session.get(SynologyExtractionBatch, item.batch_id)
        if batch and batch.status == BATCH_QUEUED:
            batch.status = BATCH_RUNNING
            batch.started_at = now
    session.flush()
    return item


def process_extraction_queue(
    session: Session,
    settings: Settings,
    gateway: SmbGateway,
    *,
    max_items: int | None = None,
) -> int:
    """Process up to concurrency items. Returns number processed this tick."""
    limit = max_items if max_items is not None else max(1, int(settings.extraction_concurrency or 1))
    processed = 0
    for _ in range(limit):
        item = _claim_queue_item(session, settings)
        if item is None:
            break
        item_id = item.id
        try:
            with session.begin_nested():
                _process_one(session, settings, gateway, item)
                session.flush()
        except Exception:
            log.exception("extraction.item_failed queue_id=%s", item_id)
            # Savepoint rolled back the claim; re-load and mark failed so poison
            # files (e.g. NUL in extracted text) cannot stall the batch forever.
            item = session.get(SynologyExtractionQueue, item_id)
            if item is not None and item.status in (QUEUE_PENDING, QUEUE_PROCESSING):
                now = _now()
                item.status = QUEUE_FAILED
                item.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Extraction persistence failed."
                item.lease_owner = None
                item.lease_expires_at = None
                item.updated_at = now
                _bump_batch(session, item.batch_id, failed=1)
                session.flush()
        processed += 1
    _finalize_batches(session)
    return processed


def _process_one(
    session: Session,
    settings: Settings,
    gateway: SmbGateway,
    item: SynologyExtractionQueue,
) -> None:
    now = _now()
    row = session.get(SynologyFileInventory, item.inventory_file_id)
    if row is None or not row.is_present or row.is_directory:
        item.status = QUEUE_SKIPPED
        item.safe_error = "Inventory file is missing."
        item.updated_at = now
        _bump_batch(session, item.batch_id, skipped=1)
        return
    content = ensure_content_row(session, settings, row, force_outside_pilot=item.force_outside_pilot)
    scoped = in_indexing_scope(settings, row.relative_path_normalized) or item.force_outside_pilot
    if not scoped:
        content.status = EXTRACT_SKIPPED
        content.extracted_text = None
        content.chunks_json = None
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Outside the approved extraction scope."
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        item.status = QUEUE_SKIPPED
        item.updated_at = now
        _bump_batch(session, item.batch_id, skipped=1)
        return
    if not is_extractable(row.extension):
        content.status = EXTRACT_UNSUPPORTED
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|File type is not supported for extraction."
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        item.status = QUEUE_UNSUPPORTED
        item.updated_at = now
        _bump_batch(session, item.batch_id, unsupported=1)
        return
    if int(row.size_bytes or 0) > int(settings.extraction_max_file_bytes or 0):
        content.status = EXTRACT_FAILED
        content.safe_error = (
            f"extractor_v={EXTRACTOR_VERSION}|File exceeds extraction size limit "
            f"({settings.extraction_max_file_bytes} bytes)."
        )
        content.attempt_count = int(content.attempt_count or 0) + 1
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        item.status = QUEUE_FAILED
        item.safe_error = content.safe_error
        item.updated_at = now
        _bump_batch(session, item.batch_id, failed=1)
        return

    share = session.get(SynologyApprovedShare, row.approved_share_id)
    connection = _connection_for_share(session, row.approved_share_id)
    if share is None or connection is None:
        _fail_retryable(session, settings, item, content, "Approved share/connection unavailable.")
        return
    unc = build_unc(connection.host_name, share.share_name, share.folder_relative_path)
    creds = _credentials(settings, connection)
    try:
        payload = gateway.open_read(unc, row.relative_path, creds)
    except Exception:
        log.exception("extraction.read_failed file=%s", row.file_name)
        _fail_retryable(session, settings, item, content, "Read-only extraction could not open the file.")
        return

    max_chars = int(settings.extraction_max_chars or settings.max_preview_chars * 20)
    result = extract_document(
        row.extension,
        payload,
        max_chars,
        settings=settings,
        max_pages=settings.extraction_max_pages,
    )
    content.status = result.status
    content.extracted_text = result.text if result.status == EXTRACT_SUCCEEDED else None
    content.chunks_json = result.chunks_json() if result.status == EXTRACT_SUCCEEDED else None
    content.extraction_method = result.method
    content.ocr_used = bool(result.ocr_used)
    content.truncated = bool(result.truncated)
    content.incomplete = bool(result.incomplete)
    content.extractor_version = EXTRACTOR_VERSION
    content.content_sha256 = result.digest
    content.attempt_count = int(content.attempt_count or 0) + 1
    content.extracted_at = now if result.status == EXTRACT_SUCCEEDED else None
    content.source_last_write_utc = row.last_write_utc
    notes = "; ".join(result.notes)[:2000] if result.notes else None
    content.extraction_notes = notes
    detail = result.error or ""
    content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|{detail}" if detail else f"extractor_v={EXTRACTOR_VERSION}"
    content.lease_owner = None
    content.lease_expires_at = None
    content.updated_at = now
    content.pilot_scoped = scoped
    content.non_confidential = scoped

    if result.status == EXTRACT_SUCCEEDED and content.extracted_text:
        _update_search_vector(session, content)
        item.status = QUEUE_DONE
        item.safe_error = None
        _bump_batch(session, item.batch_id, succeeded=1)
    elif result.status == EXTRACT_UNSUPPORTED:
        _clear_content_vector(session, content.id)
        item.status = QUEUE_UNSUPPORTED
        item.safe_error = content.safe_error
        _bump_batch(session, item.batch_id, unsupported=1)
    elif result.status == EXTRACT_FAILED:
        _clear_content_vector(session, content.id)
        # Unsupported-like permanent OCR missing should not loop forever without change.
        permanent = "not available" in (result.error or "").lower() or "not supported" in (result.error or "").lower()
        if permanent or item.attempt_count >= int(settings.extraction_max_attempts or 5):
            item.status = QUEUE_FAILED
            item.safe_error = content.safe_error
            _bump_batch(session, item.batch_id, failed=1)
        else:
            delay = min(3600, 2 ** min(item.attempt_count, 8))
            item.status = QUEUE_PENDING
            item.next_attempt_at = now + timedelta(seconds=delay)
            item.lease_owner = None
            item.lease_expires_at = None
            item.safe_error = content.safe_error
            return
    else:
        _clear_content_vector(session, content.id)
        item.status = QUEUE_FAILED
        item.safe_error = content.safe_error
        _bump_batch(session, item.batch_id, failed=1)
    item.updated_at = now
    item.lease_owner = None
    item.lease_expires_at = None


def _fail_retryable(
    session: Session,
    settings: Settings,
    item: SynologyExtractionQueue,
    content: SynologyDocumentContent,
    message: str,
) -> None:
    now = _now()
    content.status = EXTRACT_FAILED
    content.extracted_text = None
    content.chunks_json = None
    content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|{message}"
    content.attempt_count = int(content.attempt_count or 0) + 1
    content.extractor_version = EXTRACTOR_VERSION
    content.updated_at = now
    _clear_content_vector(session, content.id)
    if item.attempt_count >= int(settings.extraction_max_attempts or 5):
        item.status = QUEUE_FAILED
        item.safe_error = content.safe_error
        item.updated_at = now
        item.lease_owner = None
        item.lease_expires_at = None
        _bump_batch(session, item.batch_id, failed=1)
        return
    delay = min(3600, 2 ** min(item.attempt_count, 8))
    item.status = QUEUE_PENDING
    item.next_attempt_at = now + timedelta(seconds=delay)
    item.safe_error = content.safe_error
    item.updated_at = now
    item.lease_owner = None
    item.lease_expires_at = None


def _bump_batch(
    session: Session,
    batch_id: uuid.UUID | None,
    *,
    succeeded: int = 0,
    failed: int = 0,
    skipped: int = 0,
    unsupported: int = 0,
) -> None:
    if batch_id is None:
        return
    batch = session.get(SynologyExtractionBatch, batch_id)
    if batch is None:
        return
    batch.processed_count += succeeded + failed + skipped + unsupported
    batch.succeeded_count += succeeded
    batch.failed_count += failed
    batch.skipped_count += skipped
    batch.unsupported_count += unsupported


def _pending_for_batch(session: Session, batch_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(SynologyExtractionQueue)
            .where(
                SynologyExtractionQueue.batch_id == batch_id,
                SynologyExtractionQueue.status.in_([QUEUE_PENDING, QUEUE_PROCESSING]),
            )
        ).scalar_one()
        or 0
    )


def _reopen_batch_if_work_remains(session: Session) -> None:
    """Recover batches/jobs marked terminal while queue work is still pending."""
    from qka_api.services.synology import JOB_SCANNING
    from qka_shared.models import SynologySyncJob, SynologySyncSettings

    now = _now()
    closed = session.execute(
        select(SynologyExtractionBatch).where(
            SynologyExtractionBatch.status.in_([BATCH_COMPLETED, BATCH_CANCELLED]),
            SynologyExtractionBatch.cancel_requested.is_(False),
        )
    ).scalars().all()
    for batch in closed:
        pending = _pending_for_batch(session, batch.id)
        if pending <= 0:
            continue
        batch.status = BATCH_RUNNING
        batch.completed_at = None
        batch.started_at = batch.started_at or now
        if batch.sync_job_id is None:
            continue
        job = session.get(SynologySyncJob, batch.sync_job_id)
        if job is None:
            continue
        job.status = JOB_SCANNING
        job.run_stage = "indexing"
        job.current_operation = "Extracting and indexing content"
        job.completed_at = None
        job.safe_error = None
        settings_row = session.execute(select(SynologySyncSettings)).scalars().first()
        if settings_row is not None:
            settings_row.current_job_id = job.id
            settings_row.updated_at = now


def _finalize_batches(session: Session) -> None:
    now = _now()
    _reopen_batch_if_work_remains(session)
    # Include queued sync batches that never claimed an item (enumeration finished with 0 extractables).
    batches = session.execute(
        select(SynologyExtractionBatch).where(
            SynologyExtractionBatch.status.in_([BATCH_QUEUED, BATCH_RUNNING])
        )
    ).scalars().all()
    for batch in batches:
        pending = _pending_for_batch(session, batch.id)
        # Sync-linked batches stay open while discovery is still adding jobs.
        if batch.sync_job_id is not None:
            from qka_shared.models import SynologySyncJob

            job = session.get(SynologySyncJob, batch.sync_job_id)
            if job is not None and not job.enumeration_complete and not job.cancel_requested:
                continue
        if pending == 0 and (batch.queued_count > 0 or batch.sync_job_id is not None):
            # Empty sync batch (nothing to extract) can complete once discovery finishes.
            if batch.status == BATCH_QUEUED and batch.queued_count == 0 and batch.sync_job_id is not None:
                batch.started_at = batch.started_at or now
            batch.status = BATCH_CANCELLED if batch.cancel_requested else BATCH_COMPLETED
            batch.completed_at = now
    _finalize_sync_jobs_for_batches(session)


def _finalize_sync_jobs_for_batches(session: Session) -> None:
    """Complete sync/resync jobs only after discovery finishes and linked extraction is terminal."""
    from qka_api.services.synology import JOB_CANCELLED, JOB_COMPLETED, JOB_FAILED, JOB_SCANNING
    from qka_shared.models import SynologySyncJob, SynologySyncSettings

    now = _now()
    jobs = session.execute(
        select(SynologySyncJob).where(
            SynologySyncJob.status == JOB_SCANNING,
            SynologySyncJob.enumeration_complete.is_(True),
            SynologySyncJob.extraction_batch_id.is_not(None),
        )
    ).scalars().all()
    for job in jobs:
        batch = session.get(SynologyExtractionBatch, job.extraction_batch_id) if job.extraction_batch_id else None
        if batch is None:
            continue
        if batch.status not in {BATCH_COMPLETED, BATCH_CANCELLED}:
            job.run_stage = "indexing"
            job.current_operation = "Extracting and indexing content"
            continue
        if job.cancel_requested or batch.cancel_requested:
            job.status = JOB_CANCELLED
            job.run_stage = "cancelled"
            job.current_operation = "Cancelled"
            job.safe_error = (
                "Synchronization was cancelled. Completed extraction results were kept; "
                "unscanned paths were not marked deleted."
            )
        elif batch.failed_count > 0:
            job.status = JOB_COMPLETED
            job.run_stage = "completed"
            job.current_operation = "Completed with errors"
            job.safe_error = (
                f"Discovery finished. Extraction completed with {batch.failed_count} failed file(s). "
                "Use Retry Failed to requeue failures."
            )
        else:
            job.status = JOB_COMPLETED
            job.run_stage = "completed"
            job.current_operation = "Completed"
            job.safe_error = None
        job.completed_at = now
        if job.status == JOB_COMPLETED and job.approved_share_id is not None:
            share = session.get(SynologyApprovedShare, job.approved_share_id)
            if share is not None:
                share.last_successful_sync_at = now
        settings_row = session.execute(select(SynologySyncSettings)).scalars().first()
        if settings_row is not None:
            if job.status == JOB_COMPLETED:
                settings_row.last_successful_job_id = job.id
                settings_row.last_successful_sync_at = now
            if settings_row.current_job_id == job.id and job.status in {
                JOB_COMPLETED,
                JOB_CANCELLED,
                JOB_FAILED,
            }:
                settings_row.current_job_id = None
            settings_row.updated_at = now


def create_sync_index_batch(
    session: Session,
    settings: Settings,
    *,
    sync_job_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    approved_share_id: uuid.UUID | None,
) -> SynologyExtractionBatch:
    """Create the extraction batch that Sync/Resync fills while discovering files."""
    now = _now()
    force = connection_scope_forces_outside_pilot(settings)
    batch = SynologyExtractionBatch(
        id=uuid.uuid4(),
        action="sync_index",
        status=BATCH_QUEUED,
        actor_user_id=actor_user_id,
        approved_share_id=approved_share_id,
        sync_job_id=sync_job_id,
        force_outside_pilot=force,
        created_at=now,
    )
    session.add(batch)
    session.flush()
    return batch


def enqueue_from_sync(
    session: Session,
    settings: Settings,
    row: SynologyFileInventory,
    *,
    force: bool = False,
    batch_id: uuid.UUID | None = None,
) -> str:
    """Enqueue extraction during Sync/Resync.

    Returns: queued | unchanged | skipped | unsupported
    """
    force_scope = connection_scope_forces_outside_pilot(settings)
    content = ensure_content_row(session, settings, row, force_outside_pilot=force_scope)
    if not in_indexing_scope(settings, row.relative_path_normalized) and not force_scope:
        content.status = EXTRACT_SKIPPED
        content.extracted_text = None
        content.chunks_json = None
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Outside the approved extraction scope."
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        if batch_id is not None:
            batch = session.get(SynologyExtractionBatch, batch_id)
            if batch is not None:
                batch.skipped_count += 1
        return "skipped"
    if not is_extractable(row.extension):
        content.status = EXTRACT_UNSUPPORTED
        content.extracted_text = None
        content.chunks_json = None
        content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|File type is not supported for extraction."
        content.extractor_version = EXTRACTOR_VERSION
        _clear_content_vector(session, content.id)
        if batch_id is not None:
            batch = session.get(SynologyExtractionBatch, batch_id)
            if batch is not None:
                batch.unsupported_count += 1
        return "unsupported"
    if not force and not needs_extraction_retry(session, settings, row, force_outside_pilot=force_scope):
        return "unchanged"
    # Mark stale while pending so search/fetch do not treat old text as current.
    content.status = EXTRACT_PENDING
    content.extracted_text = None
    content.chunks_json = None
    content.safe_error = f"extractor_v={EXTRACTOR_VERSION}|Queued for extraction."
    content.extractor_version = EXTRACTOR_VERSION
    _clear_content_vector(session, content.id)
    now = _now()
    existing = session.execute(
        select(SynologyExtractionQueue).where(
            SynologyExtractionQueue.inventory_file_id == row.id,
            SynologyExtractionQueue.status.in_([QUEUE_PENDING, QUEUE_PROCESSING]),
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.updated_at = now
        existing.next_attempt_at = now
        existing.force_outside_pilot = force_scope or existing.force_outside_pilot
        if batch_id is not None and existing.batch_id != batch_id:
            existing.batch_id = batch_id
            batch = session.get(SynologyExtractionBatch, batch_id)
            if batch is not None:
                batch.queued_count += 1
        elif batch_id is not None:
            existing.batch_id = batch_id
        return "queued"
    session.add(
        SynologyExtractionQueue(
            id=uuid.uuid4(),
            batch_id=batch_id,
            inventory_file_id=row.id,
            approved_share_id=row.approved_share_id,
            status=QUEUE_PENDING,
            force_outside_pilot=force_scope,
            priority=80,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    if batch_id is not None:
        batch = session.get(SynologyExtractionBatch, batch_id)
        if batch is not None:
            batch.queued_count += 1
    return "queued"
