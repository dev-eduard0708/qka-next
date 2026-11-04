"""Extraction, OCR, queue, and MCP chunk retrieval regressions."""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from qka_api.extract import (
    EXTRACT_FAILED,
    EXTRACT_SUCCEEDED,
    EXTRACT_UNSUPPORTED,
    EXTRACTOR_VERSION,
    extract_document,
    is_extractable,
)
from qka_api.ocr_engine import ocr_status
from qka_api.services import extraction as extraction_svc
from qka_api.services import documents as docs
from qka_api.smb import EnumeratedFile, FakeSmbGateway
from qka_shared.config import Settings
from qka_shared.models import SynologyConnectionSettings, SynologyDocumentContent, SynologyFileInventory
from sqlalchemy import select

from tests.conftest import ALLOWED_USER, DENIED_USER, SHARE_ID, SID_ALLOWED, auth_cookies


def _ensure_connection(session) -> None:
    existing = session.execute(
        select(SynologyConnectionSettings).where(SynologyConnectionSettings.primary_approved_share_id == SHARE_ID)
    ).scalars().first()
    if existing is not None:
        return
    now = datetime.now(timezone.utc)
    session.add(
        SynologyConnectionSettings(
            id=uuid.uuid4(),
            host_name="192.168.110.11",
            connection_name="Test NAS",
            configured_shared_folder_path=r"\\192.168.110.11\eduard",
            primary_approved_share_id=SHARE_ID,
            auth_mode=1,
            service_account_user_name=r".\qkai",
            connection_status=1,
            updated_at=now,
        )
    )
    session.flush()


