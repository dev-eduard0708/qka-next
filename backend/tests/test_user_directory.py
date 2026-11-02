from __future__ import annotations

from qka_shared.chat_access import require_local_chat, require_mcp_chat
from tests.conftest import ALLOWED_USER, GOOGLE_ONLY_USER
from tests.test_source_access import _admin_headers


def test_add_user_name_email_and_reject_invalid_or_duplicate(client):
    headers = _admin_headers(client)
    created = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "Fatimah Al-Qahtani", "email": "fatimah.qahtani@qehc.edu.sa"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["displayName"] == "Fatimah Al-Qahtani"
    assert body["primaryEmail"] == "fatimah.qahtani@qehc.edu.sa"
    assert body["identityStatus"] == "pending"
    assert body["accountStatus"] == "pending"
    assert body["chatgptMembership"] == "unknown"
    assert body["localChatEnabled"] is False
    assert body["mcpEnabled"] is False
    assert body["canGrant"] is True

    listed = client.get("/api/v1/access/users", headers=headers, params={"search": "fatimah"})
    assert listed.status_code == 200
    emails = {row["primaryEmail"] for row in listed.json()["items"]}
    assert "fatimah.qahtani@qehc.edu.sa" in emails

    employees = client.get("/api/v1/access/source/employees", headers=headers).json()
    assert any(row["primaryEmail"] == "fatimah.qahtani@qehc.edu.sa" for row in employees)

    bad_domain = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "Outsider", "email": "person@gmail.com"},
    )
    assert bad_domain.status_code == 400

    dup = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"displayName": "Fatimah Again", "email": "FATIMAH.QAHTANI@qehc.edu.sa"},
    )
    assert dup.status_code == 409


def test_unknown_membership_is_not_nonmember(client):
    headers = _admin_headers(client)
    rows = client.get("/api/v1/access/users", headers=headers, params={"pageSize": 100}).json()["items"]
    eduard = next(row for row in rows if row["primaryEmail"] == "eduard@qehc.edu.sa")
    assert eduard["chatgptMembership"] == "unknown"
    assert eduard["identityStatus"] == "linked"
    pending = next(row for row in rows if row["primaryEmail"] == "dev.eduard@qehc.edu.sa")
    assert pending["identityStatus"] == "pending"
    assert pending["chatgptMembership"] == "unknown"


def test_membership_sync_is_manual_csv_only(client):
    headers = _admin_headers(client)
    info = client.get("/api/v1/access/users/membership-sync", headers=headers).json()
    assert info["automaticSyncSupported"] is False
    assert info["mode"] == "manual_csv_import"


def test_membership_csv_import_creates_pending_without_privileges(client):
    headers = _admin_headers(client)
    csv_body = "email,name,role,seat type\nimported.user@qehc.edu.sa,Imported User,member,standard\n"
    uploaded = client.post(
        "/api/v1/access/users/membership/import",
        headers=headers,
        files={"file": ("members.csv", csv_body, "text/csv")},
        data={"markMissingAsNonmember": "false"},
    )
    assert uploaded.status_code == 200, uploaded.text
    result = uploaded.json()
    assert result["createdCount"] == 1
    assert result["memberCount"] == 1
    assert result["mode"] == "manual_csv_import"

    listed = client.get(
        "/api/v1/access/users",
        headers=headers,
        params={"search": "imported.user"},
    ).json()["items"]
    assert len(listed) == 1
    row = listed[0]
    assert row["displayName"] == "Imported User"
    assert row["chatgptMembership"] == "verified_member"
    assert row["allowlistEnabled"] is False
    assert row["localChatEnabled"] is False
    assert row["mcpEnabled"] is False
    assert row["identityStatus"] == "disabled"

    employees = client.get("/api/v1/access/source/employees", headers=headers).json()
    assert any(item["primaryEmail"] == "imported.user@qehc.edu.sa" for item in employees)

    audit = client.get("/api/v1/access/source/audit", headers=headers).json()
    actions = {item["action"] for item in audit["items"]}
    assert "MembershipImportCompleted" in actions
    assert "UserCreated" in actions


def test_local_chat_independent_of_business_membership(client):
    headers = _admin_headers(client)
    created = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "displayName": "Local Only",
            "email": "local.only@qehc.edu.sa",
            "localChatEnabled": True,
            "mcpEnabled": False,
        },
    )
    assert created.status_code == 200
    assert created.json()["chatgptMembership"] == "unknown"
    assert created.json()["localChatEnabled"] is True
    assert created.json()["mcpEnabled"] is False

    with client.app.state.session_factory() as session:
        # Pending users have no QkaUser yet; linked users keep existing channel checks.
        local = require_local_chat(session, ALLOWED_USER)
        mcp_google = require_mcp_chat(session, GOOGLE_ONLY_USER)
        assert local.allowed is True
        assert mcp_google.allowed is True
