from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text as sql_text
from sqlalchemy.orm import Session

from qka_api.extract import EXTRACT_SUCCEEDED, extract_bytes, is_supported
from qka_api.services.inventory_hierarchy import parent_normalized, present_file_filters
from qka_api.smb import ACL_CAPTURED, EnumeratedFile, SmbCredentials, SmbError, SmbGateway
from qka_shared.config import Settings
from qka_shared.models import (
    SynologyApprovedShare,
    SynologyConnectionSettings,
    SynologyDocumentContent,
    SynologyFileInventory,
    SynologySyncJob,
    SynologySyncJobLease,
    SynologySyncSettings,
)
from qka_shared.secrets import protect_secret, unprotect_secret
from qka_shared.unc import UncError, build_unc, parse_unc

log = logging.getLogger("qka.synology")

CONNECTION_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000010")
SETTINGS_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000011")
LEASE_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000012")
INSTANCE_ID = f"{os.environ.get('COMPUTERNAME', 'qka-next')}:{uuid.uuid4().hex}"
LEASE_MINUTES = 5
AUTH_SERVICE = 0
AUTH_PASSWORD = 1
STATUS_UNKNOWN = 0
STATUS_CONNECTED = 1
STATUS_FAILED = 2
JOB_QUEUED = 0
JOB_SCANNING = 1
JOB_COMPLETED = 2
JOB_FAILED = 3
JOB_CANCELLED = 4
TRIGGER_MANUAL = 0
TRIGGER_RESYNC = 1
SYNC_NEW = 0
SYNC_MODIFIED = 1
SYNC_UNCHANGED = 2
SYNC_REMOVED = 3
TYPE_OTHER = 0
TYPE_EXCEL = 1
TYPE_PDF = 2
TYPE_WORD = 3
TYPE_PPT = 4
TYPE_CSV = 5


class SynologyValidationError(ValueError):
    pass


class SynologyConflictError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify(extension: str) -> int:
    ext = (extension or "").lower().lstrip(".")
    if ext in {"xls", "xlsx", "xlsm"}:
        return TYPE_EXCEL
    if ext == "pdf":
        return TYPE_PDF
    if ext in {"doc", "docx"}:
        return TYPE_WORD
    if ext in {"ppt", "pptx"}:
        return TYPE_PPT
    if ext == "csv":
        return TYPE_CSV
    return TYPE_OTHER


def _get_or_create_settings(session: Session) -> SynologySyncSettings:
    row = session.get(SynologySyncSettings, SETTINGS_ID)
    if row is None:
        row = SynologySyncSettings(id=SETTINGS_ID, updated_at=_now())
        session.add(row)
        session.flush()
    return row


