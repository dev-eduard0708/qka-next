from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from qka_shared.models import QkaUser, SourceGrant, SourceScope, SynologyDocumentContent, SynologyFileInventory
from qka_shared.source_access import GrantStatus, ScopeStatus, TrustClass, evaluate_document_access
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies


def _admin_headers(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def test_allowlisted_without_grant_sees_no_hits_for_google_only(client):
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    body = search.json()
    assert body["error"] is None
    assert body["results"] == []


def test_ad_acl_user_still_searches_without_corporate_grant(client):
    cookies = auth_cookies(ALLOWED_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    body = search.json()
    assert body["error"] is None
    assert len(body["results"]) == 1


def test_corporate_grant_enables_google_user_without_ad(client):
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    client.cookies.clear()
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert search.json()["error"] is None
    assert search.json()["results"] == []

    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "Pilot corporate test",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "TEST-APPROVAL-1",
            "approvalNotes": "pytest",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200, reg.text
    scope_id = reg.json()["id"]

    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "reason": "pilot grant",
            "pathMode": "selected",
            "includeSubfolders": True,
            "selectedPaths": ["EDUARDTESTCLOUD"],
            "fileTypes": ["excel"],
        },
    )
    assert grant.status_code == 200, grant.text
    grant_id = grant.json()["id"]
    assert grant.json()["pathMode"] == "selected"
    assert grant.json()["fileTypes"] == ["excel"]

    client.cookies.clear()
    search2 = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search2.status_code == 200, search2.text
    body = search2.json()
    assert body["error"] is None
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == FILE_ID.hex

    # PDF-only rule must hide the Excel hit
    headers = _admin_headers(client)
    pdf_only = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "pathMode": "all",
            "fileTypes": ["pdf"],
            "reason": "pdf only",
        },
    )
    assert pdf_only.status_code == 200, pdf_only.text
    client.cookies.clear()
    assert client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies).json()["results"] == []

    headers = _admin_headers(client)
    restore = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": scope_id,
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "pathMode": "all",
            "fileTypes": [],
            "reason": "restore all types",
        },
    )
    assert restore.status_code == 200
    grant_id = restore.json()["id"]
    client.cookies.clear()
    fetch = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch.json()["denied"] is False
    assert "Laptop" in fetch.json()["text"]

    headers = _admin_headers(client)
    revoke = client.post(
        f"/api/v1/access/source/grants/{grant_id}/status",
        headers=headers,
        json={"status": "revoked", "reason": "done"},
    )
    assert revoke.status_code == 200
    audit = client.get("/api/v1/access/source/audit", headers=headers)
    assert audit.status_code == 200
    page = audit.json()
    assert page["total"] >= 1
    assert any(item["action"] in {"Created", "Updated", "Revoked"} for item in page["items"])

    client.cookies.clear()
    search3 = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search3.json()["results"] == []
    fetch2 = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch2.json()["denied"] is True


def test_source_deny_overrides_corporate_grant(client):
    with client.app.state.session_factory() as session:
        user = session.execute(
            select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == DENIED_USER)
        ).scalar_one()
        inv = session.get(SynologyFileInventory, FILE_ID)
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == FILE_ID)
        ).scalar_one()
        now = datetime.now(timezone.utc)
        scope = SourceScope(
            id=uuid.uuid4(),
            approved_share_id=SHARE_ID,
            folder_prefix_normalized="EDUARDTESTCLOUD",
            trust_class=TrustClass.CORPORATE_DESIGNATED,
            status=ScopeStatus.ACTIVE,
            display_name="deny-test",
            source_owner_name="Owner",
            source_owner_email="owner@qehc.edu.sa",
            approval_reference="REF-DENY",
            approval_notes="",
            approved_at=now,
            approved_by_user_id=ALLOWED_USER,
            service_read_verified=True,
            created_at=now,
            updated_at=now,
        )
        session.add(scope)
        session.flush()
        session.add(
            SourceGrant(
                id=uuid.uuid4(),
                scope_id=scope.id,
                qka_user_id=DENIED_USER,
                capability_read=True,
                capability_search=True,
                capability_preview=True,
                capability_view=True,
                capability_download=False,
                status=GrantStatus.ACTIVE,
                reason="should not help",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        eff = evaluate_document_access(session, user, inv, content, capability="search")
        assert eff.allowed is False
        assert eff.denial_reason == "source_acl_deny"

    cookies = auth_cookies(DENIED_USER)
    assert client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies).json()["results"] == []


def test_employees_endpoint_lists_linked_pending_and_disabled(client):
    denied = client.get("/api/v1/access/source/employees", cookies=auth_cookies(ALLOWED_USER))
    assert denied.status_code == 403
    headers = _admin_headers(client)
    ok = client.get("/api/v1/access/source/employees", headers=headers)
    assert ok.status_code == 200
    rows = ok.json()
    by_email = {row["primaryEmail"]: row for row in rows}
    assert "eduard@qehc.edu.sa" in by_email
    assert by_email["eduard@qehc.edu.sa"]["identityStatus"] == "linked"
    assert by_email["eduard@qehc.edu.sa"]["canGrant"] is True
    assert by_email["eduard@qehc.edu.sa"]["googleSub"]
    assert "dev.eduard@qehc.edu.sa" in by_email
    assert by_email["dev.eduard@qehc.edu.sa"]["identityStatus"] == "pending"
    assert by_email["dev.eduard@qehc.edu.sa"]["canGrant"] is True
    assert by_email["dev.eduard@qehc.edu.sa"]["qkaUserId"] is None
    assert "disabled.user@qehc.edu.sa" in by_email
    assert by_email["disabled.user@qehc.edu.sa"]["identityStatus"] == "disabled"
    assert by_email["disabled.user@qehc.edu.sa"]["canGrant"] is False


def test_pending_user_can_store_inactive_grant(client):
    headers = _admin_headers(client)
    employees = client.get("/api/v1/access/source/employees", headers=headers).json()
    pending = next(row for row in employees if row["primaryEmail"] == "dev.eduard@qehc.edu.sa")
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD\\PENDING",
            "displayName": "pending-plan",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "TEST-PENDING",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200, reg.text
    # Fake UUID with no Google link must be rejected
    bad = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": reg.json()["id"],
            "qkaUserId": str(uuid.uuid4()),
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "reason": "should fail",
        },
    )
    assert bad.status_code == 400
    planned = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": reg.json()["id"],
            "allowlistId": pending["allowlistId"],
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "reason": "pending plan",
        },
    )
    assert planned.status_code == 200, planned.text
    assert planned.json()["pendingActivation"] is True
    assert planned.json()["qkaUserId"] is None
    assert planned.json()["allowedEmailId"] == pending["allowlistId"]


def test_corporate_registration_requires_approval_fields(client):
    headers = _admin_headers(client)
    bad = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "",
            "serviceReadVerified": True,
        },
    )
    assert bad.status_code == 400
