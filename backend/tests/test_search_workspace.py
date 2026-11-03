from __future__ import annotations

from qka_api.smb import FakeSmbGateway
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers
from tests.test_search_preview import _xlsx_bytes


def test_browse_excel_filter_lists_authorized_inventory(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/search", params={"fileType": "excel", "pageSize": 20}, cookies=cookies)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["total"] >= 1
    assert all(item["fileType"] == "excel" for item in body["results"])
    assert any(item["id"] == FILE_ID.hex for item in body["results"])


def test_browse_pdf_filter_excludes_excel(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/search", params={"fileType": "pdf"}, cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert all(item["fileType"] == "pdf" for item in body["results"])
    assert not any(item["id"] == FILE_ID.hex for item in body["results"])


def test_scoped_content_search_respects_file_type(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    excel = client.get("/api/v1/search", params={"q": "Laptop", "fileType": "excel"}, cookies=cookies)
    assert excel.status_code == 200
    assert any(item["id"] == FILE_ID.hex for item in excel.json()["results"])
    pdf = client.get("/api/v1/search", params={"q": "Laptop", "fileType": "pdf"}, cookies=cookies)
    assert pdf.json()["results"] == []


def test_clear_filter_style_all_types_still_finds_content(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert any(item["id"] == FILE_ID.hex for item in response.json()["results"])


def test_filename_search_finds_inventory(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/search", params={"q": "Assets New Form"}, cookies=cookies)
    assert response.status_code == 200
    assert any(item["id"] == FILE_ID.hex for item in response.json()["results"])
    hit = next(item for item in response.json()["results"] if item["id"] == FILE_ID.hex)
    assert hit["matchType"] in {"filename", "filename_exact", "content", "path"}


def test_denied_user_sees_no_browse_results(client) -> None:
    cookies = auth_cookies(DENIED_USER)
    response = client.get("/api/v1/search", params={"fileType": "excel"}, cookies=cookies)
    assert response.json()["results"] == []
    assert response.json()["total"] == 0


def test_pagination_metadata(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    response = client.get("/api/v1/search", params={"fileType": "excel", "page": 1, "pageSize": 1}, cookies=cookies)
    body = response.json()
    assert body["page"] == 1
    assert body["pageSize"] == 1
    assert body["total"] >= 1
    assert len(body["results"]) <= 1


def test_download_streams_original_bytes(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
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
            "displayName": "download-stream",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "DL-STREAM-1",
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
            "reason": "download stream",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    response = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/")
    assert "Assets" in (response.headers.get("content-disposition") or "")
    assert response.content == payload


def test_view_only_cannot_stream_original(client, tmp_path) -> None:
    client.app.state.settings.preview_cache_dir = str(tmp_path)
    gateway = FakeSmbGateway()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = _xlsx_bytes()
    client.app.state.smb_gateway = gateway
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "view-only-dl",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "VIEW-ONLY-DL-1",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200
    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": reg.json()["id"],
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "capabilityDownload": False,
            "pathMode": "all",
            "reason": "view only",
        },
    )
    assert grant.status_code == 200
    client.cookies.clear()
    response = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=auth_cookies(GOOGLE_ONLY_USER))
    assert response.status_code in {403, 200}
    body = response.json()
    assert body["denied"] is True
