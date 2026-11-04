"""Seamless per-user MCP OAuth (ChatGPT Business Connect + Google/QKA session reuse)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from qka_api.services.mcp_diagnostics import _probe_tunnel
from qka_shared.chat_access import require_mcp_chat
from qka_shared.models import ChatAccess, QkaUser
from tests.conftest import (
    ALLOWED_USER,
    DENIED_USER,
    DISABLED_USER,
    GOOGLE_ONLY_USER,
    ALLOW_EMAIL_OTHER,
    auth_cookies,
)


REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
SCOPE = "mcp:documents.read offline_access openid email"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def _enable_oauth(client) -> None:
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    oauth.settings.google_client_id = "test-google-client.apps.googleusercontent.com"
    oauth.settings.google_client_secret = "test-google-secret"
    oauth.settings.google_redirect_uri = "http://127.0.0.1:5186/api/v1/auth/google/callback"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        oauth.settings.mcp_inspector_user_id = admin.id


def _register(client) -> str:
    reg = client.post(
        "/oauth/register",
        json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"},
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["client_id"]


def _exchange(client, client_id: str, code: str, verifier: str) -> dict:
    oauth = client.app.state.mcp_oauth
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": oauth.resource,
        },
    )
    assert token.status_code == 200, token.text
    return token.json()


def test_a_first_time_google_callback_issues_per_user_code(google_client):
    _enable_oauth(google_client)
    client_id = _register(google_client)
    verifier, challenge = _pkce()
    start = google_client.get(
        "/oauth/google/start",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPE,
            "state": "chatgpt-state",
        },
        follow_redirects=False,
    )
    assert start.status_code == 302
    location = start.headers["location"]
    assert "accounts.google.com" in location
    assert "prompt=" not in location  # reuse Google browser session when present
    qs = parse_qs(urlparse(location).query)
    state = qs["state"][0]

    callback = google_client.get(
        "/oauth/google/callback",
        params={"code": "code-allowed", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    back = urlparse(callback.headers["location"])
    assert back.scheme == "https"
    assert "chatgpt.com" in (back.netloc or "")
    code = parse_qs(back.query)["code"][0]
    assert google_client.cookies.get("qka_next_session")  # QKA session for later reuse

    body = _exchange(google_client, client_id, code, verifier)
    assert body["access_token"]
    assert body["refresh_token"]
    access = google_client.app.state.mcp_oauth.validate_access_token(body["access_token"])
    assert access is not None
    assert access.subject == str(ALLOWED_USER)

    with google_client.app.state.session_factory() as session:
        user = session.get(QkaUser, ALLOWED_USER)
        assert user.mcp_connected_at is not None


def test_b_authorize_reuses_existing_qka_google_session(client):
    _enable_oauth(client)
    client_id = _register(client)
    verifier, challenge = _pkce()
    response = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPE,
            "state": "reuse",
        },
        cookies=auth_cookies(ALLOWED_USER),
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(REDIRECT)
    assert "accounts.google.com" not in location
    code = parse_qs(urlparse(location).query)["code"][0]
    body = _exchange(client, client_id, code, verifier)
    assert client.app.state.mcp_oauth.validate_access_token(body["access_token"]).subject == str(ALLOWED_USER)


def test_c_returning_user_valid_access_token(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    access, refresh, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=ALLOWED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=True,
    )
    validated = oauth.validate_access_token(access)
    assert validated is not None
    assert validated.subject == str(ALLOWED_USER)
    assert refresh


def test_d_expired_access_refresh_rotates(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_access_token_seconds = 1
    oauth.settings.mcp_oauth_refresh_token_seconds = 0
    access, refresh, expires = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=ALLOWED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=True,
    )
    assert expires == 1
    # Force expiry
    row = oauth._access[access]
    row.expires_at = 1.0
    assert oauth.validate_access_token(access) is None

    renewed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": "https://chatgpt.com",
            "resource": oauth.resource,
        },
    )
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    assert body["access_token"] != access
    assert body["refresh_token"] != refresh
    # Old refresh consumed (rotation)
    again = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": "https://chatgpt.com"},
    )
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


def test_e_revoked_refresh_token(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    access, refresh, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=ALLOWED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=True,
    )
    revoked = client.post("/oauth/revoke", data={"token": refresh, "token_type_hint": "refresh_token"})
    assert revoked.status_code == 200
    assert oauth.validate_access_token(access) is None
    fail = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": "https://chatgpt.com"},
    )
    assert fail.status_code == 400


def test_f_disabled_qka_user_cannot_refresh_or_authorize(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    with client.app.state.session_factory() as session:
        user = session.get(QkaUser, ALLOWED_USER)
        user.login_enabled = False
        session.commit()
    try:
        _, refresh, _ = oauth.issue_tokens(
            client_id="https://chatgpt.com",
            qka_user_id=ALLOWED_USER,
            scope=SCOPE,
            resource=oauth.resource,
            include_refresh=True,
        )
        fail = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": "https://chatgpt.com"},
        )
        assert fail.status_code == 400
        assert "not active" in fail.json().get("error_description", "").lower()

        client_id = _register(client)
        verifier, challenge = _pkce()
        response = client.get(
            "/oauth/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": SCOPE,
                "state": "disabled",
            },
            cookies=auth_cookies(ALLOWED_USER),
            follow_redirects=False,
        )
        # No session reuse — falls through to Google start (or HTML)
        assert response.status_code == 302
        assert REDIRECT not in response.headers.get("location", "") or "error=" in response.headers.get("location", "")
        location = response.headers["location"]
        assert "code=" not in location or "error=" in location
    finally:
        with client.app.state.session_factory() as session:
            user = session.get(QkaUser, ALLOWED_USER)
            user.login_enabled = True
            session.commit()


def test_g_mcp_permission_disabled_is_403_style_not_relogin(client):
    _enable_oauth(client)
    with client.app.state.session_factory() as session:
        assert require_mcp_chat(session, DENIED_USER).allowed is False
        assert require_mcp_chat(session, DENIED_USER).reason == "mcp_disabled"
        row = session.execute(select(ChatAccess).where(ChatAccess.allowed_email_id == ALLOW_EMAIL_OTHER)).scalar_one()
        assert row.mcp_enabled is False

    oauth = client.app.state.mcp_oauth
    access, _, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=DENIED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=False,
    )
    # Token remains valid (no forced re-login); tools enforce MCP flag.
    assert oauth.validate_access_token(access) is not None
    channel_denied = require_mcp_chat
    with client.app.state.session_factory() as session:
        denied = channel_denied(session, DENIED_USER)
        assert denied.allowed is False
        assert denied.reason == "mcp_disabled"


def test_h_two_employees_separate_token_binding(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    a1, _, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=ALLOWED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=False,
    )
    a2, _, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=GOOGLE_ONLY_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=False,
    )
    assert a1 != a2
    assert oauth.validate_access_token(a1).subject == str(ALLOWED_USER)
    assert oauth.validate_access_token(a2).subject == str(GOOGLE_ONLY_USER)


def test_i_tunnel_offline_does_not_imply_google_login(client):
    settings = client.app.state.settings
    settings.mcp_tunnel_ready_url = "http://127.0.0.1:1/readyz"
    probe = _probe_tunnel(settings)
    assert probe["status"] in {"error", "disconnected"}
    assert "Tunnel offline" in (probe.get("error") or "")
    assert "Do not prompt Google login" in (probe.get("error") or "")


def test_j_existing_google_qka_login_still_works(google_client):
    start = google_client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 302
    assert "select_account" in start.headers["location"]
    state = start.cookies.get("qka_oidc_state")
    callback = google_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "code-allowed", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert google_client.cookies.get("qka_next_session")
    me = google_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(ALLOWED_USER)


def test_authorize_without_session_redirects_to_google_start(client):
    _enable_oauth(client)
    client_id = _register(client)
    _, challenge = _pkce()
    response = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPE,
            "state": "fresh",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("/oauth/google/start?")


def test_metadata_includes_revocation_and_pkce(client):
    _enable_oauth(client)
    meta = client.get("/.well-known/oauth-authorization-server")
    assert meta.status_code == 200
    body = meta.json()
    assert body["revocation_endpoint"].endswith("/oauth/revoke")
    assert "S256" in body["code_challenge_methods_supported"]


def test_disabled_allowlist_user_denied_on_token(client):
    _enable_oauth(client)
    oauth = client.app.state.mcp_oauth
    _, refresh, _ = oauth.issue_tokens(
        client_id="https://chatgpt.com",
        qka_user_id=DISABLED_USER,
        scope=SCOPE,
        resource=oauth.resource,
        include_refresh=True,
    )
    fail = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": "https://chatgpt.com"},
    )
    assert fail.status_code == 400
    assert fail.json()["error"] == "invalid_grant"


def test_userinfo_401_challenge(client):
    _enable_oauth(client)
    missing = client.get("/oauth/userinfo")
    assert missing.status_code == 401
    assert "WWW-Authenticate" in missing.headers
    bad = client.get("/oauth/userinfo", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401
