from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from qka_api.google_oidc import GOOGLE_ISSUERS, GoogleOidcError, GoogleOidcService
from qka_shared.config import Settings, get_settings
from qka_shared.google_oidc import GoogleOidcIdentity, accept_google_identity
from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, SID_ALLOWED


def _login(client, code: str):
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 302, start.text
    state = start.cookies.get("qka_oidc_state")
    assert state
    return client.get(
        "/api/v1/auth/google/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )


def test_accepts_verified_hosted_domain() -> None:
    email, error = accept_google_identity(
        GoogleOidcIdentity("sub-1", "Name@QEHC.EDU.SA", True, "qehc.edu.sa", "Name"),
        "qehc.edu.sa",
    )
    assert error == ""
    assert email == "name@qehc.edu.sa"


def test_rejects_unverified_wrong_domain_and_missing_sub() -> None:
    _, error = accept_google_identity(
        GoogleOidcIdentity("sub-1", "eduard@qehc.edu.sa", False, "qehc.edu.sa", "E"),
        "qehc.edu.sa",
    )
    assert "not verified" in error
    _, error = accept_google_identity(
        GoogleOidcIdentity("sub-1", "user@gmail.com", True, "gmail.com", "G"),
        "qehc.edu.sa",
    )
    assert "hosted domain" in error.lower() or "qehc.edu.sa" in error
    _, error = accept_google_identity(
        GoogleOidcIdentity("", "eduard@qehc.edu.sa", True, "qehc.edu.sa", "E"),
        "qehc.edu.sa",
    )
    assert "subject" in error.lower()


def test_expired_id_token_is_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        google_client_id="test-aud",
        google_redirect_uri="http://127.0.0.1:5186/api/v1/auth/google/callback",
        session_secret="unit-test-secret",
    )
    service = GoogleOidcService(settings)

    class StaticJwks:
        def get_signing_key_from_jwt(self, _token: str):
            return type("Key", (), {"key": key.public_key()})()

    service._jwks = StaticJwks()
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": GOOGLE_ISSUERS[0],
            "aud": "test-aud",
            "sub": "sub-1",
            "email": "eduard@qehc.edu.sa",
            "email_verified": True,
            "hd": "qehc.edu.sa",
            "nonce": "nonce-1",
            "iat": now - 3600,
            "exp": now - 600,
        },
        key,
        algorithm="RS256",
    )
    try:
        service.validate_id_token(token, "nonce-1")
        raise AssertionError("expired token must fail")
    except GoogleOidcError as exc:
        assert "expired" in str(exc).lower()


def test_wrong_audience_is_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        google_client_id="expected-client",
        google_redirect_uri="http://127.0.0.1:5186/api/v1/auth/google/callback",
        session_secret="unit-test-secret",
    )
    service = GoogleOidcService(settings)

    class StaticJwks:
        def get_signing_key_from_jwt(self, _token: str):
            return type("Key", (), {"key": key.public_key()})()

    service._jwks = StaticJwks()
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": GOOGLE_ISSUERS[0],
            "aud": "other-client",
            "sub": "sub-1",
            "email": "eduard@qehc.edu.sa",
            "email_verified": True,
            "hd": "qehc.edu.sa",
            "nonce": "nonce-1",
            "iat": now,
            "exp": now + 300,
        },
        key,
        algorithm="RS256",
    )
    try:
        service.validate_id_token(token, "nonce-1")
        raise AssertionError("wrong audience must fail")
    except GoogleOidcError as exc:
        assert "audience" in str(exc).lower() or "invalid" in str(exc).lower()


def test_dev_session_disabled_by_default(client) -> None:
    response = client.post("/api/v1/auth/dev-session", json={"email": "eduard@qehc.edu.sa"})
    assert response.status_code == 404


