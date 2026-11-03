from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from qka_api.services import documents as docs
from qka_shared.models import SynologyDocumentContent, SynologyFileInventory
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


PNG_ID = uuid.UUID("aaaaaaaa-aaa1-4aa1-8aa1-111111111111")
RENEWAL_ID = uuid.UUID("aaaaaaaa-aaa2-4aa2-8aa2-222222222222")
SONIC_ID = uuid.UUID("aaaaaaaa-aaa3-4aa3-8aa3-333333333333")
DENIED_FILLER_BASE = uuid.UUID("bbbbbbbb-ccc0-4cc0-8cc0-000000000000")


def _now():
    return datetime.now(timezone.utc)


def _add_inventory(session, *, inventory_id, file_name, relative_path, extension, size=100):
    now = _now()
    session.add(
        SynologyFileInventory(
            id=inventory_id,
            approved_share_id=SHARE_ID,
            relative_path=relative_path,
            relative_path_normalized=relative_path.upper(),
            file_name=file_name,
            extension=extension,
            file_type_category=0,
            size_bytes=size,
            last_write_utc=now,
            sync_status=1,
            acl_capture_status=1,
            acl_snapshot_json='[{"Identity":"S-1-5-21-4108582196-371218740-2140778505-3104","Rights":"Modify, Read","AccessControlType":"Allow"}]',
            is_present=True,
            is_directory=False,
            parent_path_normalized="EDUARDTESTCLOUD",
            created_at=now,
            updated_at=now,
        )
    )


def _add_content(session, *, inventory_id, file_name, relative_path, extension, status, text=None):
    now = _now()
    session.add(
        SynologyDocumentContent(
            id=uuid.uuid4(),
            inventory_file_id=inventory_id,
            approved_share_id=SHARE_ID,
            relative_path_normalized=relative_path.upper(),
            file_name=file_name,
            extension=extension,
            status=status,
            extracted_text=text,
            pilot_scoped=True,
            non_confidential=True,
            created_at=now,
            updated_at=now,
        )
    )


def _seed_search_fixtures(session) -> None:
    fixtures = [
        (
            PNG_ID,
            "Sonicwall Capture Client Quotation 2026.png",
            r"EDUARDTESTCLOUD\Sonicwall Capture Client Quotation 2026.png",
            "png",
            3,
            None,
            True,
        ),
        (
            RENEWAL_ID,
            "Renewal SONICWALL CAPTURE CLIENT.xlsx",
            r"EDUARDTESTCLOUD\Renewal SONICWALL CAPTURE CLIENT.xlsx",
            "xlsx",
            2,
            None,
            True,
        ),
        (
            SONIC_ID,
            "SonicWall Firewall Notes.xlsx",
            r"EDUARDTESTCLOUD\SonicWall Firewall Notes.xlsx",
            "xlsx",
            1,
            "Laptop inventory and SonicWall appliance notes.",
            True,
        ),
    ]
    for inventory_id, file_name, path, extension, status, text, allowed_acl in fixtures:
        _add_inventory(
            session,
            inventory_id=inventory_id,
            file_name=file_name,
            relative_path=path,
            extension=extension,
        )
    for index in range(30):
        filler_id = uuid.UUID(int=DENIED_FILLER_BASE.int + index + 1)
        path = rf"OTHERFOLDER\Denied Sonic Filler {index}.xlsx"
        _add_inventory(
            session,
            inventory_id=filler_id,
            file_name=f"Denied Sonic Filler {index}.xlsx",
            relative_path=path,
            extension="xlsx",
        )
    session.flush()
    for inventory_id, file_name, path, extension, status, text, allowed_acl in fixtures:
        _ = allowed_acl
        _add_content(
            session,
            inventory_id=inventory_id,
            file_name=file_name,
            relative_path=path,
            extension=extension,
            status=status,
            text=text,
        )
    for index in range(30):
        filler_id = uuid.UUID(int=DENIED_FILLER_BASE.int + index + 1)
        path = rf"OTHERFOLDER\Denied Sonic Filler {index}.xlsx"
        inv = session.get(SynologyFileInventory, filler_id)
        inv.acl_snapshot_json = "[]"
        inv.relative_path_normalized = path.upper()
        _add_content(
            session,
            inventory_id=filler_id,
            file_name=f"Denied Sonic Filler {index}.xlsx",
            relative_path=path,
            extension="xlsx",
            status=1,
            text="Laptop SonicWall filler content that must not crowd authorized hits.",
        )
    session.commit()


