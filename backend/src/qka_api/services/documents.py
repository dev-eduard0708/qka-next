from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from qka_shared.allowlist import deny_message, evaluate_user, load_active_user
from qka_shared.config import Settings
from qka_shared.models import (
    AllowedEmail,
    ExternalIdentity,
    QkaUser,
    SynologyConnectionSettings,
    SynologyDocumentContent,
    SynologyFileInventory,
)
from qka_shared.source_access import Capability, authorize_document as source_authorize
from qka_shared.source_access import normalize_prefix as _prefix_normalized
from qka_shared.source_access import under_prefix as _under_prefix


@dataclass(frozen=True)
class SearchHit:
    id: str
    title: str
    url: str
    snippet: str
    relative_path: str
    extension: str
    file_type: str


def _file_type(extension: str) -> str:
    ext = (extension or "").lower().lstrip(".")
    mapping = {
        "xlsx": "excel",
        "xlsm": "excel",
        "xls": "excel",
        "csv": "csv",
        "pdf": "pdf",
        "docx": "word",
        "doc": "word",
        "pptx": "powerpoint",
        "ppt": "powerpoint",
    }
    return mapping.get(ext, "other")


def _snippet(text: str | None, query: str, limit: int = 220) -> str:
    body = " ".join((text or "").split())
    if not body:
        return ""
    needle = query.strip()
    idx = body.lower().find(needle.lower()) if needle else -1
    if idx < 0:
        return body[:limit]
    start = max(0, idx - 40)
    return body[start : start + limit]


def authorize_document(
    session: Session,
    settings: Settings,
    user: QkaUser,
    inventory: SynologyFileInventory,
    content: SynologyDocumentContent,
    *,
    capability: str = Capability.PREVIEW,
) -> bool:
    _ = settings
    return source_authorize(session, user, inventory, content, capability=capability)


def search_documents(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    query: str,
) -> tuple[list[SearchHit], str | None]:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return [], deny_message(access.reason)
    user = load_active_user(session, caller_id)
    if user is None:
        return [], deny_message("no_verified_email")
    if not query.strip():
        return [], "Query is required."

    share_id = settings.approved_share_id
    prefix = _prefix_normalized(settings.pilot_folder_prefix)
    take = max(1, min(settings.max_search_results, 50))

    sql = text(
        """
        SELECT c."Id"
        FROM synology_document_content AS c
        WHERE c."PilotScoped" = TRUE
          AND c."NonConfidential" = TRUE
          AND c."Status" = 1
          AND (:share_id IS NULL OR c."ApprovedShareId" = :share_id)
          AND c.search_vector @@ plainto_tsquery('english', :query)
        ORDER BY ts_rank(c.search_vector, plainto_tsquery('english', :query)) DESC
        LIMIT :limit
        """
    )
    matched_ids = [
        row[0]
        for row in session.execute(
            sql,
            {"query": query, "share_id": share_id, "limit": take * 5},
        ).all()
    ]
    if not matched_ids:
        return [], None

    contents = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.id.in_(matched_ids))
    ).scalars().all()
    by_id = {row.id: row for row in contents}
    hits: list[SearchHit] = []
    for content_id in matched_ids:
        content = by_id.get(content_id)
        if content is None or not _under_prefix(content.relative_path_normalized, prefix):
            continue
        inventory = session.get(SynologyFileInventory, content.inventory_file_id)
        if inventory is None or not inventory.is_present:
            continue
        if not authorize_document(session, settings, user, inventory, content, capability=Capability.SEARCH):
            continue
        hits.append(
            SearchHit(
                id=content.inventory_file_id.hex,
                title=content.file_name,
                url=f"qka://synology/{content.approved_share_id.hex}/{content.relative_path_normalized.replace(chr(92), '/')}",
                snippet=_snippet(content.extracted_text, query),
                relative_path=content.relative_path_normalized,
                extension=content.extension,
                file_type=_file_type(content.extension),
            )
        )
        if len(hits) >= take:
            break
    return hits, None


