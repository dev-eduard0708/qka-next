from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.smb import SmbGateway
from qka_api.services.documents import authorize_document, _file_type
from qka_api.services.preview_engine import (
    OFFICE_EXTS,
    PDF_EXTS,
    ensure_original_render,
    libreoffice_status,
    read_page_bytes,
)
from qka_shared.allowlist import deny_message, evaluate_user, load_active_user
from qka_shared.config import Settings
from qka_shared.models import SynologyApprovedShare, SynologyConnectionSettings, SynologyDocumentContent, SynologyFileInventory
from qka_shared.source_access import Capability
from qka_shared.unc import build_unc

log = logging.getLogger("qka.preview")

EXCEL_ROW_PAGE = 80
EXCEL_MAX_COLS = 40
MAX_PDF_PAGE_CHARS = 8000
MAX_WORD_BLOCKS = 400
MAX_SLIDE_SHAPES = 80
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"}
PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _cache_root(settings: Settings) -> Path:
    raw = (settings.preview_cache_dir or "").strip()
    if raw:
        root = Path(raw)
    else:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        root = Path(base) / "QKA-Next" / "preview-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _version_key(inventory: SynologyFileInventory, content: Any) -> str:
    digest = (getattr(content, "content_sha256", None) or "").strip()
    if digest:
        return digest[:64]
    stamp = inventory.last_write_utc.isoformat() if inventory.last_write_utc else "na"
    raw = f"{inventory.size_bytes}|{stamp}|{inventory.relative_path_normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _cache_path(settings: Settings, inventory_id: uuid.UUID, version: str, name: str) -> Path:
    folder = _cache_root(settings) / str(inventory_id) / version
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def _read_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _content_view(inventory: SynologyFileInventory, content: SynologyDocumentContent | None) -> Any:
    if content is not None:
        return content
    return SimpleNamespace(
        status=0,
        extracted_text=None,
        content_sha256=None,
        approved_share_id=inventory.approved_share_id,
        relative_path_normalized=inventory.relative_path_normalized,
        extension=inventory.extension,
        file_name=inventory.file_name,
    )


def _denied(document_id: str, error: str, *, kind: str = "unavailable") -> dict:
    return {
        "id": document_id,
        "denied": True,
        "error": error,
        "previewKind": kind,
        "previewAvailable": False,
        "canDownload": False,
    }


def _base_payload(
    inventory: SynologyFileInventory,
    content: Any,
    *,
    can_download: bool,
) -> dict:
    path = inventory.relative_path_normalized or ""
    parent = "\\".join(path.split("\\")[:-1]) if "\\" in path else ""
    return {
        "id": inventory.id.hex,
        "denied": False,
        "error": None,
        "title": inventory.file_name,
        "fileType": _file_type(inventory.extension),
        "extension": inventory.extension,
        "relativePath": path,
        "folder": parent,
        "sizeBytes": int(inventory.size_bytes or 0),
        "previewAvailable": True,
        "canDownload": can_download,
        "url": f"qka://synology/{inventory.approved_share_id.hex}/{path.replace(chr(92), '/')}",
        "extractedAvailable": bool(getattr(content, "extracted_text", None)),
    }


def _indexed_fallback(inventory: SynologyFileInventory, content: Any, can_download: bool, note: str | None = None) -> dict:
    body = (getattr(content, "extracted_text", None) or "").strip()
    payload = _base_payload(inventory, content, can_download=can_download)
    if not body:
        payload.update(
            {
                "previewKind": "unavailable",
                "previewAvailable": False,
                "previewNote": note or "Preview is not available for this file.",
                "text": None,
            }
        )
        return payload
    payload.update(
        {
            "previewKind": "indexed_text",
            "previewNote": note
            or "Indexed Text View — original formatting not available.",
            "text": body[:20_000],
            "hasMore": len(body) > 20_000,
            "totalChars": len(body),
        }
    )
    return payload


def _connection_for_share(session: Session, share_id: uuid.UUID) -> SynologyConnectionSettings | None:
    return session.execute(
        select(SynologyConnectionSettings).where(SynologyConnectionSettings.primary_approved_share_id == share_id)
    ).scalars().first()


