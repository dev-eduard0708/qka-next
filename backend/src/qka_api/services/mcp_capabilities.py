"""MCP capability helpers: download/preview handoff, folder browse, index status, start_indexing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from qka_api.services import documents as docs
from qka_api.services import extraction as extraction_svc
from qka_api.services import synology as synology_svc
from qka_api.services.document_preview import preview_document
from qka_api.services.inventory_hierarchy import list_folder_children
from qka_shared.allowlist import deny_message, evaluate_user, load_active_user
from qka_shared.config import Settings
from qka_shared.models import (
    McpDocumentHandoff,
    McpIndexingConfirmation,
    QkaUser,
    SynologyApprovedShare,
    SynologyDocumentContent,
    SynologyExtractionBatch,
    SynologyFileInventory,
)
from qka_shared.source_access import Capability, normalize_prefix
from types import SimpleNamespace

HANDOFF_TTL = timedelta(minutes=15)
CONFIRM_TTL = timedelta(minutes=10)
MAX_PAGE = 50
INDEX_OPS = frozenset({"sync", "resync", "extract_missing", "retry_failed", "reindex_selected"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_doc_id(document_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(document_id) if "-" in document_id else uuid.UUID(hex=document_id)
    except ValueError:
        return None


def _is_loopback_origin(origin: str) -> bool:
    lower = (origin or "").strip().lower()
    return (
        not lower
        or "localhost" in lower
        or "127.0.0.1" in lower
        or "[::1]" in lower
        or lower.startswith("http://0.0.0.0")
    )


def employee_app_origin(settings: Settings) -> str | None:
    """HTTPS/HTTP origin reachable by the employee's browser (not server localhost)."""
    for candidate in (settings.frontend_origin, getattr(settings, "mcp_public_base_url", "")):
        origin = (candidate or "").strip().rstrip("/")
        if origin and not _is_loopback_origin(origin):
            return origin
    return None


def _encode_cursor(offset: int) -> str:
    return f"o:{max(0, int(offset))}"


