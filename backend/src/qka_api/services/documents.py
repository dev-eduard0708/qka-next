from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, false, func, or_, select, text, true
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from qka_shared.allowlist import deny_message, evaluate_user, load_active_user
from qka_shared.config import Settings
from qka_shared.models import (
    AllowedEmail,
    ExternalIdentity,
    QkaUser,
    SourceGrant,
    SourceScope,
    SynologyApprovedShare,
    SynologyConnectionSettings,
    SynologyDocumentContent,
    SynologyFileInventory,
)
from qka_shared.source_access import Capability, authorize_document as source_authorize
from qka_shared.source_access import FILE_TYPE_EXTENSIONS
from qka_shared.source_access import GrantStatus, PathMode, ScopeStatus
from qka_shared.source_access import acl_needles as _acl_needles  # noqa: F401 — tests import this name
from qka_shared.source_access import classify_extension
from qka_shared.source_access import grant_has_capability
from qka_shared.source_access import is_authorized_managed_scope
from qka_shared.source_access import normalize_prefix as _prefix_normalized
from qka_shared.source_access import parse_json_list
from qka_shared.source_access import share_is_qka_managed
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
    size_bytes: int = 0


def _file_type(extension: str) -> str:
    return classify_extension(extension)


def _normalize_search_query(query: str) -> str:
    raw = (query or "").strip()
    return raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")


def _plain_search_needle(query: str) -> str:
    needle = _normalize_search_query(query)
    if len(needle) >= 2 and needle[0] == needle[-1] and needle[0] in {'"', "'"}:
        needle = needle[1:-1].strip()
    return needle


def _snippet(text: str | None, query: str, limit: int = 220) -> str:
    body = " ".join((text or "").split())
    if not body:
        return ""
    needle = _plain_search_needle(query)
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
    capability: str = Capability.VIEW,
    require_extracted: bool | None = None,
) -> bool:
    _ = settings
    return source_authorize(
        session,
        user,
        inventory,
        content,
        capability=capability,
        require_extracted=require_extracted,
    )


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
    fts_query = _normalize_search_query(query)
    needle = _plain_search_needle(query)

    sql = text(
        """
        SELECT c."Id"
        FROM synology_document_content AS c
        WHERE c."PilotScoped" = TRUE
          AND c."NonConfidential" = TRUE
          AND c."Status" = 1
          AND (:share_id IS NULL OR c."ApprovedShareId" = :share_id)
          AND NULLIF(websearch_to_tsquery('english', :query)::text, '') IS NOT NULL
          AND c.search_vector @@ websearch_to_tsquery('english', :query)
        ORDER BY ts_rank(c.search_vector, websearch_to_tsquery('english', :query)) DESC
        LIMIT :limit
        """
    )
    matched_ids: list = []
    try:
        matched_ids = [
            row[0]
            for row in session.execute(
                sql,
                {"query": fts_query, "share_id": share_id, "limit": take * 5},
            ).all()
        ]
    except Exception:
        session.rollback()
        matched_ids = []
        fts_query = _plain_search_needle(query)
    if not matched_ids and needle:
        like = f"%{needle}%"
        fallback = select(SynologyDocumentContent.id).where(
            SynologyDocumentContent.pilot_scoped.is_(True),
            SynologyDocumentContent.non_confidential.is_(True),
            SynologyDocumentContent.status == 1,
            or_(
                SynologyDocumentContent.extracted_text.ilike(like),
                SynologyDocumentContent.file_name.ilike(like),
                SynologyDocumentContent.relative_path_normalized.ilike(like),
            ),
        )
        if share_id:
            fallback = fallback.where(SynologyDocumentContent.approved_share_id == share_id)
        matched_ids = [
            row[0] for row in session.execute(fallback.limit(take * 5)).all()
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
        if not authorize_document(session, settings, user, inventory, content, capability=Capability.VIEW):
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
                size_bytes=int(inventory.size_bytes or 0),
            )
        )
        if len(hits) >= take:
            break
    return hits, None


