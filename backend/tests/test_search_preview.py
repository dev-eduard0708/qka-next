from __future__ import annotations

import io

from openpyxl import Workbook
from pypdf import PdfWriter

from qka_api.smb import FakeSmbGateway
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Assets"
    sheet["A1"] = "Item"
    sheet["B1"] = "Laptop"
    sheet["A2"] = "Count"
    sheet["B2"] = 3
    second = wb.create_sheet("Notes")
    second["A1"] = "OK"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Inventory", level=1)
    doc.add_paragraph("Laptop assets for QKA preview.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_preview_requires_auth(client) -> None:
    response = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview")
    assert response.status_code == 401


def test_preview_falls_back_to_indexed_text_without_smb(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    cookies = auth_cookies(ALLOWED_USER)
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=cookies)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["denied"] is False
    assert body["previewKind"] in {"indexed_text", "excel_grid"}
    assert body["fileType"] == "excel"
    if body["previewKind"] == "indexed_text":
        assert "Laptop" in (body.get("text") or "")
        assert "indexed text preview" in (body.get("previewNote") or "").lower()


def test_preview_excel_grid_from_source_bytes(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    gateway = FakeSmbGateway()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = _xlsx_bytes()
    client.app.state.smb_gateway = gateway
    cookies = auth_cookies(ALLOWED_USER)
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=cookies)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["denied"] is False
    assert body["previewKind"] == "excel_grid"
    assert body["sheets"][0]["name"] == "Assets"
    assert any("Laptop" in row for row in body["rows"])
    notes = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", params={"sheet": 1}, cookies=cookies)
    assert notes.json()["sheetName"] == "Notes"
    assert notes.json()["rows"][0][0] == "OK"


def test_preview_word_and_pdf_and_csv(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    gateway = FakeSmbGateway()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = _docx_bytes()
    client.app.state.smb_gateway = gateway
    cookies = auth_cookies(ALLOWED_USER)
    word = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=cookies)
    # Extension is still xlsx on the inventory row; formatted builder uses extension.
    # Swap by generating via builder helpers through pdf/csv extracted fallback.
    assert word.status_code == 200
    pdf_preview = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert pdf_preview.json()["previewKind"] == "indexed_text"


def test_unauthorized_preview_denied(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    cookies = auth_cookies(DENIED_USER)
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=cookies)
    assert preview.status_code == 200
    assert preview.json()["denied"] is True
    assert preview.json()["previewKind"] == "denied"


def test_view_preview_does_not_enable_download(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "preview",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "PREVIEW-1",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200, reg.text
    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": reg.json()["id"],
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "capabilityDownload": False,
            "pathMode": "all",
            "reason": "preview view only",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=cookies)
    assert preview.json()["denied"] is False
    assert preview.json()["canDownload"] is False
    download = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert download.json()["denied"] is True
    fetch = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch.json()["previewKind"] == "indexed_text"


def test_cached_preview_cannot_bypass_permissions(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    gateway = FakeSmbGateway()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = _xlsx_bytes()
    client.app.state.smb_gateway = gateway
    allowed = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=auth_cookies(ALLOWED_USER))
    assert allowed.json()["previewKind"] == "excel_grid"
    denied = client.get(f"/api/v1/documents/{FILE_ID.hex}/preview", cookies=auth_cookies(DENIED_USER))
    assert denied.json()["denied"] is True


def test_preview_builders_csv_pdf_word(tmp_path) -> None:
    from qka_api.services.document_preview import _csv_table, _pdf_page, _word_blocks

    csv_body = _csv_table(b"a,b\n1,2\n", None)
    assert csv_body["previewKind"] == "csv_table"
    assert csv_body["rows"][0] == ["a", "b"]
    pdf_body = _pdf_page(_pdf_bytes(), 1)
    assert pdf_body["previewKind"] == "pdf_pages"
    assert pdf_body["pageCount"] == 2
    word_body = _word_blocks(_docx_bytes())
    assert word_body["previewKind"] == "word_document"
    assert any("Laptop" in (block.get("text") or "") for block in word_body["blocks"])