def connection_dto(row: SynologyConnectionSettings) -> dict:
    return {
        "id": str(row.id),
        "name": row.connection_name or row.configured_shared_folder_path or row.host_name or "Synology NAS",
        "hostName": row.host_name,
        "configuredSharedFolderPath": row.configured_shared_folder_path,
        "primaryApprovedShareId": str(row.primary_approved_share_id) if row.primary_approved_share_id else None,
        "authMode": row.auth_mode,
        "serviceAccountUserName": row.service_account_user_name,
        "serviceAccountSecretConfigured": bool(row.service_account_secret_protected),
        "connectionStatus": row.connection_status,
        "port445Reachable": row.port445_reachable,
        "authenticatedShareAccess": row.authenticated_share_access,
        "lastSuccessfulTestAt": row.last_successful_test_at.isoformat() if row.last_successful_test_at else None,
        "lastSafeError": row.last_safe_error,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _require_connection(session: Session, connection_id: uuid.UUID) -> SynologyConnectionSettings:
    row = session.get(SynologyConnectionSettings, connection_id)
    if row is None:
        raise SynologyValidationError("Synology connection was not found.")
    return row


def _connection_for_share(session: Session, share_id: uuid.UUID) -> SynologyConnectionSettings | None:
    return session.execute(
        select(SynologyConnectionSettings).where(SynologyConnectionSettings.primary_approved_share_id == share_id)
    ).scalars().first()


def list_connections(session: Session) -> list[dict]:
    rows = session.execute(
        select(SynologyConnectionSettings).order_by(SynologyConnectionSettings.connection_name.nulls_last(), SynologyConnectionSettings.host_name)
    ).scalars().all()
    return [connection_dto(row) for row in rows]


def get_connection(session: Session, connection_id: uuid.UUID | None = None) -> dict:
    if connection_id is not None:
        return connection_dto(_require_connection(session, connection_id))
    rows = list_connections(session)
    if rows:
        return rows[0]
    # Preserve a default empty connection for first-time setup.
    row = SynologyConnectionSettings(
        id=CONNECTION_ID,
        host_name="",
        configured_shared_folder_path="",
        connection_name=None,
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return connection_dto(row)


def _apply_connection_body(
    session: Session,
    settings: Settings,
    actor_id: uuid.UUID,
    row: SynologyConnectionSettings,
    body: dict,
    *,
    creating: bool,
) -> SynologyConnectionSettings:
    try:
        host, share, folder, display = parse_unc(body.get("sharedFolderPath") or body.get("configuredSharedFolderPath"))
    except UncError as exc:
        raise SynologyValidationError(str(exc)) from exc
    allowed = [item.strip().lower() for item in (settings.smb_allowed_hosts or "").split(",") if item.strip()]
    if allowed and host.lower() not in allowed:
        raise SynologyValidationError("That NAS host is not on the approved SMB host list.")
    auth_mode = int(body.get("authMode") if body.get("authMode") is not None else AUTH_SERVICE)
    username = (body.get("serviceAccountUserName") or body.get("username") or "").strip() or None
    secret = body.get("serviceAccountSecret") or body.get("password")
    name = (body.get("name") or body.get("connectionName") or "").strip() or None
    if not name:
        raise SynologyValidationError("Connection name is required (for example Eduard NAS or Finance).")
    duplicate_name = session.execute(
        select(SynologyConnectionSettings).where(
            SynologyConnectionSettings.connection_name == name,
            SynologyConnectionSettings.id != row.id,
        )
    ).scalar_one_or_none()
    if duplicate_name is not None:
        raise SynologyValidationError("Another Synology connection already uses that name.")
    secret_changing = bool(secret)
    if auth_mode == AUTH_PASSWORD:
        if not username and not row.service_account_user_name:
            raise SynologyValidationError("Username is required for password authentication.")
        if not secret_changing and not row.service_account_secret_protected:
            raise SynologyValidationError("Password is required for password authentication.")
    if share:
        display = build_unc(host, share, folder)
    path_owner = session.execute(
        select(SynologyConnectionSettings).where(
            SynologyConnectionSettings.configured_shared_folder_path == display,
            SynologyConnectionSettings.id != row.id,
        )
    ).scalar_one_or_none()
    if path_owner is not None:
        raise SynologyValidationError("Another Synology connection already uses that shared folder path.")
    changed = creating or (
        row.host_name.lower() != host.lower()
        or row.configured_shared_folder_path.lower() != display.lower()
        or row.auth_mode != auth_mode
        or (username or "") != (row.service_account_user_name or "")
        or secret_changing
        or (auth_mode == AUTH_SERVICE and row.service_account_secret_protected)
        or (name or "") != (row.connection_name or "")
    )
    row.host_name = host
    row.connection_name = name
    row.configured_shared_folder_path = display
    row.auth_mode = auth_mode
    row.service_account_user_name = username if auth_mode == AUTH_PASSWORD else None
    row.updated_by_user_id = actor_id
    row.updated_at = _now()
    if secret_changing and auth_mode == AUTH_PASSWORD:
        row.service_account_secret_protected = protect_secret(settings, str(secret))
    if auth_mode == AUTH_SERVICE:
        row.service_account_secret_protected = None
    if share:
        approved = _upsert_share(session, share, folder, actor_id)
        row.primary_approved_share_id = approved.id
        row.configured_shared_folder_path = build_unc(host, approved.share_name, approved.folder_relative_path)
    if changed:
        row.connection_status = STATUS_UNKNOWN
        row.last_safe_error = None
        row.port445_reachable = None
        row.authenticated_share_access = None
    session.flush()
    return row


def share_dto(row: SynologyApprovedShare, host: str) -> dict:
    return {
        "id": str(row.id),
        "shareName": row.share_name,
        "folderRelativePath": row.folder_relative_path,
        "uncRoot": build_unc(host, row.share_name, row.folder_relative_path) if host else "",
        "isActive": row.is_active,
        "aclCaptureStatus": row.acl_capture_status,
        "lastSafeError": row.last_safe_error,
        "lastSuccessfulSyncAt": row.last_successful_sync_at.isoformat() if row.last_successful_sync_at else None,
    }


def job_dto(job: SynologySyncJob) -> dict:
    percent = None
    if job.discovery_total and job.discovery_total > 0:
        percent = round(min(100.0, 100.0 * job.files_processed / job.discovery_total), 1)
    elif job.status == JOB_COMPLETED:
        percent = 100.0
    return {
        "id": str(job.id),
        "trigger": job.trigger,
        "status": job.status,
        "approvedShareId": str(job.approved_share_id) if job.approved_share_id else None,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
        "enumerationComplete": job.enumeration_complete,
        "currentOperation": job.current_operation,
        "scopeUncRoot": job.scope_unc_root,
        "filesDiscovered": job.files_discovered,
        "filesProcessed": job.files_processed,
        "filesRemaining": None if job.discovery_total is None else max(0, job.discovery_total - job.files_processed),
        "filesSkipped": job.files_skipped,
        "filesFailed": job.files_failed,
        "filesNew": job.files_new,
        "filesModified": job.files_modified,
        "filesUnchanged": job.files_unchanged,
        "filesRemoved": job.files_removed,
        "excelCount": job.excel_count,
        "pdfCount": job.pdf_count,
        "wordCount": job.word_count,
        "powerPointCount": job.powerpoint_count,
        "csvCount": job.csv_count,
        "otherCount": job.other_count,
        "totalBytes": job.total_bytes,
        "discoveryTotal": job.discovery_total,
        "percentComplete": percent,
        "safeError": job.safe_error,
        "cancelRequested": job.cancel_requested,
    }


def _credentials(settings: Settings, row: SynologyConnectionSettings) -> SmbCredentials | None:
    if row.auth_mode != AUTH_PASSWORD:
        return None
    secret = unprotect_secret(settings, row.service_account_secret_protected)
    if not secret:
        raise SynologyValidationError(
            "Re-enter the NAS password so QKA-Next can store it with its own encryption. The original protected secret is not readable here."
        )
    return SmbCredentials(row.service_account_user_name, secret)


def create_connection(session: Session, settings: Settings, actor_id: uuid.UUID, body: dict) -> dict:
    row = SynologyConnectionSettings(
        id=uuid.uuid4(),
        host_name="",
        configured_shared_folder_path="",
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return connection_dto(_apply_connection_body(session, settings, actor_id, row, body, creating=True))


def save_connection(
    session: Session,
    settings: Settings,
    actor_id: uuid.UUID,
    body: dict,
    connection_id: uuid.UUID | None = None,
) -> dict:
    if connection_id is None:
        # Backward-compatible: update first existing connection, or create one.
        existing = session.execute(select(SynologyConnectionSettings).order_by(SynologyConnectionSettings.updated_at.desc())).scalars().first()
        if existing is None:
            return create_connection(session, settings, actor_id, body)
        connection_id = existing.id
    row = _require_connection(session, connection_id)
    return connection_dto(_apply_connection_body(session, settings, actor_id, row, body, creating=False))


def delete_connection(session: Session, connection_id: uuid.UUID) -> None:
    row = _require_connection(session, connection_id)
    # Never delete Synology source files or inventory; only remove this connection record.
    session.delete(row)
    session.flush()


def _upsert_share(session: Session, share_name: str, folder: str, actor_id: uuid.UUID) -> SynologyApprovedShare:
    key = share_name.upper()
    folder_key = folder.upper()
    row = session.execute(
        select(SynologyApprovedShare).where(
            SynologyApprovedShare.share_name_normalized == key,
            SynologyApprovedShare.folder_relative_path_normalized == folder_key,
        )
    ).scalar_one_or_none()
    now = _now()
    if row is None:
        row = SynologyApprovedShare(
            id=uuid.uuid4(),
            share_name=share_name,
            share_name_normalized=key,
            folder_relative_path=folder,
            folder_relative_path_normalized=folder_key,
            is_active=True,
            created_at=now,
            updated_at=now,
            updated_by_user_id=actor_id,
        )
        session.add(row)
    else:
        row.is_active = True
        row.updated_at = now
        row.updated_by_user_id = actor_id
    session.flush()
    return row


def test_connection(
    session: Session,
    settings: Settings,
    gateway: SmbGateway,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID | None = None,
) -> dict:
    row = _require_connection(session, connection_id) if connection_id else _require_connection(session, uuid.UUID(get_connection(session)["id"]))
    if not row.host_name:
        raise SynologyValidationError("Save a shared folder path before testing the connection.")
    diagnostics: list[str] = []
    port_ok = gateway.test_port(row.host_name)
    diagnostics.append("TCP 445 is reachable." if port_ok else "TCP 445 is not reachable from the API host.")
    share = None
    if row.primary_approved_share_id:
        share = session.get(SynologyApprovedShare, row.primary_approved_share_id)
    auth_ok = False
    safe_error = None
    if not port_ok:
        safe_error = "SMB port 445 is not reachable."
    elif share is None or not share.is_active:
        safe_error = "Port 445 is reachable. Configure a shared folder path to verify authenticated access."
        diagnostics.append("No approved shared folder configured; authenticated access was not exercised.")
    else:
        unc = build_unc(row.host_name, share.share_name, share.folder_relative_path)
        diagnostics.append(f"Testing UNC: {unc}")
        try:
            creds = _credentials(settings, row)
            auth_ok, acl, extra = gateway.test_share(unc, creds)
            diagnostics.extend(extra)
            share.acl_capture_status = acl.status
            share.last_access_test_at = _now()
            share.last_safe_error = None if auth_ok else (acl.safe_error or "Authenticated share access failed.")
            if not auth_ok:
                safe_error = share.last_safe_error
        except SynologyValidationError as exc:
            safe_error = str(exc)
            diagnostics.append(str(exc))
        except SmbError as exc:
            safe_error = str(exc)
            diagnostics.extend(exc.diagnostics or [str(exc)])
    row.port445_reachable = port_ok
    row.authenticated_share_access = auth_ok
    row.connection_status = STATUS_CONNECTED if port_ok and auth_ok else STATUS_FAILED
    row.last_safe_error = safe_error
    row.last_successful_test_at = _now() if auth_ok else row.last_successful_test_at
    row.updated_by_user_id = actor_id
    row.updated_at = _now()
    session.flush()
    return {
        "succeeded": bool(port_ok and auth_ok),
        "status": row.connection_status,
        "port445Reachable": port_ok,
        "authenticatedShareAccess": auth_ok,
        "safeError": safe_error,
        "diagnostics": diagnostics,
    }


def list_shares(session: Session, connection_id: uuid.UUID | None = None) -> list[dict]:
    connection = _require_connection(session, connection_id) if connection_id else None
    rows = session.execute(
        select(SynologyApprovedShare).where(SynologyApprovedShare.is_active.is_(True)).order_by(SynologyApprovedShare.share_name)
    ).scalars().all()
    host = connection.host_name if connection else ""
    if connection and connection.primary_approved_share_id:
        rows = [row for row in rows if row.id == connection.primary_approved_share_id]
    return [share_dto(row, host) for row in rows]


def stats_dto(session: Session, share_id: uuid.UUID | None = None) -> dict:
    settings = _get_or_create_settings(session)
    previous = None
    if settings.last_successful_job_id or settings.last_total_files:
        previous = {
            "excel": settings.last_excel_count,
            "pdf": settings.last_pdf_count,
            "word": settings.last_word_count,
            "powerPoint": settings.last_powerpoint_count,
            "csv": settings.last_csv_count,
            "other": settings.last_other_count,
            "totalFiles": settings.last_total_files,
            "totalBytes": settings.last_total_bytes,
        }
    current_job = None
    if settings.current_job_id:
        current_job = session.get(SynologySyncJob, settings.current_job_id)
    in_progress = current_job is not None and current_job.status in {JOB_QUEUED, JOB_SCANNING}
    if share_id is not None and current_job is not None and current_job.approved_share_id != share_id:
        in_progress = False
    if in_progress and previous and share_id is None:
        return {**previous, "new": 0, "modified": 0, "unchanged": 0, "removed": 0, "failed": 0, "isInProgress": True, "previousCompleted": previous}
    present = SynologyFileInventory.is_present.is_(True)
    filters = [present]
    if share_id is not None:
        filters.append(SynologyFileInventory.approved_share_id == share_id)
    total_files = session.scalar(select(func.count()).select_from(SynologyFileInventory).where(*filters)) or 0
    total_bytes = session.scalar(select(func.coalesce(func.sum(SynologyFileInventory.size_bytes), 0)).where(*filters)) or 0
    type_rows = session.execute(
        select(SynologyFileInventory.file_type_category, func.count())
        .where(*filters)
        .group_by(SynologyFileInventory.file_type_category)
    ).all()
    counts = {TYPE_EXCEL: 0, TYPE_PDF: 0, TYPE_WORD: 0, TYPE_PPT: 0, TYPE_CSV: 0, TYPE_OTHER: 0}
    for category, n in type_rows:
        counts[int(category)] = int(n)
    status_rows = session.execute(
        select(SynologyFileInventory.sync_status, func.count())
        .where(*filters)
        .group_by(SynologyFileInventory.sync_status)
    ).all()
    statuses = {SYNC_NEW: 0, SYNC_MODIFIED: 0, SYNC_UNCHANGED: 0, SYNC_REMOVED: 0, 4: 0}
    for status, n in status_rows:
        statuses[int(status)] = int(n)
    return {
        "excel": counts[TYPE_EXCEL],
        "pdf": counts[TYPE_PDF],
        "word": counts[TYPE_WORD],
        "powerPoint": counts[TYPE_PPT],
        "csv": counts[TYPE_CSV],
        "other": counts[TYPE_OTHER],
        "totalFiles": int(total_files),
        "totalBytes": int(total_bytes),
        "new": statuses[SYNC_NEW],
        "modified": statuses[SYNC_MODIFIED],
        "unchanged": statuses[SYNC_UNCHANGED],
        "removed": statuses[SYNC_REMOVED],
        "failed": statuses[4],
        "isInProgress": in_progress,
        "previousCompleted": previous if share_id is None else None,
    }


def get_monitor(session: Session, connection_id: uuid.UUID | None = None) -> dict:
    if connection_id is not None:
        connection = _require_connection(session, connection_id)
    else:
        connection = _require_connection(session, uuid.UUID(get_connection(session)["id"]))
    settings = _get_or_create_settings(session)
    primary = None
    if connection.primary_approved_share_id:
        share = session.get(SynologyApprovedShare, connection.primary_approved_share_id)
        if share:
            primary = share_dto(share, connection.host_name)
    current = None
    if settings.current_job_id:
        job = session.get(SynologySyncJob, settings.current_job_id)
        if job:
            if connection.primary_approved_share_id and job.approved_share_id == connection.primary_approved_share_id:
                current = job_dto(job)
            if job.status in {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED}:
                settings.current_job_id = None
                session.flush()
                if current is not None and job.status != JOB_QUEUED:
                    current = job_dto(job)
    share_last_sync = primary["lastSuccessfulSyncAt"] if primary else None
    has_completed = bool(share_last_sync or (settings.last_successful_job_id and connection.primary_approved_share_id))
    return {
        "connection": connection_dto(connection),
        "connections": list_connections(session),
        "primaryShare": primary,
        "currentJob": current,
        "stats": stats_dto(session, connection.primary_approved_share_id),
        "lastSuccessfulSyncAt": share_last_sync or (settings.last_successful_sync_at.isoformat() if settings.last_successful_sync_at else None),
        "hasCompletedInventory": has_completed,
    }


def start_sync(session: Session, actor_id: uuid.UUID, connection_id: uuid.UUID | None = None) -> dict:
    connection = _require_connection(session, connection_id) if connection_id else _require_connection(session, uuid.UUID(get_connection(session)["id"]))
    if connection.connection_status != STATUS_CONNECTED:
        raise SynologyValidationError("Connect and test the Synology shared folder before starting synchronization.")
    if connection.primary_approved_share_id is None:
        raise SynologyValidationError("Configure an approved shared folder path before synchronization.")
    share = session.get(SynologyApprovedShare, connection.primary_approved_share_id)
    if share is None or not share.is_active:
        raise SynologyValidationError("Approved shared folder was not found.")
    settings = _get_or_create_settings(session)
    if settings.current_job_id:
        running = session.get(SynologySyncJob, settings.current_job_id)
        if running and running.status in {JOB_QUEUED, JOB_SCANNING}:
            raise SynologyConflictError("A Synology synchronization job is already running.")
    present = session.execute(
        select(func.count()).select_from(SynologyFileInventory).where(
            SynologyFileInventory.approved_share_id == share.id,
            SynologyFileInventory.is_present.is_(True),
        )
    ).scalar_one()
    trigger = TRIGGER_RESYNC if present else TRIGGER_MANUAL
    job = SynologySyncJob(
        id=uuid.uuid4(),
        trigger=trigger,
        status=JOB_QUEUED,
        approved_share_id=share.id,
        actor_user_id=actor_id,
        created_at=_now(),
        scope_unc_root=build_unc(connection.host_name, share.share_name, share.folder_relative_path),
        current_operation="Queued",
    )
    session.add(job)
    settings.current_job_id = job.id
    settings.last_attempted_sync_at = _now()
    settings.updated_at = _now()
    settings.updated_by_user_id = actor_id
    session.flush()
    return job_dto(job)


def cancel_job(session: Session, job_id: uuid.UUID) -> None:
    job = session.get(SynologySyncJob, job_id)
    if job is None:
        raise SynologyValidationError("Sync job was not found.")
    if job.status in {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED}:
        return
    job.cancel_requested = True
    session.flush()


def list_history(session: Session, take: int = 50) -> list[dict]:
    limit = max(1, min(take, 100))
    jobs = session.execute(select(SynologySyncJob).order_by(SynologySyncJob.created_at.desc()).limit(limit)).scalars().all()
    return [job_dto(job) for job in jobs]


def get_job(session: Session, job_id: uuid.UUID) -> dict | None:
    job = session.get(SynologySyncJob, job_id)
    return None if job is None else job_dto(job)


def list_inventory(
    session: Session,
    search: str | None,
    file_type: int | None,
    page: int,
    page_size: int,
    connection_id: uuid.UUID | None = None,
) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size or 50, 200))
    query = select(SynologyFileInventory).where(*present_file_filters())
    if connection_id is not None:
        connection = _require_connection(session, connection_id)
        if connection.primary_approved_share_id is None:
            return {"items": [], "totalCount": 0, "page": page, "pageSize": page_size}
        query = query.where(SynologyFileInventory.approved_share_id == connection.primary_approved_share_id)
    if file_type is not None:
        query = query.where(SynologyFileInventory.file_type_category == file_type)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(SynologyFileInventory.file_name.ilike(term), SynologyFileInventory.relative_path.ilike(term)))
    total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = session.execute(
        query.order_by(SynologyFileInventory.relative_path_normalized).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    content_by_id = {}
    if rows:
        contents = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id.in_([row.id for row in rows]))
        ).scalars().all()
        content_by_id = {item.inventory_file_id: item for item in contents}
    items = []
    for row in rows:
        folder = row.relative_path.rsplit("\\", 1)[0] if "\\" in row.relative_path else ""
        items.append(
            {
                "id": str(row.id),
                "fileName": row.file_name,
                "extension": row.extension,
                "fileTypeCategory": row.file_type_category,
                "relativeFolderPath": folder,
                "relativePath": row.relative_path,
                "sizeBytes": int(row.size_bytes or 0),
                "lastWriteUtc": row.last_write_utc.isoformat() if row.last_write_utc else None,
                "syncStatus": row.sync_status,
                "aclCaptureStatus": row.acl_capture_status,
                "aclDefaultDeny": row.acl_capture_status != ACL_CAPTURED,
                "indexingState": _indexing_state(content_by_id.get(row.id)),
            }
        )
    return {"items": items, "totalCount": total, "page": page, "pageSize": page_size}