def fetch_document(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
    offset: int = 0,
) -> dict:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return {"id": document_id, "denied": True, "error": deny_message(access.reason)}
    user = load_active_user(session, caller_id)
    if user is None:
        return {"id": document_id, "denied": True, "error": deny_message("no_verified_email")}

    try:
        inventory_id = uuid.UUID(document_id) if "-" in document_id else uuid.UUID(hex=document_id)
    except ValueError:
        return {"id": document_id, "denied": True, "error": "Document not found."}

    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory_id)
    ).scalar_one_or_none()
    prefix = _prefix_normalized(settings.pilot_folder_prefix)
    if (
        content is None
        or not content.pilot_scoped
        or not content.non_confidential
        or content.status != 1
        or (settings.approved_share_id and content.approved_share_id != settings.approved_share_id)
        or not _under_prefix(content.relative_path_normalized, prefix)
    ):
        return {"id": document_id, "denied": True, "error": "Document not found or outside pilot scope."}

    inventory = session.get(SynologyFileInventory, content.inventory_file_id)
    if inventory is None or not authorize_document(
        session, settings, user, inventory, content, capability=Capability.PREVIEW
    ):
        return {"id": document_id, "denied": True, "error": "Access denied."}

    body = content.extracted_text or ""
    start = max(0, offset)
    chunk = body[start : start + settings.max_preview_chars]
    return {
        "id": content.inventory_file_id.hex,
        "title": content.file_name,
        "text": chunk,
        "offset": start,
        "totalChars": len(body),
        "hasMore": start + len(chunk) < len(body),
        "url": f"qka://synology/{content.approved_share_id.hex}/{content.relative_path_normalized.replace(chr(92), '/')}",
        "relativePath": content.relative_path_normalized,
        "extension": content.extension,
        "fileType": _file_type(content.extension),
        "denied": False,
        "error": None,
    }


def list_inventory(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    search: str | None,
    extension: str | None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int, str | None]:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return [], 0, deny_message(access.reason)
    user = load_active_user(session, caller_id)
    if user is None:
        return [], 0, deny_message("no_verified_email")

    prefix = _prefix_normalized(settings.pilot_folder_prefix)
    stmt = select(SynologyFileInventory).where(SynologyFileInventory.is_present.is_(True))
    if settings.approved_share_id:
        stmt = stmt.where(SynologyFileInventory.approved_share_id == settings.approved_share_id)
    if prefix:
        stmt = stmt.where(SynologyFileInventory.relative_path_normalized.startswith(prefix))
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                SynologyFileInventory.file_name.ilike(like),
                SynologyFileInventory.relative_path.ilike(like),
            )
        )
    if extension:
        stmt = stmt.where(SynologyFileInventory.extension.ilike(extension.lstrip(".")))

    rows = session.execute(stmt.order_by(SynologyFileInventory.file_name).limit(400)).scalars().all()
    authorized: list[SynologyFileInventory] = []
    for inventory in rows:
        if not _under_prefix(inventory.relative_path_normalized, prefix):
            continue
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory.id)
        ).scalar_one_or_none()
        if content is None:
            continue
        if authorize_document(session, settings, user, inventory, content, capability=Capability.READ):
            authorized.append(inventory)

    total = len(authorized)
    page = authorized[offset : offset + max(1, min(limit, 100))]
    items = [
        {
            "id": item.id.hex,
            "fileName": item.file_name,
            "relativePath": item.relative_path,
            "extension": item.extension,
            "fileType": _file_type(item.extension),
            "sizeBytes": item.size_bytes,
            "lastWriteUtc": item.last_write_utc.isoformat() if item.last_write_utc else None,
        }
        for item in page
    ]
    return items, total, None


def summary(session: Session, settings: Settings | None = None) -> dict:
    from qka_shared.config import get_settings

    cfg = settings or get_settings()
    inventory = session.scalar(select(func.count()).select_from(SynologyFileInventory))
    extracted = session.scalar(
        select(func.count()).select_from(SynologyDocumentContent).where(SynologyDocumentContent.status == 1)
    )
    google_workspace_users = session.scalar(
        select(func.count()).select_from(ExternalIdentity).where(
            ExternalIdentity.provider == 1,
            ExternalIdentity.link_status == 2,
            ExternalIdentity.account_status == 0,
        )
    )
    allowlisted_emails = session.scalar(
        select(func.count()).select_from(AllowedEmail).where(AllowedEmail.enabled.is_(True))
    )
    synology_connections = session.scalar(
        select(func.count()).select_from(SynologyConnectionSettings).where(
            SynologyConnectionSettings.configured_shared_folder_path != ""
        )
    )
    sources = [
        {
            "id": "synology",
            "name": "Synology NAS",
            "kind": "synology",
            "connectionCount": int(synology_connections or 0),
        }
    ]
    openai_configured = cfg.openai_configured
    return {
        "inventoryCount": int(inventory or 0),
        "extractedCount": int(extracted or 0),
        "googleWorkspaceUsers": int(google_workspace_users or 0),
        "allowlistedEmails": int(allowlisted_emails or 0),
        "synologyConnections": int(synology_connections or 0),
        "sourceCount": len([item for item in sources if item["connectionCount"] > 0]),
        "sources": sources,
        "openaiConfigured": openai_configured,
        "openaiModel": cfg.openai_model if openai_configured else None,
        "openaiAvailable": openai_configured,
        "mcpParity": True,
        "oauthParity": True,
    }
