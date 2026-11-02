from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qka_api.services import documents as docs
from qka_shared.allowlist import LINKED, PROVIDER_AD
from qka_shared.models import QkaUser, SynologyDocumentContent, SynologyFileInventory
from qka_shared.source_access import Capability, evaluate_document_access
from tests.conftest import ALLOWED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


def _activate_qka_managed(client, headers, prefix="EDUARDTESTCLOUD"):
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": prefix,
            "displayName": "QKA Managed pilot",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "QKA-MANAGED-TEST-1",
            "approvalNotes": "pytest",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return reg.json()["id"]


def test_google_user_without_grant_is_denied(client):
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert search.json()["results"] == []
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "no_linked_ad_identity"


def test_qka_managed_without_grant_denies_not_ad(client):
    headers = _admin_headers(client)
    _activate_qka_managed(client, headers)
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "qka_grant_missing"
        assert eff.denial_reason != "no_linked_ad_identity"
    client.cookies.clear()
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=auth_cookies(GOOGLE_ONLY_USER))
    assert search.json()["results"] == []


def test_qka_managed_view_grant_allows_google_without_ad(client):
    headers = _admin_headers(client)
    scope_id = _activate_qka_managed(client, headers)
    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "capabilityDownload": False,
            "pathMode": "all",
            "reason": "pilot view",
        },
    )
    assert grant.status_code == 200, grant.text
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        assert not any(i.provider == PROVIDER_AD and i.link_status == LINKED for i in user.identities)
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        view = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert view.allowed is True
        assert view.qka_grant == "qka_managed"
        assert view.denial_reason is None
        download = evaluate_document_access(session, user, inv, content, capability=Capability.DOWNLOAD)
        assert download.allowed is False
        assert download.denial_reason == "download_requires_explicit_grant"

    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    hits = search.json()["results"]
    assert len(hits) == 1
    assert hits[0]["title"] == "Assets New Form.xlsx"
    with client.app.state.session_factory() as session:
        rest_fetch = docs.fetch_document(session, client.app.state.settings, GOOGLE_ONLY_USER, hits[0]["id"])
        mcp_fetch = docs.fetch_document(session, client.app.state.settings, GOOGLE_ONLY_USER, hits[0]["id"])
        assert rest_fetch["denied"] is False
        assert mcp_fetch["denied"] is False
        assert rest_fetch.get("title") == mcp_fetch.get("title")
    preview = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert preview.json()["denied"] is False
    dl = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert dl.json()["denied"] is True

    headers = _admin_headers(client)
    audit = client.get("/api/v1/access/source/audit", headers=headers)
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1


def test_qka_managed_specific_folders_and_file_types(client):
    headers = _admin_headers(client)
    scope_id = _activate_qka_managed(client, headers)
    client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "pathMode": "selected",
            "includeSubfolders": True,
            "selectedPaths": ["EDUARDTESTCLOUD\\OTHER"],
            "fileTypes": ["excel"],
            "reason": "wrong folder",
        },
    )
    client.cookies.clear()
    assert client.get("/api/v1/search", params={"q": "Laptop"}, cookies=auth_cookies(GOOGLE_ONLY_USER)).json()["results"] == []
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "outside_allowed_scope"

    headers = _admin_headers(client)
    client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "pathMode": "all",
            "fileTypes": ["pdf"],
            "reason": "pdf only",
        },
    )
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "file_type_not_permitted"


def test_qka_managed_download_grant_and_revoke(client):
    headers = _admin_headers(client)
    scope_id = _activate_qka_managed(client, headers)
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
    assert grant.status_code == 200
    grant_id = grant.json()["id"]
    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    dl = client.get(f"/api/v1/documents/{FILE_ID.hex}/download", cookies=cookies)
    assert dl.status_code == 200
    assert dl.json()["denied"] is False
    assert dl.json()["fileName"]

    headers = _admin_headers(client)
    disabled = client.post(
        f"/api/v1/access/source/grants/{grant_id}/status",
        headers=headers,
        json={"status": "disabled", "reason": "pause"},
    )
    assert disabled.status_code == 200
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "qka_grant_disabled"

    client.cookies.clear()
    headers = _admin_headers(client)
    revoked = client.post(
        f"/api/v1/access/source/grants/{grant_id}/status",
        headers=headers,
        json={"status": "revoked", "reason": "done"},
    )
    assert revoked.status_code == 200
    client.cookies.clear()
    assert client.get("/api/v1/search", params={"q": "Laptop"}, cookies=auth_cookies(GOOGLE_ONLY_USER)).json()["results"] == []


def test_qka_managed_pending_google_is_denied(client):
    headers = _admin_headers(client)
    scope_id = _activate_qka_managed(client, headers)
    client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(ALLOWED_USER),
            "capabilityView": True,
            "reason": "ad-only probe",
        },
    )
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == ALLOWED_USER)
        ).scalar_one()
        user.identities = [row for row in user.identities if row.provider == PROVIDER_AD]
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        eff = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert eff.allowed is False
        assert eff.denial_reason == "pending_google_identity"


def test_source_acl_mode_still_uses_ad(client):
    cookies = auth_cookies(ALLOWED_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert len(search.json()["results"]) == 1
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == ALLOWED_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        view = evaluate_document_access(session, user, inv, content, capability=Capability.VIEW)
        assert view.allowed is True
        assert view.qka_grant == "none_personal_acl"
        download = evaluate_document_access(session, user, inv, content, capability=Capability.DOWNLOAD)
        assert download.allowed is False


def test_local_ai_uses_same_document_authorization(client):
    headers = _admin_headers(client)
    scope_id = _activate_qka_managed(client, headers)
    client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "reason": "ai parity",
        },
    )
    with client.app.state.session_factory() as session:
        from qka_api.services.documents import authorize_document

        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == GOOGLE_ONLY_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        assert authorize_document(session, client.app.state.settings, user, inv, content) is True
        hits, err = docs.search_documents(session, client.app.state.settings, GOOGLE_ONLY_USER, "Laptop")
        assert err is None
        assert len(hits) == 1
        fetched = docs.fetch_document(session, client.app.state.settings, GOOGLE_ONLY_USER, hits[0].id)
        assert fetched["denied"] is False