def _indexing_state(content: SynologyDocumentContent | None) -> str:
    if content is None:
        return "pending"
    return {0: "pending", 1: "indexed", 2: "failed", 3: "unsupported", 4: "skipped"}.get(content.status, "pending")


def process_next_job(session: Session, settings: Settings, gateway: SmbGateway) -> None:
    _recover_stale_jobs(session)
    job = session.execute(
        select(SynologySyncJob).where(SynologySyncJob.status == JOB_QUEUED).order_by(SynologySyncJob.created_at)
    ).scalars().first()
    if job is None:
        return
    if not _acquire_lease(session, job.id):
        return
    try:
        _run_job(session, settings, gateway, job.id)
    finally:
        _release_lease(session, job.id)


def _recover_stale_jobs(session: Session) -> None:
    lease = session.get(SynologySyncJobLease, LEASE_ID)
    now = _now()
    if lease is None or lease.owner_job_id is None:
        return
    if lease.expires_at is not None and lease.expires_at > now:
        return
    job = session.get(SynologySyncJob, lease.owner_job_id)
    if job is not None and job.status == JOB_SCANNING:
        job.status = JOB_FAILED
        job.completed_at = now
        job.current_operation = "Failed"
        job.enumeration_complete = False
        job.safe_error = "Worker lost the synchronization lease. Inventory was not marked removed because enumeration did not complete."
        sync_settings = _get_or_create_settings(session)
        if sync_settings.current_job_id == job.id:
            sync_settings.current_job_id = None
    lease.owner_job_id = None
    lease.owner_instance_id = None
    lease.expires_at = None
    session.flush()


