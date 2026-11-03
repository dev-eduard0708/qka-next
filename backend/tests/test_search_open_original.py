"""Search Open Original — canDownload on hits + download enforcement."""

from __future__ import annotations

import io

from openpyxl import Workbook

from qka_api.smb import FakeSmbGateway
from tests.conftest import FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.active["A1"] = "OpenOriginal"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _grant(client, *, download: bool, label: str) -> None:
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": label,
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": label.upper(),
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
            "capabilityDownload": download,
            "pathMode": "all",
            "reason": label,
        },
    )
    assert grant.status_code == 200, grant.text


def test_search_hit_can_download_false_for_view_only(client) -> None:
    _grant(client, download=False, label="open-original-view")
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Assets"}, cookies=cookies)
    assert search.status_code == 200
    hits = [item for item in search.json()["results"] if item["id"] == FILE_ID.hex]
    assert hits, "expected Assets search hit"
    assert hits[0]["canDownload"] is False
    assert hits[0].get("openWithLabel") in (None, "")


def test_search_hit_can_download_true_with_grant(client) -> None:
    _grant(client, download=True, label="open-original-dl")
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Assets"}, cookies=cookies)
    assert search.status_code == 200
    hits = [item for item in search.json()["results"] if item["id"] == FILE_ID.hex]
    assert hits
    assert hits[0]["canDownload"] is True
    assert hits[0]["openWithLabel"] == "Open with Microsoft Excel"
    assert hits[0]["recommendedApp"] == "Microsoft Excel"


def test_browse_hit_includes_can_download(client) -> None:
    _grant(client, download=True, label="open-original-browse")
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"fileType": "excel"}, cookies=cookies)
    assert search.status_code == 200
    hits = [item for item in search.json()["results"] if item["id"] == FILE_ID.hex]
    assert hits
    assert hits[0]["canDownload"] is True


def test_open_original_stream_rechecks_permission(client) -> None:
    gateway = FakeSmbGateway()
    payload = _xlsx_bytes()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = payload
    client.app.state.smb_gateway = gateway
    _grant(client, download=True, label="open-original-stream")
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    opened = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?intent=open", cookies=cookies)
    assert opened.status_code == 200
    assert opened.content == payload
    assert opened.headers.get("content-disposition", "").startswith("inline;")
    assert "unc" not in (opened.headers.get("content-disposition") or "").lower()


def test_view_only_cannot_stream_via_open_intent(client) -> None:
    gateway = FakeSmbGateway()
    gateway.contents[r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX"] = _xlsx_bytes()
    client.app.state.smb_gateway = gateway
    _grant(client, download=False, label="open-original-deny-stream")
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    opened = client.get(f"/api/v1/documents/{FILE_ID.hex}/download?intent=open", cookies=cookies)
    assert opened.status_code in {403, 200}
    if opened.status_code == 200 and "application/json" not in (opened.headers.get("content-type") or ""):
        raise AssertionError("view-only user received original bytes")
    body = opened.json()
    assert body.get("denied") is True