def _read_source_bytes(
    session: Session,
    settings: Settings,
    gateway: SmbGateway | None,
    inventory: SynologyFileInventory,
) -> bytes | None:
    if gateway is None:
        return None
    if int(inventory.size_bytes or 0) > int(settings.preview_max_source_bytes or 25_000_000):
        return None
    try:
        return gateway.open_read("", inventory.relative_path, None)
    except FileNotFoundError:
        pass
    except Exception:
        log.debug("preview.direct_read_miss path=%s", inventory.relative_path_normalized)
    share = session.get(SynologyApprovedShare, inventory.approved_share_id)
    if share is None:
        return None
    connection = _connection_for_share(session, share.id)
    if connection is None:
        return None
    try:
        from qka_api.services.synology import _credentials

        unc = build_unc(connection.host_name, share.share_name, share.folder_relative_path)
        creds = _credentials(settings, connection)
        return gateway.open_read(unc, inventory.relative_path, creds)
    except Exception:
        log.info("preview.smb_read_failed id=%s", inventory.id)
        return None


def _excel_grid(payload: bytes, *, sheet: int, row_offset: int) -> dict:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        names = [item.title for item in workbook.worksheets]
        if not names:
            return {"error": "Workbook has no worksheets."}
        index = max(0, min(int(sheet), len(names) - 1))
        ws = workbook.worksheets[index]
        skip = max(0, int(row_offset))
        rows: list[list[str]] = []
        truncated = False
        seen = 0
        total = 0
        for row in ws.iter_rows(values_only=True):
            values = ["" if cell is None else str(cell) for cell in row[:EXCEL_MAX_COLS]]
            if not any(item.strip() for item in values):
                continue
            total += 1
            if seen < skip:
                seen += 1
                continue
            if len(rows) >= EXCEL_ROW_PAGE:
                truncated = True
                break
            rows.append(values)
            seen += 1
        return {
            "previewKind": "excel_grid",
            "previewNote": "Read-only worksheet preview. Formulas are not executed.",
            "sheets": [{"index": i, "name": name} for i, name in enumerate(names)],
            "sheet": index,
            "sheetName": names[index],
            "rowOffset": skip,
            "rowCount": len(rows),
            "totalRows": total + (1 if truncated else 0),
            "hasMore": truncated,
            "columns": EXCEL_MAX_COLS,
            "rows": rows,
        }
    finally:
        workbook.close()


def _word_blocks(payload: bytes) -> dict:
    from docx import Document

    document = Document(io.BytesIO(payload))
    blocks: list[dict] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").rstrip()
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        heading = style.lower().startswith("heading")
        level = 0
        if heading:
            digits = "".join(ch for ch in style if ch.isdigit())
            level = int(digits) if digits else 1
        blocks.append({"type": "heading" if heading else "p", "text": text, "level": level})
        if len(blocks) >= MAX_WORD_BLOCKS:
            break
    if len(blocks) < MAX_WORD_BLOCKS:
        for table in document.tables:
            grid = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            blocks.append({"type": "table", "rows": grid[:40]})
            if len(blocks) >= MAX_WORD_BLOCKS:
                break
    return {
        "previewKind": "word_document",
        "previewNote": "Read-only Word preview. Layout may differ from the original file.",
        "blocks": blocks,
        "truncated": len(blocks) >= MAX_WORD_BLOCKS,
    }


def _pdf_page(payload: bytes, page: int) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    total = len(reader.pages)
    if total <= 0:
        return {"error": "PDF has no pages."}
    index = max(0, min(int(page) - 1 if page else 0, total - 1))
    text = reader.pages[index].extract_text() or ""
    return {
        "previewKind": "pdf_pages",
        "previewNote": "Read-only PDF text preview by page. This is not a print-faithful rendering.",
        "page": index + 1,
        "pageCount": total,
        "text": text[:MAX_PDF_PAGE_CHARS],
        "hasMoreText": len(text) > MAX_PDF_PAGE_CHARS,
    }


def _pptx_slides(payload: bytes, slide: int) -> dict:
    try:
        from pptx import Presentation

        presentation = Presentation(io.BytesIO(payload))
        total = len(presentation.slides)
        if total <= 0:
            return {"error": "Presentation has no slides."}
        index = max(0, min(int(slide), total - 1))
        current = presentation.slides[index]
        chunks: list[str] = []
        for shape in current.shapes:
            if hasattr(shape, "text") and shape.text:
                chunks.append(shape.text.strip())
            if len(chunks) >= MAX_SLIDE_SHAPES:
                break
        thumbs = []
        for i, item in enumerate(presentation.slides):
            title = ""
            for shape in item.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    title = shape.text.strip().splitlines()[0][:80]
                    break
            thumbs.append({"index": i, "title": title or f"Slide {i + 1}"})
        return {
            "previewKind": "powerpoint_slides",
            "previewNote": "Read-only slide text preview.",
            "slide": index,
            "slideCount": total,
            "title": thumbs[index]["title"] if thumbs else f"Slide {index + 1}",
            "body": "\n".join(chunks),
            "thumbnails": thumbs,
        }
    except ImportError:
        return _pptx_from_zip(payload, slide)