def _acquire_lease(session: Session, job_id: uuid.UUID) -> bool:
    lease = session.get(SynologySyncJobLease, LEASE_ID)
    if lease is None:
        lease = SynologySyncJobLease(id=LEASE_ID)
        session.add(lease)
        session.flush()
    now = _now()
    if lease.owner_job_id and lease.expires_at and lease.expires_at > now and lease.owner_job_id != job_id:
        return False
    lease.owner_job_id = job_id
    lease.owner_instance_id = INSTANCE_ID
    lease.acquired_at = now
    lease.expires_at = now + timedelta(minutes=LEASE_MINUTES)
    session.flush()
    return True


def _renew_lease(session: Session, job_id: uuid.UUID) -> None:
    lease = session.get(SynologySyncJobLease, LEASE_ID)
    if lease is None or lease.owner_job_id != job_id:
        raise SynologyConflictError("Lost Synology synchronization lease.")
    lease.expires_at = _now() + timedelta(minutes=LEASE_MINUTES)
    session.flush()


def _release_lease(session: Session, job_id: uuid.UUID) -> None:
    lease = session.get(SynologySyncJobLease, LEASE_ID)
    if lease is not None and lease.owner_job_id == job_id:
        lease.owner_job_id = None
        lease.owner_instance_id = None
        lease.expires_at = None
        session.flush()


