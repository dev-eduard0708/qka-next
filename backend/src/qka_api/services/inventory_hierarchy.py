"""PostgreSQL Synology folder hierarchy — reuse synology_file_inventory rows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from qka_shared.models import SynologyFileInventory
from qka_shared.source_access import FILE_TYPE_EXTENSIONS, normalize_prefix


def parent_normalized(path_norm: str) -> str:
    value = (path_norm or "").replace("/", "\\").strip("\\")
    if "\\" not in value:
        return ""
    return value.rsplit("\\", 1)[0]


def path_segments(relative: str) -> list[str]:
    return [part for part in (relative or "").replace("/", "\\").strip("\\").split("\\") if part]


def breadcrumb_for(path_norm: str) -> list[dict]:
    parts = path_segments(path_norm)
    accum: list[str] = []
    crumbs: list[dict] = []
    for part in parts:
        accum.append(part)
        key = "\\".join(accum)
        crumbs.append({"label": part, "path": key})
    return crumbs


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backfill_inventory_hierarchy(session: Session) -> dict:
    """Fill ParentPathNormalized and insert missing folder rows. Never deletes inventory."""
    rows = session.execute(select(SynologyFileInventory)).scalars().all()
    existing = {(row.approved_share_id, row.relative_path_normalized) for row in rows}
    created = 0
    now = _now()
    pending: list[SynologyFileInventory] = []
    for row in rows:
        row.parent_path_normalized = parent_normalized(row.relative_path_normalized)
        rel = row.relative_path.replace("/", "\\").strip("\\")
        parts = path_segments(rel)
        folder_parts = parts if bool(getattr(row, "is_directory", False)) else parts[:-1]
        accum: list[str] = []
        for part in folder_parts:
            accum.append(part)
            rel_path = "\\".join(accum)
            key = rel_path.upper()
            ident = (row.approved_share_id, key)
            if ident in existing:
                continue
            pending.append(
                SynologyFileInventory(
                    id=uuid.uuid4(),
                    approved_share_id=row.approved_share_id,
                    relative_path=rel_path,
                    relative_path_normalized=key,
                    file_name=part,
                    extension="",
                    file_type_category=0,
                    size_bytes=0,
                    sync_status=0,
                    acl_capture_status=0,
                    is_present=True,
                    is_directory=True,
                    parent_path_normalized=parent_normalized(key),
                    created_at=now,
                    updated_at=now,
                )
            )
            existing.add(ident)
            created += 1
    if pending:
        session.add_all(pending)
        session.flush()
    return {"folderRowsCreated": created, "scanned": len(rows)}


def _file_icon(label: str, is_file: bool) -> str:
    if not is_file:
        return "folder"
    ext = label.rsplit(".", 1)[-1].lower() if "." in label else ""
    for name, exts in FILE_TYPE_EXTENSIONS.items():
        if ext in exts:
            return {
                "excel": "grid_on",
                "word": "description",
                "pdf": "picture_as_pdf",
                "powerpoint": "slideshow",
                "images": "image",
                "csv": "table_chart",
                "text": "article",
            }.get(name, "insert_drive_file")
    return "insert_drive_file"


def _node(row: SynologyFileInventory) -> dict:
    is_file = not bool(row.is_directory)
    return {
        "id": row.relative_path_normalized,
        "inventoryId": str(row.id),
        "label": row.file_name or path_segments(row.relative_path)[-1],
        "path": row.relative_path_normalized,
        "isFile": is_file,
        "lazy": not is_file,
        "icon": _file_icon(row.file_name, is_file),
        "aclCaptureStatus": int(row.acl_capture_status or 0) if is_file else None,
        "sizeBytes": int(row.size_bytes or 0),
        "extension": row.extension or "",
        "children": [] if not is_file else None,
    }


_FILE_TYPE_CATEGORY = {
    "excel": 1,
    "pdf": 2,
    "word": 3,
    "powerpoint": 4,
    "csv": 5,
}


def _file_type_category_filter(file_type: str | None):
    if not file_type:
        return None
    wanted = file_type.strip().lower()
    if wanted in {"all", ""}:
        return None
    return _FILE_TYPE_CATEGORY.get(wanted)


def list_folder_children(
    session: Session,
    share_id: uuid.UUID,
    parent_path: str = "",
    *,
    search: str | None = None,
    file_type: str | None = None,
    page: int = 1,
    page_size: int = 200,
) -> dict:
    parent = normalize_prefix(parent_path)
    page = max(1, page)
    page_size = max(1, min(page_size or 200, 500))
    filters = [
        SynologyFileInventory.approved_share_id == share_id,
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.parent_path_normalized == parent,
    ]
    q = (search or "").strip()
    if q:
        filters.append(SynologyFileInventory.file_name.ilike(f"%{q}%"))
    category = _file_type_category_filter(file_type)
    if category is not None:
        filters.append(
            or_(
                SynologyFileInventory.is_directory.is_(True),
                SynologyFileInventory.file_type_category == category,
            )
        )

    base = select(SynologyFileInventory).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    folder_count = session.scalar(
        select(func.count()).select_from(SynologyFileInventory).where(*filters, SynologyFileInventory.is_directory.is_(True))
    ) or 0
    rows = session.execute(
        base.order_by(
            SynologyFileInventory.is_directory.desc(),
            SynologyFileInventory.relative_path_normalized,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return {
        "parentPath": parent,
        "breadcrumb": breadcrumb_for(parent),
        "items": [_node(row) for row in rows],
        "total": int(total),
        "folderCount": int(folder_count),
        "fileCount": int(total) - int(folder_count),
        "page": page,
        "pageSize": page_size,
        "hasMore": page * page_size < int(total),
    }


def folder_tree_roots(
    session: Session,
    share_id: uuid.UUID,
    root_prefix: str | None,
) -> list[dict]:
    """One level only. Folders are lazy; never return the whole share as nested JSON."""
    prefix = "" if root_prefix is None else normalize_prefix(root_prefix)
    if prefix:
        row = session.execute(
            select(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share_id,
                SynologyFileInventory.relative_path_normalized == prefix,
                SynologyFileInventory.is_present.is_(True),
            )
        ).scalar_one_or_none()
        if row is not None:
            node = _node(row)
            node["isFile"] = False
            node["lazy"] = True
            node["icon"] = "folder"
            node["children"] = []
            return [node]
        # Prefix exists only as a parent of files (not yet a folder row).
        child_page = list_folder_children(session, share_id, prefix, page=1, page_size=1)
        if child_page["total"] == 0:
            return []
        label = path_segments(prefix)[-1] if path_segments(prefix) else prefix
        return [
            {
                "id": prefix,
                "inventoryId": None,
                "label": label,
                "path": prefix,
                "isFile": False,
                "lazy": True,
                "icon": "folder",
                "aclCaptureStatus": None,
                "children": [],
            }
        ]
    page = list_folder_children(session, share_id, "", page=1, page_size=200)
    return page["items"]


def search_folder_nodes(
    session: Session,
    share_id: uuid.UUID,
    query: str,
    *,
    parent_path: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    q = (query or "").strip()
    page = max(1, page)
    page_size = max(1, min(page_size or 50, 100))
    if not q:
        return {"items": [], "total": 0, "page": page, "pageSize": page_size, "query": q}
    parent = normalize_prefix(parent_path)
    filters = [
        SynologyFileInventory.approved_share_id == share_id,
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.file_name.ilike(f"%{q}%"),
    ]
    if parent:
        filters.append(
            or_(
                SynologyFileInventory.relative_path_normalized == parent,
                SynologyFileInventory.relative_path_normalized.like(parent + "\\%"),
            )
        )
    base = select(SynologyFileInventory).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(SynologyFileInventory.relative_path_normalized)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return {
        "query": q,
        "parentPath": parent,
        "items": [_node(row) for row in rows],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
    }


def present_file_filters():
    return (
        SynologyFileInventory.is_present.is_(True),
        SynologyFileInventory.is_directory.is_(False),
    )