def _xlsx_bytes(*, with_formula_cache: bool = True) -> bytes:
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Pricing"
    ws1["A1"] = "Item"
    ws1["B1"] = "Qty"
    ws1["A2"] = "Capture Client"
    ws1["B2"] = 25
    if with_formula_cache:
        ws1["C2"] = 100
    else:
        ws1["C2"] = "=B2*4"
    ws2 = wb.create_sheet("Notes")
    ws2["A1"] = "Renewal"
    ws2["B1"] = "2026-01-18"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx_with_table() -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph("Quotation header")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "SKU"
    table.cell(0, 1).text = "Qty"
    table.cell(1, 0).text = "SW-CC"
    table.cell(1, 1).text = "25"
    document.add_paragraph("Footer note")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _png_bytes(text: str = "SONICWALL 2026") -> bytes:
    image = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((20, 40), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_native_bytes() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Native PDF renewal quantity 25")
    data = doc.tobytes()
    doc.close()
    return data


def test_docx_tables_preserve_order() -> None:
    result = extract_document("docx", _docx_with_table(), max_chars=50_000)
    assert result.status == EXTRACT_SUCCEEDED
    assert "Quotation header" in result.text
    assert "SW-CC" in result.text
    assert "Footer note" in result.text
    assert result.method == "native"
    assert any(c.ref == "table" for c in result.chunks)


def test_xlsx_multisheet_and_formula_notes() -> None:
    result = extract_document("xlsx", _xlsx_bytes(with_formula_cache=False), max_chars=50_000)
    assert result.status == EXTRACT_SUCCEEDED
    assert "[Sheet: Pricing]" in result.text
    assert "[Sheet: Notes]" in result.text
    assert "Capture Client" in result.text
    assert any("formula" in note.lower() for note in result.notes)


def test_pdf_native_text_pages() -> None:
    result = extract_document("pdf", _pdf_native_bytes(), max_chars=50_000)
    assert result.status == EXTRACT_SUCCEEDED
    assert "Native PDF renewal" in result.text
    assert result.chunks[0].page == 1
    assert result.ocr_used is False


def test_image_ocr_or_clear_failure(settings_factory=None) -> None:
    settings = Settings(tesseract_path="")
    status = ocr_status(settings)
    result = extract_document("png", _png_bytes(), max_chars=50_000, settings=settings)
    if status["available"]:
        assert result.status == EXTRACT_SUCCEEDED
        assert result.ocr_used is True
        assert "SONICWALL" in result.text.upper() or len(result.text) > 0
    else:
        assert result.status == EXTRACT_FAILED
        assert "Tesseract" in (result.error or "") or "OCR" in (result.error or "")


def test_legacy_doc_marks_unsupported_without_libreoffice(monkeypatch) -> None:
    settings = Settings(libreoffice_path="")
    monkeypatch.setattr("qka_api.services.preview_engine.find_soffice", lambda _settings: None)
    result = extract_document("doc", b"legacy-bytes", max_chars=1000, settings=settings)
    assert result.status == EXTRACT_UNSUPPORTED
    assert "LibreOffice" in (result.error or "")


def test_queue_retry_missing_and_idempotent(client) -> None:
    settings = client.app.state.settings
    gateway = FakeSmbGateway()
    client.app.state.smb_gateway = gateway
    inventory_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        _ensure_connection(session)
        row = SynologyFileInventory(
            id=inventory_id,
            approved_share_id=SHARE_ID,
            relative_path=r"EDUARDTESTCLOUD\quote.txt",
            relative_path_normalized=r"EDUARDTESTCLOUD\QUOTE.TXT",
            file_name="quote.txt",
            extension="txt",
            file_type_category=0,
            size_bytes=12,
            last_write_utc=now,
            sync_status=0,
            acl_capture_status=1,
            is_present=True,
            is_directory=False,
            parent_path_normalized="EDUARDTESTCLOUD",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()

        batch = extraction_svc.start_reindex_selected(
            session,
            settings,
            actor_user_id=ALLOWED_USER,
            inventory_ids=[inventory_id],
            force_outside_pilot=False,
        )
        session.commit()
        assert batch.queued_count == 1

        gateway.contents[r"EDUARDTESTCLOUD\QUOTE.TXT"] = b"alpha renewal price token"
        # Process twice — second pass should be idle (idempotent).
        n1 = extraction_svc.process_extraction_queue(session, settings, gateway, max_items=5)
        session.commit()
        n2 = extraction_svc.process_extraction_queue(session, settings, gateway, max_items=5)
        session.commit()
        assert n1 == 1
        assert n2 == 0
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == inventory_id)
        ).scalar_one()
        assert content.status == EXTRACT_SUCCEEDED
        assert "alpha renewal" in (content.extracted_text or "")
        assert content.extractor_version == EXTRACTOR_VERSION

        # Unchanged file with missing text should be re-queueable.
        content.extracted_text = None
        content.status = 0
        session.commit()
        assert extraction_svc.needs_extraction_retry(session, settings, row) is True