def _run_job(session: Session, settings: Settings, gateway: SmbGateway, job_id: uuid.UUID) -> None:
    job = session.get(SynologySyncJob, job_id)
    if job is None:
        return
    job.status = JOB_SCANNING
    job.started_at = _now()
    job.current_operation = "Scanning approved folder"
    session.flush()
    try:
        if job.approved_share_id is None:
            raise SynologyValidationError("Sync job is missing an approved share.")
        share = session.get(SynologyApprovedShare, job.approved_share_id)
        if share is None:
            raise SynologyValidationError("Approved shared folder was not found for this sync job.")
        connection = _connection_for_share(session, share.id)
        if connection is None:
            raise SynologyValidationError("No Synology connection is linked to this approved share.")
        unc = build_unc(connection.host_name, share.share_name, share.folder_relative_path)
        job.scope_unc_root = unc
        seen: set[str] = set()
        since_flush = 0
        creds = _credentials(settings, connection)
        for item in gateway.enumerate_files(unc, creds):
            cancelled = session.execute(
                select(SynologySyncJob.cancel_requested).where(SynologySyncJob.id == job.id)
            ).scalar_one()
            if cancelled:
                raise InterruptedError("cancelled")
            key = item.relative_path.replace("/", "\\").strip("\\").upper()
            seen.add(key)
            is_dir = bool(getattr(item, "is_directory", False))
            job.current_operation = f"Processing {item.file_name}"
            try:
                row, changed = _upsert_inventory(session, share.id, job, item)
                seen.update(_ensure_ancestors(session, share.id, job, item.relative_path))
                if not is_dir:
                    job.files_discovered += 1
                    if changed:
                        _extract_if_needed(session, settings, gateway, connection, share, row, unc, creds)
                    job.files_processed += 1
                    _bump_type(job, classify(item.extension), item.size_bytes)
            except Exception:
                log.exception("Failed to inventory Synology file name=%s", item.file_name)
                job.files_failed += 1
                job.files_skipped += 1
            since_flush += 1
            if since_flush >= 25:
                since_flush = 0
                _renew_lease(session, job.id)
                session.flush()
        job.enumeration_complete = True
        job.discovery_total = job.files_discovered
        existing = session.execute(
            select(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share.id,
                SynologyFileInventory.is_present.is_(True),
            )
        ).scalars().all()
        for row in existing:
            if row.relative_path_normalized not in seen:
                row.is_present = False
                row.sync_status = SYNC_REMOVED
                row.updated_at = _now()
                row.last_seen_job_id = job.id
                job.files_removed += 1
                _clear_search(session, row.id)
        job.status = JOB_COMPLETED
        job.completed_at = _now()
        job.current_operation = "Completed"
        job.safe_error = None
        share.last_successful_sync_at = job.completed_at
        sync_settings = _get_or_create_settings(session)
        sync_settings.last_successful_sync_at = job.completed_at
        sync_settings.last_successful_job_id = job.id
        sync_settings.current_job_id = None
        sync_settings.last_excel_count = job.excel_count
        sync_settings.last_pdf_count = job.pdf_count
        sync_settings.last_word_count = job.word_count
        sync_settings.last_powerpoint_count = job.powerpoint_count
        sync_settings.last_csv_count = job.csv_count
        sync_settings.last_other_count = job.other_count
        sync_settings.last_total_files = job.files_discovered
        sync_settings.last_total_bytes = job.total_bytes
        sync_settings.updated_at = _now()
        session.flush()
    except InterruptedError:
        job.status = JOB_CANCELLED
        job.completed_at = _now()
        job.current_operation = "Cancelled"
        job.safe_error = "Synchronization was cancelled."
        job.enumeration_complete = False
        _get_or_create_settings(session).current_job_id = None
        session.flush()
    except Exception as exc:
        log.exception("Synology sync job failed job_id=%s", job.id)
        job.status = JOB_FAILED
        job.completed_at = _now()
        job.current_operation = "Failed"
        job.safe_error = "Synchronization failed. Inventory was not marked removed because enumeration did not complete."
        job.enumeration_complete = False
        _get_or_create_settings(session).current_job_id = None
        session.flush()
        _ = exc


