from __future__ import annotations

from qka_shared.source_access import Capability, evaluate_document_access
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qka_shared.models import QkaUser, SourceGrant, SynologyDocumentContent, SynologyFileInventory
from tests.conftest import ALLOWED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


def test_eligible_scopes_has_no_corporate_warning(client):
    headers = _admin_headers(client)
    body = client.get(
        f"/api/v1/access/source/eligible-scopes?approvedShareId={SHARE_ID}",
        headers=headers,
    ).json()
    assert body.get("reason") in (None, "")
    assert "corporate designated" not in (body.get("reason") or "").lower()


def test_view_allows_search_but_not_download(client):
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "view-only",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "VIEW-ONLY-1",
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
            "reason": "view only",
        },
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["capabilityView"] is True
    assert grant.json()["capabilityDownload"] is False

    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert len(search.json()["results"]) == 1
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert preview.json()["denied"] is False
    assert preview.json().get("previewKind") == "indexed_text"

    dl = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert dl.status_code == 200
    assert dl.json()["denied"] is True
    assert "Download" in dl.json()["error"]


def test_download_requires_explicit_permission(client):
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "with-download",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "DL-1",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200
    # Upgrade existing scope from prior test may collide — use grant upsert on returned id.
    scope_id = reg.json()["id"]
    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "capabilityDownload": True,
            "reason": "view+download",
        },
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["capabilityDownload"] is True

    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    dl = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert dl.status_code == 200
    assert dl.json()["denied"] is False
    assert dl.json()["fileName"]


def test_migration_does_not_auto_enable_download(client):
    """Legacy R/S/P grant rows map to View; Download stays false."""
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == ALLOWED_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        # Personal ACL still allows VIEW for ALLOWED_USER.
        view = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert view.allowed is True
        download = evaluate_document_access(session, user, inv, content, capability=Capability.DOWNLOAD)
        assert download.allowed is False
        assert download.denial_reason == "download_requires_explicit_grant"