def _decode_cursor(cursor: str | None) -> int:
    raw = (cursor or "").strip()
    if not raw:
        return 0
    if raw.startswith("o:"):
        try:
            return max(0, int(raw[2:]))
        except ValueError:
            return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _create_handoff(
    session: Session,
    *,
    user_id: uuid.UUID,
    inventory_id: uuid.UUID,
    purpose: str,
) -> McpDocumentHandoff:
    row = McpDocumentHandoff(
        id=uuid.uuid4(),
        qka_user_id=user_id,
        inventory_file_id=inventory_id,
        purpose=purpose,
        expires_at=_now() + HANDOFF_TTL,
        consumed_at=None,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def _handoff_url(settings: Settings, handoff_id: uuid.UUID, purpose: str) -> str | None:
    origin = employee_app_origin(settings)
    if origin is None:
        return None
    return f"{origin}/search?handoff={handoff_id.hex}&purpose={quote(purpose)}"


def get_document_download_link(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
) -> dict[str, Any]:
    auth = docs.authorize_download(session, settings, caller_id, document_id)
    if auth.get("denied"):
        return {
            "documentId": document_id,
            "denied": True,
            "downloadAuthorized": False,
            "error": auth.get("error") or "Download denied.",
            "requiresDownloadGrant": bool(auth.get("requiresDownloadGrant")),
            "browserDownloadAvailable": False,
            "downloadLink": None,
        }
    inventory_id = _parse_doc_id(auth["id"])
    assert inventory_id is not None
    inventory = session.get(SynologyFileInventory, inventory_id)
    handoff = _create_handoff(session, user_id=caller_id, inventory_id=inventory_id, purpose="download")
    link = _handoff_url(settings, handoff.id, "download")
    return {
        "documentId": auth["id"],
        "denied": False,
        "documentName": auth.get("fileName"),
        "fileType": docs._file_type(auth.get("extension") or ""),
        "extension": auth.get("extension"),
        "fileSizeBytes": int(auth.get("sizeBytes") or 0),
        "downloadAuthorized": True,
        "browserDownloadAvailable": link is not None,
        "downloadLink": link,
        "error": None
        if link
        else "Browser download is unavailable: no employee-facing QKA URL is configured "
        "(set frontend_origin or mcp_public_base_url to a non-localhost HTTPS address).",
        "note": "Open the link while signed into QKA as the same employee. Permissions are rechecked on open.",
        "openWithLabel": auth.get("openWithLabel"),
        "recommendedApp": auth.get("recommendedApp"),
        "relativePath": inventory.relative_path_normalized if inventory else auth.get("relativePath"),
    }


def get_document_preview(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
) -> dict[str, Any]:
    preview = preview_document(session, settings, caller_id, document_id)
    if preview.get("denied"):
        return {
            "documentId": document_id,
            "denied": True,
            "previewAvailable": False,
            "error": preview.get("error") or "Preview denied.",
            "previewLink": None,
            "browserPreviewAvailable": False,
        }
    inventory_id = _parse_doc_id(preview.get("id") or document_id)
    if inventory_id is None:
        return {
            "documentId": document_id,
            "denied": True,
            "previewAvailable": False,
            "error": "Document not found.",
            "previewLink": None,
            "browserPreviewAvailable": False,
        }
    handoff = _create_handoff(session, user_id=caller_id, inventory_id=inventory_id, purpose="preview")
    link = _handoff_url(settings, handoff.id, "preview")
    kind = preview.get("previewKind") or "unavailable"
    available = bool(preview.get("previewAvailable"))
    note = preview.get("previewNote")
    # Never claim original-format Office rendering when only indexed/fallback exists.
    if kind in {"indexed_text", "indexed", "text", "table", "unavailable"}:
        explanation = note or (
            "Original-format Office rendering is not available; showing the supported QKA preview kind."
            if available
            else "No supported preview is available for this document."
        )
    else:
        explanation = note
    return {
        "documentId": preview.get("id"),
        "denied": False,
        "documentName": preview.get("title"),
        "documentType": preview.get("fileType"),
        "extension": preview.get("extension"),
        "previewAvailable": available,
        "previewKind": kind,
        "pageCount": preview.get("pageCount"),
        "fallbackStatus": preview.get("fallback") or preview.get("usedFallback"),
        "previewNote": explanation,
        "browserPreviewAvailable": link is not None,
        "previewLink": link,
        "error": None
        if link
        else "Browser preview is unavailable: no employee-facing QKA URL is configured.",
        "canDownload": bool(preview.get("canDownload")),
    }


def redeem_handoff(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    handoff_id: str,
) -> dict[str, Any]:
    hid = _parse_doc_id(handoff_id)
    if hid is None:
        return {"denied": True, "error": "Handoff not found."}
    row = session.get(McpDocumentHandoff, hid)
    if row is None:
        return {"denied": True, "error": "Handoff not found."}
    if row.qka_user_id != caller_id:
        return {"denied": True, "error": "Handoff belongs to a different QKA user."}
    if _as_utc(row.expires_at) < _now():
        return {"denied": True, "error": "Handoff expired."}
    if row.purpose == "download" and row.consumed_at is not None:
        return {"denied": True, "error": "Handoff already used."}

    doc_hex = row.inventory_file_id.hex
    if row.purpose == "download":
        auth = docs.authorize_download(session, settings, caller_id, doc_hex)
        if auth.get("denied"):
            return {
                "denied": True,
                "purpose": "download",
                "error": auth.get("error") or "Download denied.",
                "requiresDownloadGrant": bool(auth.get("requiresDownloadGrant")),
            }
        return {
            "denied": False,
            "purpose": "download",
            "documentId": doc_hex,
            "handoffId": str(row.id),
            "fileName": auth.get("fileName"),
            "extension": auth.get("extension"),
            "sizeBytes": auth.get("sizeBytes"),
        }

    # preview: View Documents rechecked inside preview_document
    preview = preview_document(session, settings, caller_id, doc_hex)
    if preview.get("denied"):
        return {"denied": True, "purpose": "preview", "error": preview.get("error") or "Preview denied."}
    return {
        "denied": False,
        "purpose": "preview",
        "documentId": doc_hex,
        "documentName": preview.get("title"),
        "previewKind": preview.get("previewKind"),
    }


def _authorized_shares(session: Session, user: QkaUser) -> list[uuid.UUID]:
    match = docs._authorized_share_matches(session, user)
    return list(match.keys())


def _resolve_source_id(
    session: Session,
    user: QkaUser,
    source_id: str | None,
) -> tuple[uuid.UUID | None, str | None]:
    allowed = _authorized_shares(session, user)
    if not allowed:
        return None, "No authorized Synology sources."
    if not source_id:
        return allowed[0], None
    sid = _parse_doc_id(source_id)
    if sid is not None and sid in allowed:
        return sid, None
    # Accept connection id → primary approved share when the share is authorized.
    if sid is not None:
        try:
            conn = synology_svc.get_connection(session, sid)
        except Exception:
            conn = None
        raw = (conn or {}).get("primaryApprovedShareId")
        if raw:
            share = uuid.UUID(str(raw))
            if share in allowed:
                return share, None
    return None, "Source not found or not authorized."


def _connection_id_for_share(session: Session, share_id: uuid.UUID | None) -> uuid.UUID | None:
    if share_id is None:
        return None
    conn = synology_svc._connection_for_share(session, share_id)
    return conn.id if conn is not None else None


def _folder_has_authorized_content(
    session: Session,
    share_id: uuid.UUID,
    folder_path: str,
    auth_clause,
) -> bool:
    path = normalize_prefix(folder_path)
    filters = [
        SynologyFileInventory.approved_share_id == share_id,
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.is_directory.is_(False),
        auth_clause,
    ]
    if path:
        filters.append(
            or_(
                SynologyFileInventory.relative_path_normalized == path,
                SynologyFileInventory.relative_path_normalized.like(path + "\\%", escape="/"),
            )
        )
    count = session.scalar(select(func.count()).select_from(SynologyFileInventory).where(*filters)) or 0
    return int(count) > 0


def list_folders(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    *,
    source_id: str | None = None,
    parent_folder_id: str | None = None,
    parent_path: str | None = None,
    page_size: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return {"denied": True, "error": deny_message(access.reason), "folders": []}
    user = load_active_user(session, caller_id)
    if user is None:
        return {"denied": True, "error": deny_message("no_verified_email"), "folders": []}

    share_id, err = _resolve_source_id(session, user, source_id)
    if share_id is None:
        return {"denied": True, "error": err or "No source.", "folders": []}

    match = docs._authorized_share_matches(session, user)
    auth_clause = docs._authorized_inventory_clause(match)
    parent = normalize_prefix(parent_path or parent_folder_id or "")
    size = max(1, min(int(page_size or 25), MAX_PAGE))
    offset = _decode_cursor(cursor)

    # Page through inventory folders, filter to authorized visibility.
    raw = list_folder_children(session, share_id, parent, page=1, page_size=500)
    folders: list[dict] = []
    for item in raw.get("items") or []:
        if item.get("isFile"):
            continue
        path = item.get("path") or ""
        if not _folder_has_authorized_content(session, share_id, path, auth_clause):
            continue
        child_folders = 0
        child_raw = list_folder_children(session, share_id, path, page=1, page_size=200)
        for child in child_raw.get("items") or []:
            if child.get("isFile"):
                continue
            if _folder_has_authorized_content(session, share_id, child.get("path") or "", auth_clause):
                child_folders += 1
        folders.append(
            {
                "folderId": path,
                "name": item.get("label"),
                "path": path,
                "parentPath": parent,
                "sourceId": str(share_id),
                "authorizedChildFolderCount": child_folders,
            }
        )

    page = folders[offset : offset + size]
    next_offset = offset + size
    return {
        "denied": False,
        "error": None,
        "sourceId": str(share_id),
        "parentPath": parent,
        "folders": page,
        "count": len(page),
        "nextCursor": _encode_cursor(next_offset) if next_offset < len(folders) else None,
    }


def list_documents(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    *,
    source_id: str | None = None,
    folder_id: str | None = None,
    folder_path: str | None = None,
    recursive: bool = False,
    file_type: str | None = None,
    filename_query: str | None = None,
    page_size: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return {"denied": True, "error": deny_message(access.reason), "documents": []}
    user = load_active_user(session, caller_id)
    if user is None:
        return {"denied": True, "error": deny_message("no_verified_email"), "documents": []}

    share_id, err = _resolve_source_id(session, user, source_id)
    if share_id is None:
        return {"denied": True, "error": err or "No source.", "documents": []}

    match = docs._authorized_share_matches(session, user)
    auth_clause = docs._authorized_inventory_clause(match)
    folder = normalize_prefix(folder_path or folder_id or "")
    size = max(1, min(int(page_size or 25), MAX_PAGE))
    offset = _decode_cursor(cursor)

    filters = [
        SynologyFileInventory.approved_share_id == share_id,
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.is_directory.is_(False),
        auth_clause,
        docs._file_type_filter_clause(file_type),
    ]
    if folder:
        if recursive:
            filters.append(
                or_(
                    SynologyFileInventory.relative_path_normalized == folder,
                    SynologyFileInventory.relative_path_normalized.like(folder + "\\%", escape="/"),
                )
            )
        else:
            filters.append(SynologyFileInventory.parent_path_normalized == folder)
    needle = (filename_query or "").strip()
    if needle:
        like = f"%{needle}%"
        filters.append(
            or_(
                SynologyFileInventory.file_name.ilike(like, escape="/"),
                SynologyFileInventory.relative_path.ilike(like, escape="/"),
            )
        )

    base = select(SynologyFileInventory).where(*filters)
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    # Cap scan window; never dump entire inventory in one MCP response.
    rows = session.execute(
        base.order_by(SynologyFileInventory.relative_path_normalized)
        .offset(offset)
        .limit(size)
    ).scalars().all()

    share_names = {
        row.id: row.share_name
        for row in session.execute(select(SynologyApprovedShare)).scalars().all()
    }
    documents: list[dict] = []
    for inventory in rows:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory.id)
        ).scalar_one_or_none()
        view = content or SimpleNamespace(
            status=0,
            extracted_text=None,
            approved_share_id=inventory.approved_share_id,
            relative_path_normalized=inventory.relative_path_normalized,
            extension=inventory.extension,
            file_name=inventory.file_name,
        )
        # Authoritative per-row check (grants may be revoked mid-page).
        if not docs.authorize_document(
            session,
            settings,
            user,
            inventory,
            view,
            capability=Capability.VIEW,
            require_extracted=False,
        ):
            continue
        documents.append(
            {
                "documentId": inventory.id.hex,
                "filename": inventory.file_name,
                "relativeFolder": docs._folder_of(inventory.relative_path_normalized),
                "relativePath": inventory.relative_path_normalized,
                "fileType": docs._file_type(inventory.extension),
                "extension": inventory.extension,
                "sizeBytes": int(inventory.size_bytes or 0),
                "modifiedAt": inventory.last_write_utc.isoformat() if inventory.last_write_utc else None,
                "indexingStatus": docs._indexing_state(content),
                "previewAvailability": docs._preview_availability(content, inventory.extension),
                "contentAvailable": docs._content_available(content),
                "source": share_names.get(inventory.approved_share_id, ""),
                "sourceId": str(inventory.approved_share_id),
            }
        )

    next_offset = offset + size
    return {
        "denied": False,
        "error": None,
        "sourceId": str(share_id),
        "folderPath": folder,
        "recursive": bool(recursive),
        "documents": documents,
        "count": len(documents),
        "totalEstimate": total,
        "nextCursor": _encode_cursor(next_offset) if next_offset < total else None,
    }


def get_index_status(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    *,
    source_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    user = load_active_user(session, caller_id)
    if user is None:
        return {"denied": True, "error": deny_message("no_verified_email")}

    is_admin = bool(user.is_super_admin)
    if not is_admin:
        access = evaluate_user(session, caller_id)
        if not access.allowed:
            return {"denied": True, "error": deny_message(access.reason)}
        # Non-admins: only approved summary for accessible sources.
        share_id, err = _resolve_source_id(session, user, source_id)
        if share_id is None:
            return {"denied": True, "error": err or "No authorized sources."}
        status = synology_svc.get_monitor(session)
        current = status.get("currentJob") or {}
        indexing = current.get("indexing") if isinstance(current.get("indexing"), dict) else {}
        return {
            "denied": False,
            "scope": "accessible_summary",
            "sourceId": str(share_id),
            "connectionStatus": (status.get("connection") or {}).get("connectionStatus"),
            "jobStatus": current.get("status"),
            "runStage": current.get("runStage"),
            "filesDiscovered": current.get("filesDiscovered"),
            "progressPercentage": indexing.get("percentComplete", current.get("percentComplete")),
            "lastSuccessfulSync": status.get("lastSuccessfulSyncAt"),
            "safeErrorSummary": current.get("safeError"),
            "note": "Detailed job counters are limited for non–Super Admin callers.",
        }

    if job_id:
        jid = _parse_doc_id(job_id)
        if jid is None:
            return {"denied": True, "error": "Invalid job_id."}
        job = synology_svc.get_job(session, jid)
        if job is None:
            return {"denied": True, "error": "Job not found."}
        return {"denied": False, "scope": "super_admin", "job": job}

    status = synology_svc.get_monitor(session)
    current = status.get("currentJob")
    indexing = (current or {}).get("indexing") if isinstance(current, dict) else None
    if not isinstance(indexing, dict):
        indexing = {}
    # Real counters only — never invent percentages.
    queued = indexing.get("queued")
    processed = indexing.get("processed")
    percent = indexing.get("percentComplete")
    if queued in (None, 0) and processed in (None, 0) and not (current or {}).get("enumerationComplete"):
        percent_out: float | None | str = "indeterminate"
    else:
        percent_out = percent

    return {
        "denied": False,
        "scope": "super_admin",
        "connectionStatus": (status.get("connection") or {}).get("connectionStatus"),
        "currentJob": current,
        "filesDiscovered": (current or {}).get("filesDiscovered"),
        "added": (current or {}).get("filesNew"),
        "modified": (current or {}).get("filesModified"),
        "unchanged": (current or {}).get("filesUnchanged"),
        "removed": (current or {}).get("filesRemoved"),
        "extractionEligible": indexing.get("queued"),
        "queued": indexing.get("queued"),
        "processing": indexing.get("processing"),
        "indexed": indexing.get("indexed"),
        "failed": indexing.get("failed"),
        "unsupported": indexing.get("unsupported"),
        "remaining": (current or {}).get("filesRemaining"),
        "progressPercentage": percent_out,
        "lastSuccessfulSync": status.get("lastSuccessfulSyncAt"),
        "lastExtractionUpdate": indexing.get("status"),
        "safeErrorSummary": (current or {}).get("safeError"),
        "stats": status.get("stats"),
    }


def create_indexing_confirmation(
    session: Session,
    *,
    actor_id: uuid.UUID,
    operation: str,
    source_id: str | None = None,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    op = (operation or "").strip().lower()
    if op not in INDEX_OPS:
        return {"denied": True, "error": f"Unsupported operation. Use one of: {', '.join(sorted(INDEX_OPS))}"}
    sid = _parse_doc_id(source_id) if source_id else None
    docs_json = json.dumps([str(x) for x in (document_ids or [])][:200])
    row = McpIndexingConfirmation(
        id=uuid.uuid4(),
        qka_user_id=actor_id,
        operation=op,
        source_id=sid,
        document_ids_json=docs_json,
        expires_at=_now() + CONFIRM_TTL,
        consumed_at=None,
        created_at=_now(),
        created_by_user_id=actor_id,
    )
    session.add(row)
    session.flush()
    return {
        "denied": False,
        "confirmationId": str(row.id),
        "operation": op,
        "sourceId": str(sid) if sid else None,
        "documentIds": json.loads(docs_json),
        "expiresAt": row.expires_at.isoformat(),
        "note": "Pass confirmationId to the MCP start_indexing tool. Single-use; expires in 10 minutes.",
    }


def _active_extraction_conflict(session: Session, share_id: uuid.UUID | None, operation: str) -> str | None:
    q = select(SynologyExtractionBatch).where(
        SynologyExtractionBatch.status.in_(
            [extraction_svc.BATCH_QUEUED, extraction_svc.BATCH_RUNNING]
        )
    )
    if share_id is not None:
        q = q.where(
            or_(
                SynologyExtractionBatch.approved_share_id == share_id,
                SynologyExtractionBatch.approved_share_id.is_(None),
            )
        )
    active = session.execute(q.limit(5)).scalars().all()
    for batch in active:
        # Sync-linked batches block overlapping extract work on same share.
        if batch.sync_job_id is not None:
            return f"An indexing batch is already active (batch={batch.id}, action={batch.action})."
        if operation in {"extract_missing", "retry_failed", "reindex_selected"} and batch.action == operation:
            return f"A '{operation}' batch is already processing (batch={batch.id})."
        if operation in {"extract_missing", "retry_failed", "reindex_selected"}:
            return f"An extraction batch is already active (batch={batch.id}, action={batch.action})."
    return None


def start_indexing(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    *,
    confirmation_id: str | None = None,
    operation: str | None = None,
    source_id: str | None = None,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    # Local Super Admin may lack Google verified-email; do not require evaluate_user here.
    user = load_active_user(session, caller_id)
    if user is None or not user.is_super_admin:
        return {
            "accepted": False,
            "denied": True,
            "error": "Super Admin only. Ordinary employees cannot start indexing via MCP.",
        }

    if not confirmation_id:
        return {
            "accepted": False,
            "denied": True,
            "requiresConfirmation": True,
            "error": (
                "Administrative confirmation required. Create a short-lived confirmation in the "
                "QKA Synology admin UI, then call start_indexing with confirmation_id."
            ),
            "monitoringInstructions": "Use get_index_status after a confirmed start.",
        }

    cid = _parse_doc_id(confirmation_id)
    if cid is None:
        return {"accepted": False, "denied": True, "error": "Invalid confirmation_id."}
    confirm = session.get(McpIndexingConfirmation, cid)
    if confirm is None:
        return {"accepted": False, "denied": True, "error": "Confirmation not found."}
    if confirm.qka_user_id != caller_id:
        return {"accepted": False, "denied": True, "error": "Confirmation is bound to a different admin."}
    if confirm.consumed_at is not None:
        return {"accepted": False, "denied": True, "error": "Confirmation already used."}
    if _as_utc(confirm.expires_at) < _now():
        return {"accepted": False, "denied": True, "error": "Confirmation expired."}

    op = confirm.operation
    if operation and operation.strip().lower() != op:
        return {"accepted": False, "denied": True, "error": "operation does not match confirmation."}
    if source_id and confirm.source_id and _parse_doc_id(source_id) != confirm.source_id:
        return {"accepted": False, "denied": True, "error": "source_id does not match confirmation."}

    confirmed_docs = json.loads(confirm.document_ids_json or "[]")
    if document_ids and [str(x) for x in document_ids] != [str(x) for x in confirmed_docs]:
        return {"accepted": False, "denied": True, "error": "document_ids do not match confirmation."}

    share_id = confirm.source_id
    conflict = _active_extraction_conflict(session, share_id, op)
    if conflict and op not in {"sync", "resync"}:
        return {"accepted": False, "denied": True, "error": conflict, "duplicateJob": True}

    try:
        if op in {"sync", "resync"}:
            connection_id = _connection_id_for_share(session, share_id)
            # start_sync already rejects when a sync job is running.
            job = synology_svc.start_sync(session, caller_id, connection_id)
            confirm.consumed_at = _now()
            session.flush()
            return {
                "accepted": True,
                "denied": False,
                "operation": op,
                "jobId": job.get("id"),
                "stage": job.get("runStage") or job.get("status"),
                "monitoringInstructions": "Call get_index_status with job_id to monitor progress.",
            }

        if op == "extract_missing":
            batch = extraction_svc.start_extract_missing(
                session, settings, actor_user_id=caller_id, share_id=share_id
            )
        elif op == "retry_failed":
            batch = extraction_svc.start_retry_failed(
                session, settings, actor_user_id=caller_id, share_id=share_id
            )
        elif op == "reindex_selected":
            ids = [_parse_doc_id(x) for x in confirmed_docs]
            ids = [x for x in ids if x is not None]
            if not ids:
                return {"accepted": False, "denied": True, "error": "reindex_selected requires document IDs in the confirmation."}
            batch = extraction_svc.start_reindex_selected(
                session, settings, actor_user_id=caller_id, inventory_ids=ids
            )
        else:
            return {"accepted": False, "denied": True, "error": f"Unsupported operation: {op}"}

        confirm.consumed_at = _now()
        session.flush()
        return {
            "accepted": True,
            "denied": False,
            "operation": op,
            "jobId": str(batch.id),
            "stage": "queued",
            "queuedCount": int(batch.queued_count or 0),
            "monitoringInstructions": "Call get_index_status to monitor extraction progress.",
        }
    except synology_svc.SynologyConflictError as exc:
        return {"accepted": False, "denied": True, "error": str(exc), "duplicateJob": True}
    except synology_svc.SynologyValidationError as exc:
        return {"accepted": False, "denied": True, "error": str(exc)}
    except ValueError as exc:
        return {"accepted": False, "denied": True, "error": str(exc)}