_CATALOG_TYPES = (
    ("excel", "Excel", "table_chart"),
    ("word", "Word", "description"),
    ("pdf", "PDF", "picture_as_pdf"),
    ("powerpoint", "PowerPoint", "slideshow"),
    ("images", "Images", "image"),
    ("csv", "CSV", "table_chart"),
    ("text", "Text", "article"),
    ("other", "Other", "insert_drive_file"),
)


def _path_under_clause(prefix: str, *, include_nested: bool = True) -> ColumnElement[bool]:
    path_col = SynologyFileInventory.relative_path_normalized
    prefix = _prefix_normalized(prefix)
    if not prefix:
        return true()
    # Windows paths use `\`. Force LIKE escape `/` so `\` is literal, not an escape.
    child = path_col.like(prefix + "\\%", escape="/")
    if include_nested:
        return or_(path_col == prefix, child)
    one_level = and_(child, ~path_col.like(prefix + "\\%\\%", escape="/"))
    return or_(path_col == prefix, one_level)


def _grant_path_clause(scope: SourceScope, grant: SourceGrant) -> ColumnElement[bool]:
    prefix = _prefix_normalized(scope.folder_prefix_normalized)
    scope_clause = _path_under_clause(prefix)
    mode = int(getattr(grant, "path_mode", PathMode.ALL_IN_SCOPE) or PathMode.ALL_IN_SCOPE)
    if mode == PathMode.ALL_IN_SCOPE:
        return scope_clause
    selected = [_prefix_normalized(item) for item in parse_json_list(grant.selected_paths_json)]
    selected = [item for item in selected if item]
    if not selected:
        return false()
    include_sub = bool(getattr(grant, "include_subfolders", True))
    parts = [_path_under_clause(item, include_nested=include_sub) for item in selected]
    return and_(scope_clause, or_(*parts))


def _grant_type_clause(grant: SourceGrant) -> ColumnElement[bool]:
    types = [item.lower() for item in parse_json_list(grant.file_types_json)]
    if not types or "all" in types:
        return true()
    exts: set[str] = set()
    for name in types:
        exts |= {ext.lower() for ext in FILE_TYPE_EXTENSIONS.get(name, set())}
    if not exts:
        return SynologyFileInventory.file_type_category == 0
    return func.lower(SynologyFileInventory.extension).in_(sorted(exts))


def _count_share_types(session: Session, share_id: uuid.UUID, match: ColumnElement[bool]) -> dict[str, int]:
    rows = session.execute(
        select(
            func.lower(SynologyFileInventory.extension),
            func.count(func.distinct(SynologyFileInventory.id)),
        )
        .where(
            SynologyFileInventory.approved_share_id == share_id,
            SynologyFileInventory.is_present.is_(True),
            SynologyFileInventory.is_directory.is_(False),
            match,
        )
        .group_by(func.lower(SynologyFileInventory.extension))
    ).all()
    counts = {key: 0 for key, _label, _icon in _CATALOG_TYPES}
    for ext, n in rows:
        counts[classify_extension(ext or "")] += int(n)
    return counts


