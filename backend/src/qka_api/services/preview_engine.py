from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qka_shared.config import Settings

log = logging.getLogger("qka.preview.engine")

OFFICE_EXTS = {"xlsx", "xls", "xlsm", "xlsb", "docx", "doc", "pptx", "ppt", "odt", "ods", "odp"}
PDF_EXTS = {"pdf"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"}

_CONVERT_SEM = threading.Semaphore(1)
_DEFAULT_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    r"C:\Program Files\LibreOffice\program\soffice.com",
)


def _cache_root(settings: Settings) -> Path:
    raw = (settings.preview_cache_dir or "").strip()
    if raw:
        root = Path(raw)
    else:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        root = Path(base) / "QKA-Next" / "preview-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def find_soffice(settings: Settings) -> Path | None:
    configured = (getattr(settings, "libreoffice_path", None) or "").strip()
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(_DEFAULT_SOFFICE_CANDIDATES)
    path_env = (os.environ.get("PATH") or "").split(os.pathsep)
    for folder in path_env:
        if not folder:
            continue
        candidates.append(str(Path(folder) / "soffice.exe"))
        candidates.append(str(Path(folder) / "soffice.com"))
        candidates.append(str(Path(folder) / "soffice"))
    seen: set[str] = set()
    for raw in candidates:
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        path = Path(raw)
        if path.is_file():
            return path
    return None


def libreoffice_status(settings: Settings) -> dict[str, Any]:
    path = find_soffice(settings)
    return {
        "available": path is not None,
        "path": str(path) if path else None,
        "requiredFor": sorted(OFFICE_EXTS),
        "installHint": (
            "Install LibreOffice and set QKA_LIBREOFFICE_PATH to soffice.exe "
            "(example: C:\\Program Files\\LibreOffice\\program\\soffice.exe)."
            if path is None
            else None
        ),
    }


def document_dir(settings: Settings, inventory_id: uuid.UUID, version: str) -> Path:
    path = _cache_root(settings) / str(inventory_id) / version
    path.mkdir(parents=True, exist_ok=True)
    return path


def _meta_path(folder: Path) -> Path:
    return folder / "original_meta.json"


def read_meta(folder: Path) -> dict[str, Any] | None:
    path = _meta_path(folder)
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_meta(folder: Path, payload: dict[str, Any]) -> None:
    path = _meta_path(folder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def page_path(folder: Path, page: int) -> Path:
    return folder / "pages" / f"page-{int(page):04d}.jpg"


def _file_uri(path: Path) -> str:
    # LibreOffice UserInstallation requires a file URI.
    return "file:///" + quote(str(path.resolve()).replace("\\", "/"))


def _convert_office_to_pdf(settings: Settings, source_path: Path, out_dir: Path) -> Path:
    soffice = find_soffice(settings)
    if soffice is None:
        raise RuntimeError("LibreOffice is not installed or QKA_LIBREOFFICE_PATH is not configured.")
    profile = out_dir / f"lo-profile-{uuid.uuid4().hex}"
    profile.mkdir(parents=True, exist_ok=True)
    timeout = int(getattr(settings, "libreoffice_timeout_seconds", 120) or 120)
    cmd = [
        str(soffice),
        "--headless",
        "--nologo",
        "--nolockcheck",
        "--nodefault",
        "--norestore",
        "--nofirststartwizard",
        f"-env:UserInstallation={_file_uri(profile)}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(out_dir),
        str(source_path),
    ]
    # Prefer generic convert-to pdf for spreadsheets/presentations too.
    cmd[cmd.index("pdf:writer_pdf_Export")] = "pdf"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(out_dir),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"LibreOffice conversion timed out after {timeout}s.") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdfs = sorted(out_dir.glob("*.pdf"))
    if completed.returncode != 0 or not pdfs:
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        raise RuntimeError(detail or "LibreOffice conversion failed.")
    log.info(
        "preview.libreoffice_ok seconds=%.1f pdf=%s",
        time.monotonic() - started,
        pdfs[0].name,
    )
    return pdfs[0]


