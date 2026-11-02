from __future__ import annotations

import hashlib
import io

EXTRACT_PENDING = 0
EXTRACT_SUCCEEDED = 1
EXTRACT_FAILED = 2
EXTRACT_UNSUPPORTED = 3

SUPPORTED = {"txt", "csv", "md", "log", "docx", "xlsx", "xlsm", "pdf"}
POWERPOINT = {"ppt", "pptx"}


def is_supported(extension: str) -> bool:
    return (extension or "").lower().lstrip(".") in SUPPORTED


def extract_bytes(extension: str, payload: bytes, max_chars: int) -> tuple[int, str, str | None, str]:
    ext = (extension or "").lower().lstrip(".")
    digest = hashlib.sha256(payload).hexdigest()
    if ext in POWERPOINT:
        return EXTRACT_UNSUPPORTED, "", "PowerPoint extraction is not enabled yet.", digest
    if ext not in SUPPORTED:
        return EXTRACT_UNSUPPORTED, "", "File type is not supported for extraction.", digest
    try:
        text = _extract(ext, payload)
    except Exception:
        return EXTRACT_FAILED, "", "Text extraction failed.", digest
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[:max_chars]
    return EXTRACT_SUCCEEDED, text, None, digest


def _extract(ext: str, payload: bytes) -> str:
    if ext in {"txt", "csv", "md", "log"}:
        return payload.decode("utf-8", errors="replace")
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ext == "docx":
        from docx import Document

        document = Document(io.BytesIO(payload))
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text)
    if ext in {"xlsx", "xlsm"}:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in workbook.worksheets:
            lines.append(sheet.title)
            for row in sheet.iter_rows(values_only=True):
                values = ["" if cell is None else str(cell) for cell in row]
                if any(value.strip() for value in values):
                    lines.append("\t".join(values))
        workbook.close()
        return "\n".join(lines)
    raise ValueError(ext)
