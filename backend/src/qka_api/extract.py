"""Document text extraction with ordered structural chunks and optional OCR.

Never executes macros, formulas, embedded scripts, or external workbook links.
Spreadsheet cells use cached formula values (data_only); missing caches are marked.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EXTRACT_PENDING = 0
EXTRACT_SUCCEEDED = 1
EXTRACT_FAILED = 2
EXTRACT_UNSUPPORTED = 3
EXTRACT_SKIPPED = 4

# Text / office / images that ordinary extraction may attempt.
TEXT_EXTS = {"txt", "csv", "md", "log"}
DOCX_EXTS = {"docx"}
XLSX_EXTS = {"xlsx", "xlsm"}
PDF_EXTS = {"pdf"}
PPTX_EXTS = {"pptx"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff"}
LEGACY_OFFICE = {"doc", "xls", "ppt"}

SUPPORTED = TEXT_EXTS | DOCX_EXTS | XLSX_EXTS | PDF_EXTS | PPTX_EXTS | IMAGE_EXTS | LEGACY_OFFICE
# Bump when extractor support/behavior changes so sync can reprocess unchanged files.
EXTRACTOR_VERSION = 2

_NATIVE_TEXT_MIN_CHARS = 40
log = logging.getLogger("qka.extract")


@dataclass
class ExtractChunk:
    ordinal: int
    text: str
    method: str  # native | ocr | converted
    page: int | None = None
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    slide: int | None = None
    ref: str | None = None


@dataclass
class ExtractionResult:
    status: int
    text: str
    chunks: list[ExtractChunk] = field(default_factory=list)
    error: str | None = None
    digest: str = ""
    method: str = "none"
    ocr_used: bool = False
    truncated: bool = False
    incomplete: bool = False
    notes: list[str] = field(default_factory=list)

    def chunks_json(self) -> str:
        return json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False)


def is_supported(extension: str) -> bool:
    return (extension or "").lower().lstrip(".") in SUPPORTED


def is_extractable(extension: str) -> bool:
    """True when extraction should be attempted (including OCR-capable images)."""
    return is_supported(extension)


def extract_bytes(
    extension: str,
    payload: bytes,
    max_chars: int,
    *,
    settings: Any | None = None,
    max_pages: int | None = None,
) -> tuple[int, str, str | None, str]:
    """Backward-compatible tuple API used by older call sites and tests."""
    result = extract_document(extension, payload, max_chars, settings=settings, max_pages=max_pages)
    return result.status, result.text, result.error, result.digest


def extract_document(
    extension: str,
    payload: bytes,
    max_chars: int,
    *,
    settings: Any | None = None,
    max_pages: int | None = None,
) -> ExtractionResult:
    ext = (extension or "").lower().lstrip(".")
    digest = hashlib.sha256(payload).hexdigest()
    if ext not in SUPPORTED:
        return ExtractionResult(
            status=EXTRACT_UNSUPPORTED,
            text="",
            error="File type is not supported for extraction.",
            digest=digest,
            method="none",
        )
    try:
        if ext in LEGACY_OFFICE:
            result = _extract_legacy_office(ext, payload, max_chars, settings=settings, max_pages=max_pages)
        elif ext in TEXT_EXTS:
            result = _extract_text(payload, max_chars)
        elif ext in DOCX_EXTS:
            result = _extract_docx(payload, max_chars)
        elif ext in XLSX_EXTS:
            result = _extract_xlsx(payload, max_chars)
        elif ext in PDF_EXTS:
            result = _extract_pdf(payload, max_chars, settings=settings, max_pages=max_pages)
        elif ext in PPTX_EXTS:
            result = _extract_pptx(payload, max_chars)
        elif ext in IMAGE_EXTS:
            result = _extract_image(payload, max_chars, settings=settings)
        else:
            return ExtractionResult(
                status=EXTRACT_UNSUPPORTED,
                text="",
                error="File type is not supported for extraction.",
                digest=digest,
                method="none",
            )
    except Exception as exc:
        log.exception("extract.failed ext=%s", ext)
        return ExtractionResult(
            status=EXTRACT_FAILED,
            text="",
            error=_safe_error(exc),
            digest=digest,
            method="none",
        )
    result.digest = digest
    return result


def _safe_error(exc: BaseException) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    msg = re.sub(r"[A-Za-z]:\\[^\s]+", "[path]", msg)
    return msg[:500] or "Text extraction failed."


def _truncate(result: ExtractionResult, max_chars: int) -> ExtractionResult:
    if max_chars <= 0:
        return result
    if len(result.text) <= max_chars:
        return result
    kept: list[ExtractChunk] = []
    used = 0
    for chunk in result.chunks:
        if used >= max_chars:
            break
        room = max_chars - used
        if len(chunk.text) <= room:
            kept.append(chunk)
            used += len(chunk.text) + 1
        else:
            kept.append(
                ExtractChunk(
                    ordinal=chunk.ordinal,
                    text=chunk.text[:room],
                    method=chunk.method,
                    page=chunk.page,
                    sheet=chunk.sheet,
                    row=chunk.row,
                    column=chunk.column,
                    slide=chunk.slide,
                    ref=chunk.ref,
                )
            )
            used = max_chars
            break
    result.chunks = kept
    result.text = "\n".join(c.text for c in kept if c.text)
    result.truncated = True
    result.incomplete = True
    result.notes.append(f"Extracted text truncated to {max_chars} characters.")
    return result


def _finalize(
    chunks: list[ExtractChunk],
    *,
    max_chars: int,
    method: str,
    ocr_used: bool = False,
    notes: list[str] | None = None,
    status: int = EXTRACT_SUCCEEDED,
    error: str | None = None,
) -> ExtractionResult:
    for index, chunk in enumerate(chunks):
        chunk.ordinal = index
    text = "\n".join(c.text for c in chunks if c.text)
    result = ExtractionResult(
        status=status if (text.strip() or status != EXTRACT_SUCCEEDED) else EXTRACT_FAILED,
        text=text,
        chunks=chunks,
        error=error if (text.strip() or status != EXTRACT_SUCCEEDED) else (error or "No extractable text found."),
        method=method,
        ocr_used=ocr_used,
        notes=list(notes or []),
    )
    if result.status == EXTRACT_SUCCEEDED and not text.strip():
        result.status = EXTRACT_FAILED
        result.error = result.error or "No extractable text found."
    return _truncate(result, max_chars)


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _extract_text(payload: bytes, max_chars: int) -> ExtractionResult:
    body = _decode_text(payload)
    chunk = ExtractChunk(ordinal=0, text=body, method="native", ref="document")
    return _finalize([chunk], max_chars=max_chars, method="native")


def _iter_docx_blocks(document: Any):
    """Yield paragraphs and tables in approximate document order."""
    body = document.element.body
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body.iterchildren():
        tag = child.tag.lower()
        if tag.endswith("}p"):
            yield Paragraph(child, document)
        elif tag.endswith("}tbl"):
            yield Table(child, document)


def _extract_docx(payload: bytes, max_chars: int) -> ExtractionResult:
    from docx import Document

    document = Document(io.BytesIO(payload))
    chunks: list[ExtractChunk] = []
    for block in _iter_docx_blocks(document):
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        if isinstance(block, Paragraph):
            text = (block.text or "").rstrip()
            if text:
                chunks.append(ExtractChunk(ordinal=0, text=text, method="native", ref="paragraph"))
        elif isinstance(block, Table):
            lines: list[str] = []
            for r_idx, row in enumerate(block.rows, start=1):
                cells = [(cell.text or "").replace("\n", " ").strip() for cell in row.cells]
                if any(cells):
                    lines.append(f"R{r_idx}: " + " | ".join(cells))
            if lines:
                chunks.append(
                    ExtractChunk(
                        ordinal=0,
                        text="\n".join(lines),
                        method="native",
                        ref="table",
                    )
                )
    return _finalize(chunks, max_chars=max_chars, method="native")


def _col_letter(index: int) -> str:
    # 1-based spreadsheet column index -> letters
    n = index
    letters = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters)) or "A"


def _extract_xlsx(payload: bytes, max_chars: int) -> ExtractionResult:
    from openpyxl import load_workbook

    # data_only=True uses cached formula values; never evaluates formulas.
    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    chunks: list[ExtractChunk] = []
    notes: list[str] = []
    missing_cache = 0
    try:
        for sheet in workbook.worksheets:
            chunks.append(
                ExtractChunk(
                    ordinal=0,
                    text=f"[Sheet: {sheet.title}]",
                    method="native",
                    sheet=sheet.title,
                    ref=f"sheet:{sheet.title}",
                )
            )
            for row in sheet.iter_rows():
                cells_out: list[str] = []
                row_num = None
                for cell in row:
                    # read_only mode may yield EmptyCell without row/column attrs.
                    row_num = getattr(cell, "row", None) or row_num
                    col_idx = getattr(cell, "column", None)
                    value = getattr(cell, "value", None)
                    if value is None or col_idx is None or row_num is None:
                        continue
                    col = _col_letter(int(col_idx))
                    cells_out.append(f"{col}{row_num}={value}")
                if cells_out and row_num is not None:
                    chunks.append(
                        ExtractChunk(
                            ordinal=0,
                            text="\t".join(cells_out),
                            method="native",
                            sheet=sheet.title,
                            row=int(row_num),
                            ref=f"{sheet.title}!R{row_num}",
                        )
                    )
        # Second pass without data_only to detect formula cells lacking cache.
        wb_formulas = load_workbook(io.BytesIO(payload), read_only=True, data_only=False)
        try:
            for sheet in wb_formulas.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            missing_cache += 1
        finally:
            wb_formulas.close()
        if missing_cache:
            notes.append(
                f"{missing_cache} formula cell(s) detected; cached values used when present. "
                "Uncached formula results are unavailable (formulas are never executed)."
            )
    finally:
        workbook.close()
    return _finalize(chunks, max_chars=max_chars, method="native", notes=notes)


def _pdf_page_native_text(page: Any) -> str:
    try:
        return (page.get_text("text") or "").strip()
    except Exception:
        return ""


def _extract_pdf(
    payload: bytes,
    max_chars: int,
    *,
    settings: Any | None,
    max_pages: int | None,
) -> ExtractionResult:
    import pymupdf

    doc = pymupdf.open(stream=payload, filetype="pdf")
    chunks: list[ExtractChunk] = []
    notes: list[str] = []
    ocr_used = False
    method = "native"
    page_limit = max_pages
    if page_limit is None and settings is not None:
        page_limit = int(getattr(settings, "extraction_max_pages", 100) or 100)
    if page_limit is None:
        page_limit = 100
    try:
        total = len(doc)
        limit = min(total, max(1, page_limit))
        if total > limit:
            notes.append(f"PDF truncated to first {limit} of {total} pages.")
        for index in range(limit):
            page = doc[index]
            page_no = index + 1
            native = _pdf_page_native_text(page)
            if len(native) >= _NATIVE_TEXT_MIN_CHARS:
                chunks.append(
                    ExtractChunk(
                        ordinal=0,
                        text=native,
                        method="native",
                        page=page_no,
                        ref=f"page:{page_no}",
                    )
                )
                continue
            # Absent/unusable native text → OCR when available.
            if settings is None:
                if native:
                    chunks.append(
                        ExtractChunk(
                            ordinal=0,
                            text=native,
                            method="native",
                            page=page_no,
                            ref=f"page:{page_no}",
                        )
                    )
                notes.append(f"Page {page_no}: native text thin; OCR skipped (no settings).")
                continue
            from qka_api.ocr_engine import ocr_image_bytes, ocr_status

            status = ocr_status(settings)
            if not status["available"]:
                if native:
                    chunks.append(
                        ExtractChunk(
                            ordinal=0,
                            text=native,
                            method="native",
                            page=page_no,
                            ref=f"page:{page_no}",
                        )
                    )
                notes.append(f"Page {page_no}: OCR unavailable — {status.get('installHint')}")
                continue
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            png_bytes = pix.tobytes("png")
            text, meta = ocr_image_bytes(settings, png_bytes)
            text = (text or "").strip()
            if text:
                ocr_used = True
                method = "mixed" if chunks else "ocr"
                chunks.append(
                    ExtractChunk(
                        ordinal=0,
                        text=text,
                        method="ocr",
                        page=page_no,
                        ref=f"page:{page_no}:ocr",
                    )
                )
                if meta.get("note"):
                    notes.append(str(meta["note"]))
            elif native:
                chunks.append(
                    ExtractChunk(
                        ordinal=0,
                        text=native,
                        method="native",
                        page=page_no,
                        ref=f"page:{page_no}",
                    )
                )
    finally:
        doc.close()
    if ocr_used and any(c.method == "native" for c in chunks):
        method = "mixed"
    elif ocr_used:
        method = "ocr"
    return _finalize(chunks, max_chars=max_chars, method=method, ocr_used=ocr_used, notes=notes)


def _extract_pptx(payload: bytes, max_chars: int) -> ExtractionResult:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(io.BytesIO(payload))
    chunks: list[ExtractChunk] = []
    for slide_idx, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = (shape.text or "").strip()
                if text:
                    parts.append(text)
            if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
                table = shape.table
                for r_idx, row in enumerate(table.rows, start=1):
                    cells = [(cell.text or "").replace("\n", " ").strip() for cell in row.cells]
                    if any(cells):
                        parts.append(f"R{r_idx}: " + " | ".join(cells))
        if parts:
            chunks.append(
                ExtractChunk(
                    ordinal=0,
                    text="\n".join(parts),
                    method="native",
                    slide=slide_idx,
                    ref=f"slide:{slide_idx}",
                )
            )
    return _finalize(chunks, max_chars=max_chars, method="native")


def _extract_image(payload: bytes, max_chars: int, *, settings: Any | None) -> ExtractionResult:
    if settings is None:
        return ExtractionResult(
            status=EXTRACT_FAILED,
            text="",
            error="OCR settings are required for image extraction.",
            method="ocr",
            ocr_used=False,
        )
    from qka_api.ocr_engine import ocr_image_bytes, ocr_status

    status = ocr_status(settings)
    if not status["available"]:
        return ExtractionResult(
            status=EXTRACT_FAILED,
            text="",
            error=status.get("installHint") or "Tesseract OCR is not available.",
            method="ocr",
            ocr_used=False,
        )
    text, meta = ocr_image_bytes(settings, payload)
    notes = [str(meta["note"])] if meta.get("note") else []
    chunk = ExtractChunk(ordinal=0, text=(text or "").strip(), method="ocr", ref="image:ocr")
    return _finalize([chunk], max_chars=max_chars, method="ocr", ocr_used=True, notes=notes)


def _extract_legacy_office(
    ext: str,
    payload: bytes,
    max_chars: int,
    *,
    settings: Any | None,
    max_pages: int | None,
) -> ExtractionResult:
    if settings is None:
        return ExtractionResult(
            status=EXTRACT_UNSUPPORTED,
            text="",
            error="Legacy Office extraction requires LibreOffice conversion settings.",
            method="none",
        )
    from qka_api.services.preview_engine import find_soffice

    if find_soffice(settings) is None:
        return ExtractionResult(
            status=EXTRACT_UNSUPPORTED,
            text="",
            error=(
                f"Legacy .{ext} requires LibreOffice conversion. "
                "Install LibreOffice and set QKA_LIBREOFFICE_PATH, then reindex."
            ),
            method="none",
        )
    tmp_root = Path(tempfile.mkdtemp(prefix="qka-legacy-extract-"))
    try:
        source = tmp_root / f"source.{ext}"
        source.write_bytes(payload)
        from qka_api.services.preview_engine import _convert_office_to_pdf

        pdf_path = _convert_office_to_pdf(settings, source, tmp_root)
        pdf_bytes = pdf_path.read_bytes()
        result = _extract_pdf(pdf_bytes, max_chars, settings=settings, max_pages=max_pages)
        result.method = "converted+ocr" if result.ocr_used else "converted"
        result.notes.append(f"Legacy .{ext} converted via LibreOffice to PDF for extraction.")
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