def _ensure_ancestors(session: Session, share_id: uuid.UUID, job: SynologySyncJob, relative: str) -> set[str]:
    """Persist parent folders for a file/folder path and return their normalized keys."""
    parts = [p for p in (relative or "").replace("/", "\\").strip("\\").split("\\") if p]
    seen: set[str] = set()
    accum: list[str] = []
    for part in parts[:-1]:
        accum.append(part)
        rel_path = "\\".join(accum)
        key = rel_path.upper()
        seen.add(key)
        existing = session.execute(
            select(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share_id,
                SynologyFileInventory.relative_path_normalized == key,
            )
        ).scalar_one_or_none()
        now = _now()
        if existing is None:
            session.add(
                SynologyFileInventory(
                    id=uuid.uuid4(),
                    approved_share_id=share_id,
                    relative_path=rel_path,
                    relative_path_normalized=key,
                    file_name=part,
                    extension="",
                    file_type_category=0,
                    size_bytes=0,
                    last_write_utc=None,
                    sync_status=SYNC_NEW,
                    acl_capture_status=0,
                    last_seen_job_id=job.id,
                    is_present=True,
                    is_directory=True,
                    parent_path_normalized=parent_normalized(key),
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.is_present = True
            existing.is_directory = True
            existing.file_name = existing.file_name or part
            existing.parent_path_normalized = parent_normalized(key)
            existing.last_seen_job_id = job.id
            existing.updated_at = now
            if existing.sync_status == SYNC_REMOVED:
                existing.sync_status = SYNC_UNCHANGED
    session.flush()
    return seen


def _upsert_inventory(session: Session, share_id: uuid.UUID, job: SynologySyncJob, item: EnumeratedFile) -> tuple[SynologyFileInventory, bool]:
    relative = item.relative_path.replace("/", "\\").strip("\\")
    key = relative.upper()
    is_dir = bool(getattr(item, "is_directory", False))
    category = 0 if is_dir else classify(item.extension)
    acl_status = item.acl.status if item.acl.captured else item.acl.status
    row = session.execute(
        select(SynologyFileInventory).where(
            SynologyFileInventory.approved_share_id == share_id,
            SynologyFileInventory.relative_path_normalized == key,
        )
    ).scalar_one_or_none()
    changed = True
    if row is None:
        row = SynologyFileInventory(
            id=uuid.uuid4(),
            approved_share_id=share_id,
            relative_path=relative,
            relative_path_normalized=key,
            created_at=_now(),
            sync_status=SYNC_NEW,
        )
        session.add(row)
        if not is_dir:
            job.files_new += 1
    else:
        modified = (
            (not is_dir)
            and (
                row.size_bytes != item.size_bytes
                or row.last_write_utc != item.last_write_utc
                or row.file_name != item.file_name
            )
        )
        row.sync_status = SYNC_MODIFIED if modified else SYNC_UNCHANGED
        if not is_dir:
            if modified:
                job.files_modified += 1
            else:
                job.files_unchanged += 1
                changed = False
        else:
            changed = False
    row.file_name = item.file_name
    row.extension = "" if is_dir else (item.extension or "").lower()
    row.file_type_category = category
    row.size_bytes = 0 if is_dir else item.size_bytes
    row.last_write_utc = item.last_write_utc
    row.acl_capture_status = acl_status
    row.acl_snapshot_json = json.dumps(item.acl.entries) if item.acl.captured else None
    row.last_seen_job_id = job.id
    row.is_present = True
    row.is_directory = is_dir
    row.parent_path_normalized = parent_normalized(key)
    row.updated_at = _now()
    session.flush()
    return row, changed


def _extract_if_needed(
    session: Session,
    settings: Settings,
    gateway: SmbGateway,
    connection: SynologyConnectionSettings,
    share: SynologyApprovedShare,
    row: SynologyFileInventory,
    unc: str,
    creds: SmbCredentials | None,
) -> None:
    if bool(getattr(row, "is_directory", False)):
        return
    prefix = (settings.pilot_folder_prefix or "").replace("/", "\\").strip("\\").upper()
    path_key = row.relative_path_normalized
    pilot = (not prefix) or path_key == prefix or path_key.startswith(prefix + "\\")
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == row.id)
    ).scalar_one_or_none()
    now = _now()
    if content is None:
        content = SynologyDocumentContent(
            id=uuid.uuid4(),
            inventory_file_id=row.id,
            approved_share_id=share.id,
            relative_path_normalized=row.relative_path_normalized,
            file_name=row.file_name,
            extension=row.extension,
            created_at=now,
            updated_at=now,
        )
        session.add(content)
    content.file_name = row.file_name
    content.extension = row.extension
    content.relative_path_normalized = row.relative_path_normalized
    content.pilot_scoped = pilot
    content.non_confidential = pilot
    content.updated_at = now
    if not pilot:
        content.status = 4
        content.extracted_text = None
        content.safe_error = "Outside the approved pilot folder."
        session.flush()
        return
    if not is_supported(row.extension) and (row.extension or "").lower() not in {"ppt", "pptx"}:
        content.status = 3
        content.extracted_text = None
        content.safe_error = "File type is not supported for extraction."
        session.flush()
        return
    try:
        payload = gateway.open_read(unc, row.relative_path, creds)
    except Exception:
        content.status = 2
        content.safe_error = "Read-only extraction could not open the file."
        session.flush()
        return
    status, extracted, error, digest = extract_bytes(row.extension, payload, settings.max_preview_chars * 20)
    content.status = status
    content.extracted_text = extracted if status == EXTRACT_SUCCEEDED else None
    content.safe_error = error
    content.content_sha256 = digest
    content.extracted_at = now if status == EXTRACT_SUCCEEDED else None
    session.flush()
    if status == EXTRACT_SUCCEEDED and content.extracted_text:
        session.execute(
            sql_text('UPDATE synology_document_content SET search_vector = to_tsvector(\'english\', coalesce("ExtractedText", \'\')) WHERE "Id" = CAST(:id AS uuid)'),
            {"id": str(content.id)},
        )


def _clear_search(session: Session, inventory_id: uuid.UUID) -> None:
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory_id)
    ).scalar_one_or_none()
    if content is None:
        return
    content.extracted_text = None
    content.status = 0
    content.safe_error = "Source file is no longer present in inventory."
    content.updated_at = _now()
    session.flush()
    session.execute(
        sql_text('UPDATE synology_document_content SET search_vector = NULL WHERE "Id" = CAST(:id AS uuid)'),
        {"id": str(content.id)},
    )


def _bump_type(job: SynologySyncJob, category: int, size: int) -> None:
    job.total_bytes += int(size or 0)
    if category == TYPE_EXCEL:
        job.excel_count += 1
    elif category == TYPE_PDF:
        job.pdf_count += 1
    elif category == TYPE_WORD:
        job.word_count += 1
    elif category == TYPE_PPT:
        job.powerpoint_count += 1
    elif category == TYPE_CSV:
        job.csv_count += 1
    else:
        job.other_count += 1