def _pptx_from_zip(payload: bytes, slide: int) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return {"error": "PowerPoint file could not be opened."}
    names = sorted(
        name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")
    )
    if not names:
        return {"error": "Presentation has no slides."}
    index = max(0, min(int(slide), len(names) - 1))
    thumbs = []
    bodies: list[str] = []
    for i, name in enumerate(names):
        root = ET.fromstring(archive.read(name))
        texts = [node.text.strip() for node in root.findall(".//a:t", PPT_NS) if node.text and node.text.strip()]
        body = "\n".join(texts)
        bodies.append(body)
        title = texts[0][:80] if texts else f"Slide {i + 1}"
        thumbs.append({"index": i, "title": title})
    return {
        "previewKind": "powerpoint_slides",
        "previewNote": "Read-only slide text preview.",
        "slide": index,
        "slideCount": len(names),
        "title": thumbs[index]["title"],
        "body": bodies[index],
        "thumbnails": thumbs,
    }


def _csv_table(payload: bytes | None, extracted: str | None) -> dict:
    raw = ""
    if payload:
        raw = payload.decode("utf-8", errors="replace")
    elif extracted:
        raw = extracted
    if not raw.strip():
        return {"error": "CSV is empty."}
    reader = csv.reader(io.StringIO(raw))
    rows = []
    for i, row in enumerate(reader):
        if i >= EXCEL_ROW_PAGE:
            break
        rows.append([str(cell) for cell in row[:EXCEL_MAX_COLS]])
    return {
        "previewKind": "csv_table",
        "previewNote": "Read-only table preview.",
        "rows": rows,
        "truncated": raw.count("\n") > EXCEL_ROW_PAGE,
    }


def _plain_text(payload: bytes | None, extracted: str | None) -> dict:
    text = ""
    if payload:
        text = payload.decode("utf-8", errors="replace")
    elif extracted:
        text = extracted
    return {
        "previewKind": "text",
        "previewNote": "Read-only text preview.",
        "text": text[:20_000],
        "hasMore": len(text) > 20_000,
    }


def _image_jpeg(payload: bytes, dest: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        image = Image.open(io.BytesIO(payload))
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, format="JPEG", quality=82)
        return True
    except Exception:
        return False


def _build_formatted(
    ext: str,
    payload: bytes,
    extracted: str | None,
    *,
    sheet: int,
    page: int,
    slide: int,
    row_offset: int,
    media_path: Path,
) -> dict | None:
    if ext in {"xlsx", "xlsm"}:
        return _excel_grid(payload, sheet=sheet, row_offset=row_offset)
    if ext == "docx":
        return _word_blocks(payload)
    if ext == "pdf":
        return _pdf_page(payload, page)
    if ext in {"pptx", "ppt"}:
        if ext == "ppt":
            return {"error": "Legacy PowerPoint (.ppt) formatted preview is not available."}
        return _pptx_slides(payload, slide)
    if ext == "csv":
        return _csv_table(payload, extracted)
    if ext in {"txt", "md", "log", "json", "xml"}:
        return _plain_text(payload, extracted)
    if ext in IMAGE_EXTS:
        if _image_jpeg(payload, media_path):
            return {
                "previewKind": "image",
                "previewNote": "Derived image preview. This is not an original-file download.",
                "mediaAvailable": True,
            }
        return {"error": "Image preview could not be generated."}
    if ext in {"xls", "doc"}:
        return {"error": "Legacy Office formatted preview is not available."}
    return None