def search_catalog(session: Session, settings: Settings, caller_id: uuid.UUID) -> dict:
    """Authorized sources and file-type counts for the Search landing header."""
    _ = settings
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return {"sources": [], "totalFiles": 0, "error": None}
    user = load_active_user(session, caller_id)
    if user is None:
        return {"sources": [], "totalFiles": 0, "error": None}

    grants = session.execute(
        select(SourceGrant)
        .options(selectinload(SourceGrant.scope))
        .where(
            SourceGrant.qka_user_id == user.id,
            SourceGrant.status == GrantStatus.ACTIVE,
        )
    ).scalars().all()

    match_by_share: dict[uuid.UUID, list[ColumnElement[bool]]] = {}
    for grant in grants:
        scope = grant.scope
        if scope is None or scope.status != ScopeStatus.ACTIVE:
            continue
        if not grant_has_capability(grant, Capability.VIEW):
            continue
        share = session.get(SynologyApprovedShare, scope.approved_share_id)
        if share is None or not share.is_active:
            continue
        if not (share_is_qka_managed(share) or is_authorized_managed_scope(scope)):
            continue
        match_by_share.setdefault(share.id, []).append(
            and_(_grant_path_clause(scope, grant), _grant_type_clause(grant))
        )

    needles = [item.strip() for item in _acl_needles(user) if item and item.strip()]
    if needles:
        acl_match = and_(
            SynologyFileInventory.acl_capture_status == 1,
            or_(*[SynologyFileInventory.acl_snapshot_json.ilike(f"%{needle}%") for needle in needles]),
        )
        shares = session.execute(
            select(SynologyApprovedShare).where(SynologyApprovedShare.is_active.is_(True))
        ).scalars().all()
        for share in shares:
            if share.id in match_by_share:
                continue
            if share_is_qka_managed(share):
                continue
            match_by_share[share.id] = [acl_match]

    sources: list[dict] = []
    for share_id, clauses in match_by_share.items():
        share = session.get(SynologyApprovedShare, share_id)
        if share is None:
            continue
        counts = _count_share_types(session, share_id, or_(*clauses))
        total = sum(counts.values())
        if total <= 0:
            continue
        sources.append(
            {
                "approvedShareId": str(share.id),
                "shareName": share.share_name,
                "totalFiles": total,
                "types": [
                    {"key": key, "label": label, "icon": icon, "count": counts[key]}
                    for key, label, icon in _CATALOG_TYPES
                    if counts[key] > 0
                ],
            }
        )
    sources.sort(key=lambda row: row["shareName"].lower())
    return {"sources": sources, "totalFiles": sum(row["totalFiles"] for row in sources), "error": None}


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
        session, settings, user, inventory, content, capability=Capability.VIEW
    ):
        return {"id": document_id, "denied": True, "error": "Access denied."}

    body = content.extracted_text or ""
    start = max(0, offset)
    chunk = body[start : start + settings.max_preview_chars]
    return {
        "id": content.inventory_file_id.hex,
        "title": content.file_name,
        "text": chunk,
        "previewKind": "indexed_text",
        "previewNote": "PostgreSQL indexed text preview — not a full original-file rendering.",
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


def authorize_download(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
) -> dict:
    """Authorize original-file download. Does not open SMB; caller streams from cache/NAS separately."""
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
    inventory = session.get(SynologyFileInventory, inventory_id)
    if content is None or inventory is None or not inventory.is_present:
        return {"id": document_id, "denied": True, "error": "Document not found."}
    if not authorize_document(session, settings, user, inventory, content, capability=Capability.DOWNLOAD):
        return {
            "id": document_id,
            "denied": True,
            "error": "Download Originals permission is required.",
            "requiresDownloadGrant": True,
        }
    return {
        "id": inventory.id.hex,
        "denied": False,
        "fileName": inventory.file_name,
        "relativePath": inventory.relative_path_normalized,
        "extension": inventory.extension,
        "sizeBytes": inventory.size_bytes,
        "approvedShareId": str(inventory.approved_share_id),
        "source": "synology",
        "note": "Original file download requires authorized Synology access when no local cache exists.",
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
    stmt = select(SynologyFileInventory).where(
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.is_directory.is_(False),
    )
    if settings.approved_share_id:
        stmt = stmt.where(SynologyFileInventory.approved_share_id == settings.approved_share_id)
    if prefix:
        stmt = stmt.where(
            or_(
                SynologyFileInventory.relative_path_normalized == prefix,
                SynologyFileInventory.relative_path_normalized.like(prefix + "\\%"),
            )
        )
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
        if authorize_document(session, settings, user, inventory, content, capability=Capability.VIEW):
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