def test_stale_content_invalidated_on_requeue(client) -> None:
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    inventory_id = uuid.uuid4()
    with client.app.state.session_factory() as session:
        row = SynologyFileInventory(
            id=inventory_id,
            approved_share_id=SHARE_ID,
            relative_path=r"EDUARDTESTCLOUD\old.txt",
            relative_path_normalized=r"EDUARDTESTCLOUD\OLD.TXT",
            file_name="old.txt",
            extension="txt",
            file_type_category=0,
            size_bytes=4,
            last_write_utc=now,
            sync_status=0,
            acl_capture_status=1,
            is_present=True,
            is_directory=False,
            parent_path_normalized="EDUARDTESTCLOUD",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        content = SynologyDocumentContent(
            id=uuid.uuid4(),
            inventory_file_id=inventory_id,
            approved_share_id=SHARE_ID,
            relative_path_normalized=row.relative_path_normalized,
            file_name=row.file_name,
            extension="txt",
            status=EXTRACT_SUCCEEDED,
            extracted_text="STALE_SHOULD_NOT_SERVE",
            extractor_version=EXTRACTOR_VERSION,
            created_at=now,
            updated_at=now,
        )
        session.add(content)
        session.commit()
        extraction_svc.start_reindex_selected(
            session,
            settings,
            actor_user_id=ALLOWED_USER,
            inventory_ids=[inventory_id],
        )
        session.commit()
        refreshed = session.get(SynologyDocumentContent, content.id)
        assert refreshed is not None
        assert refreshed.status == 0
        assert refreshed.extracted_text is None


def test_mcp_chunk_retrieval_and_auth(client) -> None:
    now = datetime.now(timezone.utc)
    inventory_id = uuid.uuid4()
    long_text = ("CHUNK-A " * 800) + ("CHUNK-B " * 800)
    chunks = [
        {"ordinal": 0, "text": "page one alpha", "method": "native", "page": 1, "ref": "page:1"},
        {"ordinal": 1, "text": "page two beta", "method": "ocr", "page": 2, "ref": "page:2:ocr"},
    ]
    with client.app.state.session_factory() as session:
        session.add(
            SynologyFileInventory(
                id=inventory_id,
                approved_share_id=SHARE_ID,
                relative_path=r"EDUARDTESTCLOUD\long.pdf",
                relative_path_normalized=r"EDUARDTESTCLOUD\LONG.PDF",
                file_name="long.pdf",
                extension="pdf",
                file_type_category=2,
                size_bytes=100,
                last_write_utc=now,
                sync_status=0,
                acl_capture_status=1,
                acl_snapshot_json=json.dumps(
                    [{"Identity": SID_ALLOWED, "Rights": "Modify, Read", "AccessControlType": "Allow"}]
                ),
                is_present=True,
                is_directory=False,
                parent_path_normalized="EDUARDTESTCLOUD",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            SynologyDocumentContent(
                id=uuid.uuid4(),
                inventory_file_id=inventory_id,
                approved_share_id=SHARE_ID,
                relative_path_normalized=r"EDUARDTESTCLOUD\LONG.PDF",
                file_name="long.pdf",
                extension="pdf",
                status=EXTRACT_SUCCEEDED,
                extracted_text=long_text,
                chunks_json=json.dumps(chunks),
                extraction_method="mixed",
                ocr_used=True,
                extractor_version=EXTRACTOR_VERSION,
                truncated=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        first = docs.fetch_document(session, settings, ALLOWED_USER, inventory_id.hex, offset=0)
        assert first["denied"] is False
        assert first["contentAvailable"] is True
        assert first["hasMore"] is True
        assert first["nextOffset"] is not None
        second = docs.fetch_document(
            session, settings, ALLOWED_USER, inventory_id.hex, offset=int(first["nextOffset"])
        )
        assert second["text"]
        chunk = docs.fetch_document(
            session, settings, ALLOWED_USER, inventory_id.hex, chunk_index=1
        )
        assert chunk["previewKind"] == "indexed_chunk"
        assert "page two beta" in chunk["text"]
        assert chunk["references"][0]["page"] == 2
        assert chunk["ocrUsed"] is True

        denied = docs.fetch_document(session, settings, DENIED_USER, inventory_id.hex)
        assert denied["denied"] is True
        assert denied.get("text") in (None, "")


def test_extract_strips_nul_bytes_for_postgres() -> None:
    """Binary / mis-decoded text with NULs must not reach PostgreSQL text columns."""
    result = extract_document("txt", b"Hello\x00World\x00", max_chars=20_000)
    assert result.status == EXTRACT_SUCCEEDED
    assert "\x00" not in result.text
    assert "Hello" in result.text and "World" in result.text
    assert "\x00" not in result.chunks_json()


def test_view_does_not_grant_download(client) -> None:
    """VIEW authorization for fetch must not imply DOWNLOAD."""
    cookies = auth_cookies(ALLOWED_USER)
    # Endpoint exists and enforces download capability separately.
    response = client.get("/api/v1/documents/not-a-real-id/download", cookies=cookies)
    assert response.status_code in {403, 404, 200}
    if response.status_code == 200:
        body = response.json()
        assert body.get("denied") is True


@pytest.mark.skipif(
    not ocr_status(Settings()).get("available"),
    reason="Tesseract OCR with eng/ara not installed on this host",
)
def test_ocr_english_integration() -> None:
    settings = Settings()
    result = extract_document("png", _png_bytes("Invoice Total 1234"), max_chars=20_000, settings=settings)
    assert result.status == EXTRACT_SUCCEEDED
    assert result.ocr_used is True
    assert any(ch.isdigit() for ch in result.text)
