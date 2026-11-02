from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from qka_shared.models import QkaUser


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def test_anonymous_mcp_initialize_is_denied(client, monkeypatch):
    monkeypatch.setenv("QKA_MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("QKA_MCP_INSPECTOR_BEARER", "inspector-dev-token")
    # Recreate app settings are cached — use existing app state's oauth if present.
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert admin is not None
        oauth.settings.mcp_inspector_user_id = admin.id

    response = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        }},
    )
    assert response.status_code in {401, 403}


def test_oauth_metadata_and_dcr(client):
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        oauth.settings.mcp_inspector_user_id = admin.id

    prmd = client.get("/.well-known/oauth-protected-resource")
    assert prmd.status_code == 200, prmd.text
    body = prmd.json()
    assert body["resource"].endswith("/mcp")
    assert "authorization_servers" in body

    as_meta = client.get("/.well-known/oauth-authorization-server")
    assert as_meta.status_code == 200
    assert "S256" in as_meta.json()["code_challenge_methods_supported"]
    assert as_meta.json().get("client_id_metadata_document_supported") is True

    registered = client.post(
        "/oauth/register",
        json={
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["client_id"].startswith("qka-next-")


def test_inspector_bearer_tools_and_acl(client):
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert admin is not None
        oauth.settings.mcp_inspector_user_id = admin.id

    headers = {
        "Authorization": "Bearer inspector-dev-token",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    # SDK may require session header after initialize; accept 200 or protocol-specific success.
    assert init.status_code in {200, 202}, init.text

    tools = client.post(
        "/mcp",
        headers={**headers, "MCP-Protocol-Version": "2025-03-26"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    # If session required, still assert auth is not 401
    assert tools.status_code != 401, tools.text

    # Direct service-level ACL denial for bogus id (does not bypass OAuth layer)
    from qka_api.services import documents as docs

    with client.app.state.session_factory() as session:
        denied = docs.fetch_document(session, client.app.state.settings, admin.id, "00000000000000000000000000000000")
        assert denied["denied"] is True


def test_admin_chatgpt_status_and_test(client):
    headers = _admin(client)
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        oauth.settings.mcp_inspector_user_id = admin.id

    status = client.get("/api/v1/admin/integrations/chatgpt/status")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert "mcpServer" in payload
    assert "chatgptBusiness" in payload
    assert payload["chatgptBusiness"]["status"] in {"not_verified", "live_verified"}
    # Must not claim ChatGPT connected from local health alone
    assert payload["chatgptBusiness"]["status"] != "connected"

    tested = client.post("/api/v1/admin/integrations/chatgpt/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert "steps" in tested.json()
    assert tested.json()["chatgptLiveVerified"] is False