def _authorize_preview(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
) -> tuple[Any, SynologyFileInventory, Any, bool] | dict:
    access = evaluate_user(session, caller_id)
    if not access.allowed:
        return _denied(document_id, deny_message(access.reason), kind="denied")
    user = load_active_user(session, caller_id)
    if user is None:
        return _denied(document_id, deny_message("no_verified_email"), kind="denied")
    try:
        inventory_id = uuid.UUID(document_id) if "-" in document_id else uuid.UUID(hex=document_id)
    except ValueError:
        return _denied(document_id, "Document not found.")
    inventory = session.get(SynologyFileInventory, inventory_id)
    if inventory is None or not inventory.is_present or inventory.is_directory:
        return _denied(document_id, "Document not found.")
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory_id)
    ).scalar_one_or_none()
    view = _content_view(inventory, content)
    if not authorize_document(
        session,
        settings,
        user,
        inventory,
        view,
        capability=Capability.VIEW,
        require_extracted=False,
    ):
        return _denied(document_id, "Access denied.", kind="denied")
    can_download = authorize_document(
        session,
        settings,
        user,
        inventory,
        view,
        capability=Capability.DOWNLOAD,
        require_extracted=False,
    )
    return user, inventory, view, can_download


def _data_view_preview(
    session: Session,
    settings: Settings,
    inventory: SynologyFileInventory,
    view: Any,
    can_download: bool,
    gateway: SmbGateway | None,
    *,
    sheet: int,
    page: int,
    slide: int,
    row_offset: int,
) -> dict:
    ext = (inventory.extension or "").lower().lstrip(".")
    version = _version_key(inventory, view)
    cache_name = f"data-{ext}-{sheet}-{page}-{slide}-{row_offset}.json"
    cached = _read_json(_cache_path(settings, inventory.id, version, cache_name))
    if cached and cached.get("previewKind"):
        cached["canDownload"] = can_download
        cached["id"] = inventory.id.hex
        cached["denied"] = False
        cached["viewMode"] = "data"
        cached["originalAvailable"] = False
        return cached

    payload = _read_source_bytes(session, settings, gateway, inventory)
    media_path = _cache_path(settings, inventory.id, version, "preview.jpg")
    built: dict | None = None
    if payload:
        try:
            built = _build_formatted(
                ext,
                payload,
                getattr(view, "extracted_text", None),
                sheet=sheet,
                page=page,
                slide=slide,
                row_offset=row_offset,
                media_path=media_path,
            )
        except Exception:
            log.info("preview.generate_failed id=%s ext=%s", inventory.id, ext)
            built = {"error": "Preview generation failed."}

    if built and not built.get("error"):
        result = _base_payload(inventory, view, can_download=can_download)
        result.update(built)
        result["viewMode"] = "data"
        result["dataView"] = True
        _write_json(
            _cache_path(settings, inventory.id, version, cache_name),
            {k: v for k, v in result.items() if k != "canDownload"},
        )
        result["canDownload"] = can_download
        return result

    note = None
    if built and built.get("error"):
        note = f"{built['error']} Indexed text view — original formatting not available."
    elif payload is None:
        note = "Indexed text view — original formatting not available."
    fallback = _indexed_fallback(inventory, view, can_download, note)
    fallback["viewMode"] = "data"
    fallback["dataView"] = True
    return fallback


def _original_view_preview(
    session: Session,
    settings: Settings,
    inventory: SynologyFileInventory,
    view: Any,
    can_download: bool,
    gateway: SmbGateway | None,
    *,
    page: int,
) -> dict:
    ext = (inventory.extension or "").lower().lstrip(".")
    version = _version_key(inventory, view)
    lo = libreoffice_status(settings)
    supports_original = ext in OFFICE_EXTS or ext in PDF_EXTS or ext in IMAGE_EXTS
    result = _base_payload(inventory, view, can_download=can_download)
    result.update(
        {
            "viewMode": "original",
            "dataViewAvailable": True,
            "engineStatus": lo,
            "supportsOriginalFormat": supports_original,
        }
    )
    if not supports_original:
        result.update(
            {
                "previewKind": "original_unavailable",
                "previewAvailable": False,
                "previewNote": "Original-format preview is not supported for this file type. Use Data/Text View.",
            }
        )
        return result

    if ext in OFFICE_EXTS and not lo["available"]:
        result.update(
            {
                "previewKind": "original_unavailable",
                "previewAvailable": False,
                "previewNote": (
                    "Preview Engine Unavailable — LibreOffice is required for Office original-format preview. "
                    "Install LibreOffice and set QKA_LIBREOFFICE_PATH, then retry. Data/Text View remains available."
                ),
                "error": lo.get("installHint"),
            }
        )
        return result

    payload = _read_source_bytes(session, settings, gateway, inventory)
    if payload is None:
        result.update(
            {
                "previewKind": "original_unavailable",
                "previewAvailable": False,
                "previewNote": "Original file could not be read from Synology for rendering. Data/Text View may still work.",
            }
        )
        return result

    rendered = ensure_original_render(
        settings,
        inventory_id=inventory.id,
        version=version,
        extension=ext,
        source_bytes=payload,
    )
    status = rendered.get("status")
    if status != "ready":
        note = rendered.get("error") or "Original-format preview could not be generated."
        result.update(
            {
                "previewKind": "original_unavailable",
                "previewAvailable": False,
                "previewNote": f"{note} Indexed text / Data View remains available when present.",
                "renderStatus": status,
            }
        )
        return result

    page_count = int(rendered.get("pageCount") or 0)
    current = max(1, min(int(page or 1), max(page_count, 1)))
    result.update(
        {
            "previewKind": "original_pages",
            "previewAvailable": True,
            "previewNote": (
                "Original-format read-only preview (LibreOffice/PDF page rendering). "
                "Layout may differ slightly from Microsoft Office."
                if rendered.get("engine", "").startswith("libreoffice")
                else "Original-format read-only page preview."
            ),
            "page": current,
            "pageCount": page_count,
            "engine": rendered.get("engine"),
            "renderStatus": "ready",
            "pageUrl": f"/api/v1/documents/{inventory.id.hex}/preview/pages/{current}",
        }
    )
    return result


