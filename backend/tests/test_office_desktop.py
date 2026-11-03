"""Microsoft 365 desktop detection + Open With labels (no COM, no launch)."""

from __future__ import annotations

import io
from pathlib import Path

from openpyxl import Workbook

from qka_api.services.office_desktop import (
    detect_microsoft_office,
    microsoft_graph_preview_feasibility,
    open_with_label,
    recommended_app,
)
from qka_api.smb import FakeSmbGateway
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.active["A1"] = "Office"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_open_with_labels_map_to_office_apps() -> None:
    assert open_with_label("xlsx") == "Open with Microsoft Excel"
    assert open_with_label("docx") == "Open with Microsoft Word"
    assert open_with_label("pptx") == "Open with Microsoft PowerPoint"
    assert open_with_label("pdf") == "Open with default PDF application"
    assert recommended_app("xlsx") == "Microsoft Excel"
    assert recommended_app("docx") == "Microsoft Word"
    assert recommended_app("pptx") == "Microsoft PowerPoint"
    assert recommended_app("pdf") == "default PDF application"


def test_microsoft_graph_preview_not_available_for_synology() -> None:
    body = microsoft_graph_preview_feasibility()
    assert body["status"] == "NOT_AVAILABLE"
    assert body["localOfficeBrowserApi"] is False
    assert "SharePoint" in body["reason"] or "OneDrive" in body["reason"]


def test_detect_microsoft_office_read_only() -> None:
    info = detect_microsoft_office()
    assert "available" in info
    assert "apps" in info
    assert "launchPolicy" in info
    assert "COM" in info["launchPolicy"]
    for key in ("excel", "word", "powerpoint"):
        assert key in info["apps"]
        app = info["apps"][key]
        assert "available" in app
        assert "path" in app
        if app["available"]:
            assert Path(app["path"]).is_file()
            assert app["version"]


def test_preview_engine_status_includes_office(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    status = client.get("/api/v1/diagnostics/preview-engine", cookies=cookies)
    assert status.status_code == 200
    body = status.json()
    assert "microsoftOffice" in body
    assert "microsoftGraphPreview" in body
    assert body["microsoftGraphPreview"]["status"] == "NOT_AVAILABLE"
    assert body["pdfRenderer"] == "pymupdf"
    office = body["microsoftOffice"]
    assert "launchPolicy" in office
    assert "COM" in office["launchPolicy"]


def test_download_meta_includes_open_with_label(client) -> None:
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "office-meta",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "OFFICE-META-1",
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
            "capabilityDownload": True,
            "pathMode": "all",
            "reason": "office open-with meta",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    meta = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?meta=1", cookies=cookies)
    assert meta.status_code == 200
    body = meta.json()
    assert body.get("denied") is False
    assert body.get("openWithLabel") == "Open with Microsoft Excel"
    assert body.get("recommendedApp") == "Microsoft Excel"


def test_unauthorized_download_denied(client) -> None:
    cookies = auth_cookies(DENIED_USER)
    meta = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?meta=1", cookies=cookies)
    assert meta.status_code == 200
    body = meta.json()
    assert body.get("denied") is True
    streamed = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?intent=open", cookies=cookies)
    assert streamed.status_code in {403, 404}
    assert streamed.json().get("denied") is True


def test_open_intent_uses_inline_disposition(client) -> None:
    gateway = FakeSmbGateway()
    payload = _xlsx_bytes()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = payload
    client.app.state.smb_gateway = gateway
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "office-open-intent",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "OFFICE-OPEN-1",
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
            "capabilityDownload": True,
            "pathMode": "all",
            "reason": "office open intent",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)

    opened = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?intent=open", cookies=cookies)
    assert opened.status_code == 200
    assert opened.headers.get("content-disposition", "").startswith("inline;")
    assert opened.headers.get("x-qka-recommended-app") == "Microsoft Excel"
    assert opened.headers.get("x-qka-open-with-label") == "Open with Microsoft Excel"
    assert "unc" not in opened.headers.get("content-disposition", "").lower()
    assert opened.content == payload

    downloaded = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?intent=download", cookies=cookies)
    assert downloaded.status_code == 200
    assert downloaded.headers.get("content-disposition", "").startswith("attachment;")
