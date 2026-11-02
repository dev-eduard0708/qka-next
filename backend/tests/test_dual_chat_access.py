from __future__ import annotations

import uuid

from qka_shared.chat_access import require_local_chat, require_mcp_chat
from tests.conftest import (
    ALLOW_EMAIL_EDUARD,
    ALLOWED_USER,
    DENIED_USER,
    GOOGLE_ONLY_USER,
    SHARE_ID,
    auth_cookies,
)
from tests.test_source_access import _admin_headers


def test_nonmember_appears_in_source_access_dropdown(client):
    headers = _admin_headers(client)
    rows = client.get("/api/v1/access/source/employees", headers=headers).json()
    emails = {row["primaryEmail"] for row in rows}
    assert "eduard@qehc.edu.sa" in emails
    assert "dev.eduard@qehc.edu.sa" in emails  # pending
    assert "other.person@qehc.edu.sa" in emails
    eduard = next(row for row in rows if row["primaryEmail"] == "eduard@qehc.edu.sa")
    assert eduard["identityStatus"] == "linked"
    pending = next(row for row in rows if row["primaryEmail"] == "dev.eduard@qehc.edu.sa")
    assert pending["identityStatus"] == "pending"
    assert pending["canGrant"] is True


def test_chat_access_flags_independent_of_chatgpt_membership(client):
    headers = _admin_headers(client)
    employees = client.get("/api/v1/access/source/employees", headers=headers).json()
    target = next(row for row in employees if row["primaryEmail"] == "other.person@qehc.edu.sa")
    updated = client.post(
        "/api/v1/access/source/chat-access",
        headers=headers,
        json={"allowlistId": target["allowlistId"], "localChatEnabled": True, "mcpEnabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["localChatEnabled"] is True
    assert updated.json()["mcpEnabled"] is False

    with client.app.state.session_factory() as session:
        local = require_local_chat(session, DENIED_USER)
        mcp = require_mcp_chat(session, DENIED_USER)
        assert local.allowed is True and local.local_chat is True
        assert mcp.allowed is False and mcp.reason == "mcp_disabled"


def test_local_only_denied_mcp_and_mcp_only_denied_local(client):
    headers = _admin_headers(client)
    local_only = client.post(
        "/api/v1/access/source/chat-access",
        headers=headers,
        json={"allowlistId": str(ALLOW_EMAIL_EDUARD), "localChatEnabled": True, "mcpEnabled": False},
    )
    assert local_only.status_code == 200
    with client.app.state.session_factory() as session:
        assert require_local_chat(session, ALLOWED_USER).allowed is True
        assert require_mcp_chat(session, ALLOWED_USER).allowed is False

    mcp_only = client.post(
        "/api/v1/access/source/chat-access",
        headers=headers,
        json={"allowlistId": str(ALLOW_EMAIL_EDUARD), "localChatEnabled": False, "mcpEnabled": True},
    )
    assert mcp_only.status_code == 200
    with client.app.state.session_factory() as session:
        assert require_local_chat(session, ALLOWED_USER).allowed is False
        assert require_mcp_chat(session, ALLOWED_USER).allowed is True

    client.cookies.clear()
    cookies = auth_cookies(ALLOWED_USER)
    denied_local = client.get("/api/v1/ai/conversations", cookies=cookies)
    assert denied_local.status_code == 403
    assert "Local AI Chat" in denied_local.json()["detail"]


def test_both_channels_share_source_grants(client):
    headers = _admin_headers(client)
    client.post(
        "/api/v1/access/source/chat-access",
        headers=headers,
        json={"allowlistId": str(ALLOW_EMAIL_EDUARD), "localChatEnabled": True, "mcpEnabled": True},
    )
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "shared-channels",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "SHARED-CHANNELS",
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
            "allowlistId": str(ALLOW_EMAIL_EDUARD),
            "capabilityRead": True,
            "capabilitySearch": True,
            "capabilityPreview": True,
            "pathMode": "all",
            "fileTypes": [],
            "reason": "shared grant",
        },
    )
    assert grant.status_code == 200, grant.text

    client.cookies.clear()
    cookies = auth_cookies(GOOGLE_ONLY_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert len(search.json()["results"]) == 1

    with client.app.state.session_factory() as session:
        assert require_local_chat(session, GOOGLE_ONLY_USER).allowed is True
        assert require_mcp_chat(session, GOOGLE_ONLY_USER).allowed is True

    headers = _admin_headers(client)
    audit = client.get("/api/v1/access/source/audit", headers=headers)
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert "Created" in actions or "Updated" in actions
    assert any(
        a in actions
        for a in {"LocalChatEnabled", "McpEnabled", "ChatAccessUpdated", "LocalChatDisabled", "McpDisabled"}
    )


def test_source_acl_model_blocks_grants_until_qka_managed(client):
    headers = _admin_headers(client)
    eligible = client.get(
        f"/api/v1/access/source/eligible-scopes?approvedShareId={SHARE_ID}",
        headers=headers,
    )
    assert eligible.status_code == 200
    body = eligible.json()
    # No corporate approval warning; rules may be prepared without QKA Managed.
    assert body.get("reason") in (None, "")
    if not body["eligible"]:
        assert body["accessModel"] == "source_acl"
        prepared = client.post(
            "/api/v1/access/source/grants",
            headers=headers,
            json={
                "approvedShareId": str(SHARE_ID),
                "qkaUserId": str(GOOGLE_ONLY_USER),
                "allowlistId": str(ALLOW_EMAIL_EDUARD),
                "capabilityView": True,
                "capabilityDownload": False,
            },
        )
        assert prepared.status_code == 200, prepared.text
        # Without QKA Managed authorization, search still denies for Google-only user.
        client.cookies.clear()
        search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=auth_cookies(GOOGLE_ONLY_USER))
        assert search.status_code == 200
        assert search.json()["results"] == []