def _render_pdf_pages(pdf_path: Path, pages_dir: Path, *, max_pages: int, dpi: float = 120.0) -> int:
    import fitz

    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        total = min(len(doc), max(1, int(max_pages)))
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for index in range(total):
            dest = pages_dir / f"page-{index + 1:04d}.jpg"
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            page = doc.load_page(index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pix.save(str(dest), output="jpeg")
        return total
    finally:
        doc.close()


def ensure_original_render(
    settings: Settings,
    *,
    inventory_id: uuid.UUID,
    version: str,
    extension: str,
    source_bytes: bytes,
) -> dict[str, Any]:
    """Convert/render original-format pages into the protected preview cache."""
    ext = (extension or "").lower().lstrip(".")
    folder = document_dir(settings, inventory_id, version)
    existing = read_meta(folder)
    if existing and existing.get("status") == "ready" and int(existing.get("pageCount") or 0) > 0:
        # Verify first page still exists.
        if page_path(folder, 1).is_file():
            return existing

    max_pages = int(getattr(settings, "preview_max_pages", 100) or 100)
    pages_dir = folder / "pages"
    pdf_path = folder / "original.pdf"
    meta: dict[str, Any] = {
        "status": "generating",
        "engine": None,
        "pageCount": 0,
        "extension": ext,
        "error": None,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    write_meta(folder, meta)

    try:
        if ext in IMAGE_EXTS:
            from PIL import Image
            import io

            pages_dir.mkdir(parents=True, exist_ok=True)
            image = Image.open(io.BytesIO(source_bytes))
            image = image.convert("RGB")
            image.thumbnail((1800, 1800))
            dest = page_path(folder, 1)
            image.save(dest, format="JPEG", quality=85)
            meta.update({"status": "ready", "engine": "pillow", "pageCount": 1, "error": None})
            write_meta(folder, meta)
            return meta

        if ext in PDF_EXTS:
            pdf_path.write_bytes(source_bytes)
            count = _render_pdf_pages(pdf_path, pages_dir, max_pages=max_pages)
            meta.update({"status": "ready", "engine": "pymupdf", "pageCount": count, "error": None})
            write_meta(folder, meta)
            return meta

        if ext in OFFICE_EXTS:
            if find_soffice(settings) is None:
                meta.update(
                    {
                        "status": "engine_unavailable",
                        "engine": None,
                        "error": libreoffice_status(settings)["installHint"],
                    }
                )
                write_meta(folder, meta)
                return meta
            work = folder / "lo-work"
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)
            source_path = work / f"source.{ext}"
            source_path.write_bytes(source_bytes)
            with _CONVERT_SEM:
                converted = _convert_office_to_pdf(settings, source_path, work)
                shutil.copyfile(converted, pdf_path)
            count = _render_pdf_pages(pdf_path, pages_dir, max_pages=max_pages)
            shutil.rmtree(work, ignore_errors=True)
            meta.update({"status": "ready", "engine": "libreoffice+pymupdf", "pageCount": count, "error": None})
            write_meta(folder, meta)
            return meta

        meta.update({"status": "unsupported", "error": f"Original-format preview is not supported for .{ext}."})
        write_meta(folder, meta)
        return meta
    except Exception as exc:
        log.info("preview.original_failed id=%s ext=%s err=%s", inventory_id, ext, exc)
        meta.update({"status": "failed", "error": str(exc)[:500]})
        write_meta(folder, meta)
        return meta


def read_page_bytes(settings: Settings, inventory_id: uuid.UUID, version: str, page: int) -> bytes | None:
    folder = document_dir(settings, inventory_id, version)
    path = page_path(folder, page)
    if not path.is_file():
        return None
    return path.read_bytes()