def preview_document(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
    gateway: SmbGateway | None = None,
    *,
    sheet: int = 0,
    page: int = 1,
    slide: int = 0,
    row_offset: int = 0,
    view_mode: str = "original",
) -> dict:
    auth = _authorize_preview(session, settings, caller_id, document_id)
    if isinstance(auth, dict):
        return auth
    _user, inventory, view, can_download = auth
    mode = (view_mode or "original").strip().lower()
    if mode in {"data", "grid", "text", "indexed"}:
        return _data_view_preview(
            session,
            settings,
            inventory,
            view,
            can_download,
            gateway,
            sheet=sheet,
            page=page,
            slide=slide,
            row_offset=row_offset,
        )
    return _original_view_preview(
        session,
        settings,
        inventory,
        view,
        can_download,
        gateway,
        page=page,
    )


def preview_media(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
    gateway: SmbGateway | None = None,
) -> tuple[bytes | None, str, dict]:
    body = preview_document(session, settings, caller_id, document_id, gateway, view_mode="data")
    if body.get("denied") or body.get("previewKind") != "image":
        return None, "application/json", body
    try:
        inventory_id = uuid.UUID(document_id) if "-" in document_id else uuid.UUID(hex=document_id)
    except ValueError:
        return None, "application/json", _denied(document_id, "Document not found.")
    inventory = session.get(SynologyFileInventory, inventory_id)
    content = session.execute(
        select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory_id)
    ).scalar_one_or_none()
    if inventory is None:
        return None, "application/json", _denied(document_id, "Document not found.")
    version = _version_key(inventory, _content_view(inventory, content))
    path = _cache_path(settings, inventory.id, version, "preview.jpg")
    if not path.is_file():
        return None, "application/json", _denied(document_id, "Image preview is not available.")
    return path.read_bytes(), "image/jpeg", {}


def preview_page_image(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
    page: int,
    gateway: SmbGateway | None = None,
) -> tuple[bytes | None, str, dict]:
    """Authorize and return a single rendered original-format page JPEG."""
    auth = _authorize_preview(session, settings, caller_id, document_id)
    if isinstance(auth, dict):
        return None, "application/json", auth
    _user, inventory, view, _can_download = auth
    version = _version_key(inventory, view)
    payload = read_page_bytes(settings, inventory.id, version, page)
    if payload is None:
        # Attempt on-demand generation if cache miss.
        body = preview_document(
            session,
            settings,
            caller_id,
            document_id,
            gateway,
            page=page,
            view_mode="original",
        )
        if body.get("previewKind") != "original_pages":
            return None, "application/json", body
        payload = read_page_bytes(settings, inventory.id, version, page)
    if payload is None:
        return None, "application/json", _denied(document_id, "Preview page is not available.")
    return payload, "image/jpeg", {}


def preview_engine_status(settings: Settings) -> dict:
    return {
        "libreOffice": libreoffice_status(settings),
        "pdfRenderer": "pymupdf",
        "officeConversion": "libreoffice-headless",
        "cacheDirConfigured": bool((settings.preview_cache_dir or "").strip()),
        "maxPages": int(getattr(settings, "preview_max_pages", 100) or 100),
    }