def test_png_quotation_discoverable_without_extraction(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    web = client.get("/api/v1/search", params={"q": "quotation"}, cookies=cookies)
    assert web.status_code == 200, web.text
    ids = {item["id"] for item in web.json()["results"]}
    assert PNG_ID.hex in ids
    hit = next(item for item in web.json()["results"] if item["id"] == PNG_ID.hex)
    assert hit["matchType"] in {"filename", "filename_exact"}
    assert hit["contentAvailable"] is False
    assert hit["indexingState"] == "unsupported"

    with client.app.state.session_factory() as session:
        mcp_hits, err = docs.search_documents(session, client.app.state.settings, ALLOWED_USER, "quotation")
        assert err is None
        assert any(hit.id == PNG_ID.hex for hit in mcp_hits)


def test_renewal_excel_pending_failed_extraction_still_found(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    web = client.get("/api/v1/search", params={"q": "renewal"}, cookies=cookies)
    assert any(item["id"] == RENEWAL_ID.hex for item in web.json()["results"])
    hit = next(item for item in web.json()["results"] if item["id"] == RENEWAL_ID.hex)
    assert hit["indexingState"] == "failed"
    assert hit["contentAvailable"] is False


def test_sonic_case_insensitive_partial_filename(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    web = client.get("/api/v1/search", params={"q": "sonic"}, cookies=cookies)
    ids = {item["id"] for item in web.json()["results"]}
    assert SONIC_ID.hex in ids
    assert PNG_ID.hex in ids
    assert RENEWAL_ID.hex in ids


def test_filename_retained_when_fts_also_matches(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
        # Ensure FTS-friendly content exists alongside filename-only PNG.
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        content.extracted_text = (content.extracted_text or "") + " quotation reference"
        session.commit()
    cookies = auth_cookies(ALLOWED_USER)
    web = client.get("/api/v1/search", params={"q": "quotation"}, cookies=cookies)
    ids = {item["id"] for item in web.json()["results"]}
    assert PNG_ID.hex in ids


def test_authorized_hits_not_crowded_out_by_denied_candidates(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    web = client.get("/api/v1/search", params={"q": "sonic"}, cookies=cookies)
    ids = {item["id"] for item in web.json()["results"]}
    assert SONIC_ID.hex in ids
    filler_hexes = {uuid.UUID(int=DENIED_FILLER_BASE.int + i + 1).hex for i in range(30)}
    assert ids.isdisjoint(filler_hexes)

    with client.app.state.session_factory() as session:
        hits, err = docs.search_documents(session, client.app.state.settings, ALLOWED_USER, "sonic")
        assert err is None
        assert any(hit.id == SONIC_ID.hex for hit in hits)
        assert {hit.id for hit in hits}.isdisjoint(filler_hexes)


def test_web_and_mcp_same_eligible_ids(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    web_ids = {item["id"] for item in client.get("/api/v1/search", params={"q": "sonic"}, cookies=cookies).json()["results"]}
    with client.app.state.session_factory() as session:
        mcp_hits, err = docs.search_documents(session, client.app.state.settings, ALLOWED_USER, "sonic")
        assert err is None
        mcp_ids = {hit.id for hit in mcp_hits}
    assert web_ids == mcp_ids


def test_file_type_filter_and_pagination_with_parity_fixtures(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    images = client.get("/api/v1/search", params={"q": "quotation", "fileType": "images"}, cookies=cookies)
    assert all(item["fileType"] == "images" for item in images.json()["results"])
    assert any(item["id"] == PNG_ID.hex for item in images.json()["results"])
    excel = client.get("/api/v1/search", params={"q": "sonic", "fileType": "excel", "pageSize": 1}, cookies=cookies)
    assert excel.json()["pageSize"] == 1
    assert len(excel.json()["results"]) <= 1
    assert all(item["fileType"] == "excel" for item in excel.json()["results"])


def test_unauthorized_and_denied_users(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    denied = client.get("/api/v1/search", params={"q": "sonic"}, cookies=auth_cookies(DENIED_USER))
    assert denied.json()["results"] == []
    with client.app.state.session_factory() as session:
        hits, err = docs.search_documents(session, client.app.state.settings, DENIED_USER, "sonic")
        assert hits == []
        assert err is None or "allow" in (err or "").lower() or "verified" in (err or "").lower() or err is None


def test_fetch_content_unavailable_vs_permission_denial(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    fetch = client.get(f"/api/v1/documents/{PNG_ID.hex}", cookies=cookies)
    body = fetch.json()
    assert body["denied"] is False
    assert body["contentAvailable"] is False
    assert body["indexingState"] == "unsupported"
    assert body["previewKind"] == "content_unavailable"
    assert body.get("text") == ""

    denied = client.get(f"/api/v1/documents/{PNG_ID.hex}", cookies=auth_cookies(DENIED_USER))
    assert denied.json()["denied"] is True
    assert "Sonicwall" not in (denied.json().get("error") or "")
    assert denied.json().get("relativePath") in (None, "")


def test_view_does_not_grant_download(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "parity-view",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "PARITY-1",
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
            "reason": "parity view only",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "quotation"}, cookies=cookies)
    assert any(item["id"] == PNG_ID.hex for item in search.json()["results"])
    download = client.get(f"/api/v1/documents/{PNG_ID.hex}/download?meta=1", cookies=cookies)
    assert download.json()["denied"] is True


def test_like_wildcard_in_query_is_literal(client) -> None:
    with client.app.state.session_factory() as session:
        _seed_search_fixtures(session)
    cookies = auth_cookies(ALLOWED_USER)
    # `%` should not become a SQL wildcard that matches everything.
    response = client.get("/api/v1/search", params={"q": "%"}, cookies=cookies)
    assert response.status_code == 200
    assert all("%" in (item.get("fileName") or "") or "%" in (item.get("relativePath") or "") for item in response.json()["results"]) or response.json()["results"] == []


def test_extraction_retry_helpers(tmp_path) -> None:
    from qka_api.extract import EXTRACTOR_VERSION, is_extractable
    from qka_api.services.synology import _needs_extraction_retry, _pilot_path
    from qka_shared.config import Settings

    assert is_extractable("xlsx") is True
    assert is_extractable("png") is True
    assert is_extractable("pptx") is True
    settings = Settings(pilot_folder_prefix="EDUARDTESTCLOUD")
    assert _pilot_path(settings, r"EDUARDTESTCLOUD\A.PNG") is True
    assert _pilot_path(settings, r"OTHER\A.PNG") is False
    assert EXTRACTOR_VERSION >= 2
    _ = tmp_path
    _ = _needs_extraction_retry