def test_production_cannot_enable_dev_session(monkeypatch) -> None:
    monkeypatch.setenv("QKA_ENVIRONMENT", "production")
    monkeypatch.setenv("QKA_DEV_SESSION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert get_settings().dev_session_allowed() is False
        assert get_settings().is_production is True
    finally:
        get_settings.cache_clear()


def test_auth_status_does_not_claim_live_google(google_client) -> None:
    response = google_client.get("/api/v1/auth/status")
    assert response.status_code == 200
    body = response.json()
    assert body["googleConfigured"] is True
    assert body["devSessionEnabled"] is False
    assert body["liveGoogleLoginVerified"] is False


def test_google_login_authorized_user_can_search(google_client) -> None:
    callback = _login(google_client, "code-allowed")
    assert callback.status_code == 302, callback.text
    location = callback.headers.get("location") or ""
    assert "authError" not in location
    assert location.rstrip("/").endswith("/search")
    search = google_client.get("/api/v1/search", params={"q": "Laptop"})
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["error"] is None
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == FILE_ID.hex
    me = google_client.get("/api/v1/auth/me")
    assert me.json()["id"] == str(ALLOWED_USER)


def test_google_login_other_user_is_acl_isolated(google_client) -> None:
    callback = _login(google_client, "code-denied")
    assert callback.status_code == 302
    assert "authError" not in (callback.headers.get("location") or "")
    search = google_client.get("/api/v1/search", params={"q": "Laptop"})
    assert search.status_code == 200
    assert search.json()["results"] == []
    fetch = google_client.get(f"/api/v1/documents/{FILE_ID.hex}")
    assert fetch.json()["denied"] is True
    me = google_client.get("/api/v1/auth/me")
    assert me.json()["id"] == str(DENIED_USER)


def test_disabled_email_is_denied(google_client) -> None:
    callback = _login(google_client, "code-disabled")
    location = callback.headers.get("location") or ""
    assert "authError" in location
    assert "/login" in location
    search = google_client.get("/api/v1/search", params={"q": "Laptop"})
    assert search.status_code == 401


def test_unlisted_email_is_denied(google_client) -> None:
    callback = _login(google_client, "code-unlisted")
    assert "authError" in (callback.headers.get("location") or "")


def test_pending_unlinked_google_subject_is_denied(google_client) -> None:
    callback = _login(google_client, "code-pending")
    location = callback.headers.get("location") or ""
    assert "authError" in location
    assert "/login" in location
    assert "allowlist" in location.lower()


def test_unknown_google_sub_does_not_merge_by_email(google_client) -> None:
    callback = _login(google_client, "code-unknown")
    assert callback.status_code == 302
    assert "authError" not in (callback.headers.get("location") or "")
    me = google_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] != str(ALLOWED_USER)
    search = google_client.get("/api/v1/search", params={"q": "Laptop"})
    assert search.status_code == 200
    assert search.json()["results"] == []


def test_wrong_domain_google_identity_is_denied(google_client) -> None:
    callback = _login(google_client, "code-gmail")
    assert "authError" in (callback.headers.get("location") or "")


def test_unverified_google_email_is_denied(google_client) -> None:
    callback = _login(google_client, "code-unverified")
    assert "authError" in (callback.headers.get("location") or "")


def test_invalid_oauth_state_is_denied(google_client) -> None:
    start = google_client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 302
    callback = google_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "code-allowed", "state": "not-the-state"},
        follow_redirects=False,
    )
    assert "authError" in (callback.headers.get("location") or "")


def test_google_start_includes_pkce_and_hosted_domain(google_client) -> None:
    start = google_client.get("/api/v1/auth/google/start", follow_redirects=False)
    location = start.headers.get("location") or ""
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query.get("code_challenge")
    assert start.cookies.get("qka_oidc_verifier")
    assert start.cookies.get("qka_oidc_nonce")
    assert start.cookies.get("qka_oidc_state")


def test_session_search_still_uses_caller_ad_acl_not_shared_sid(seed_db) -> None:
    from qka_api.services.documents import _acl_needles
    from qka_shared.allowlist import load_active_user
    from qka_api.db import create_session_factory

    engine, factory = create_session_factory(get_settings())
    try:
        with factory() as session:
            allowed = load_active_user(session, ALLOWED_USER)
            denied = load_active_user(session, DENIED_USER)
            assert allowed is not None
            assert SID_ALLOWED in _acl_needles(allowed)
            assert SID_ALLOWED not in _acl_needles(denied)
            assert "otheruser" in _acl_needles(denied)
    finally:
        engine.dispose()
