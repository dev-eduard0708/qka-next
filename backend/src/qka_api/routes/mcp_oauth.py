"""MCP OAuth 2.1 discovery + authorize/token/register for ChatGPT Business staging."""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qka_api.auth import COOKIE_NAME, issue_session, read_session, set_session_cookie
from qka_api.deps import get_google_oidc, get_session, get_settings_dep
from qka_api.google_oidc import GoogleOidcError, GoogleOidcService, pkce_pair
from qka_api.identity_bind import bind_verified_google
from qka_api.mcp_oauth import McpOAuthService
from qka_api.services.mcp_session import (
    assert_mcp_token_subject_active,
    google_login_hint,
    record_mcp_connection,
    resolve_reusable_mcp_user,
)
from qka_shared.config import Settings

router = APIRouter(tags=["mcp-oauth"])
log = logging.getLogger("qka.mcp.oauth")


def get_mcp_oauth(request: Request) -> McpOAuthService:
    oauth = getattr(request.app.state, "mcp_oauth", None)
    if oauth is None:
        raise HTTPException(status_code=503, detail="MCP OAuth is not configured.")
    return oauth


def _require_enabled(oauth: McpOAuthService) -> None:
    if not oauth.is_enabled:
        raise HTTPException(status_code=503, detail="MCP OAuth is disabled.")


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{path:path}")
def protected_resource_metadata(oauth: McpOAuthService = Depends(get_mcp_oauth)) -> dict:
    _require_enabled(oauth)
    return {
        "resource": oauth.resource,
        "authorization_servers": [oauth.issuer],
        "scopes_supported": [oauth.documents_scope, "openid", "email", "offline_access"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": oauth.issuer + "/docs/06-CHATGPT-MCP.md",
    }


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def authorization_server_metadata(oauth: McpOAuthService = Depends(get_mcp_oauth)) -> dict:
    _require_enabled(oauth)
    issuer = oauth.issuer
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "revocation_endpoint": issuer + "/oauth/revoke",
        "registration_endpoint": issuer + "/oauth/register",
        "userinfo_endpoint": issuer + "/oauth/userinfo",
        "scopes_supported": [oauth.documents_scope, "openid", "email", "offline_access", "profile"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": True,
    }


@router.get("/oauth/authorize")
def get_authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
    state: str = Query(""),
    resource: str | None = Query(None),
    oauth: McpOAuthService = Depends(get_mcp_oauth),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    _require_enabled(oauth)
    error = _validate_authorize(oauth, client_id, redirect_uri, response_type, code_challenge, code_challenge_method, resource)
    if error:
        return _error_redirect(redirect_uri, error[0], error[1], state, oauth.issuer)

    # Reuse existing QKA Google browser session — no second Google prompt.
    principal = read_session(settings, request.cookies.get(COOKIE_NAME))
    if principal is not None:
        user = resolve_reusable_mcp_user(session, principal.user_id)
        if user is not None:
            auth_code = oauth.create_authorization_code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope or oauth.documents_scope,
                resource=resource or oauth.resource,
                qka_user_id=user.id,
            )
            query = urlencode({"code": auth_code, "state": state or "", "iss": oauth.issuer})
            log.info("mcp.oauth.authorize.session_reuse user_id=%s client_id=%s", user.id, client_id)
            return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)

    google_ready = bool(settings.google_client_id.strip() and settings.google_redirect_uri.strip())
    start_query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": scope or oauth.documents_scope,
            "state": state,
            "resource": resource or oauth.resource,
        }
    )
    # Seamless path: auto-continue to Google (reuses Google SSO cookie when present).
    if google_ready:
        return RedirectResponse(f"/oauth/google/start?{start_query}", status_code=302)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>QKA-Next MCP Sign-in</title></head>
<body style="font-family:Segoe UI,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem">
  <h1>QKA-Next MCP authorization</h1>
  <p>Sign in with your <strong>@qehc.edu.sa</strong> Google Workspace account to authorize ChatGPT.
  Access is per employee. Google-only users do not inherit another employee's Synology ACL.</p>
  <p><strong>Google OIDC is not configured on this API.</strong></p>
  <p style="color:#666;font-size:0.9rem">Client: {client_id}</p>
</body></html>"""
    return HTMLResponse(html)


@router.get("/oauth/google/start")
def google_start(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
    state: str = Query(""),
    resource: str | None = Query(None),
    oauth: McpOAuthService = Depends(get_mcp_oauth),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    oidc: GoogleOidcService = Depends(get_google_oidc),
) -> Response:
    _require_enabled(oauth)
    error = _validate_authorize(oauth, client_id, redirect_uri, "code", code_challenge, code_challenge_method, resource)
    if error:
        return _error_redirect(redirect_uri, error[0], error[1], state, oauth.issuer)
    if not oidc.is_configured():
        return _error_redirect(redirect_uri, "server_error", "Google OIDC is not configured.", state, oauth.issuer)

    # Prefer QKA session reuse even if ChatGPT hit /google/start directly.
    principal = read_session(settings, request.cookies.get(COOKIE_NAME))
    if principal is not None:
        user = resolve_reusable_mcp_user(session, principal.user_id)
        if user is not None:
            auth_code = oauth.create_authorization_code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope or oauth.documents_scope,
                resource=resource or oauth.resource,
                qka_user_id=user.id,
            )
            query = urlencode({"code": auth_code, "state": state or "", "iss": oauth.issuer})
            log.info("mcp.oauth.google_start.session_reuse user_id=%s", user.id)
            return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)

    login_hint = None
    if principal is not None:
        login_hint = google_login_hint(session, principal.user_id)

    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24)
    pending = oauth.create_pending(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope or oauth.documents_scope,
        resource=resource or oauth.resource,
        downstream_state=state,
        google_nonce=nonce,
        google_verifier=verifier,
    )
    redirect = settings.mcp_issuer + settings.mcp_google_redirect_path
    # No prompt=select_account: reuse existing Google Workspace browser session when present.
    url = oidc.build_authorization_url(
        pending.state,
        nonce,
        challenge,
        redirect,
        prompt=None,
        login_hint=login_hint,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/oauth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oauth: McpOAuthService = Depends(get_mcp_oauth),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    oidc: GoogleOidcService = Depends(get_google_oidc),
) -> Response:
    _require_enabled(oauth)
    pending = oauth.consume_pending(state or "")
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    if error or not code:
        return _error_redirect(
            pending.redirect_uri,
            "access_denied",
            error or "Google sign-in was cancelled.",
            pending.downstream_state,
            oauth.issuer,
        )
    try:
        identity = oidc.redeem(
            code,
            pending.google_nonce or "",
            pending.google_verifier or "",
            settings.mcp_issuer + settings.mcp_google_redirect_path,
        )
    except GoogleOidcError as exc:
        return _error_redirect(pending.redirect_uri, "access_denied", str(exc), pending.downstream_state, oauth.issuer)
    allow_write = not settings.database_read_only
    bound = bind_verified_google(session, identity, settings.google_hosted_domain, allow_write=allow_write)
    session.commit()
    if not bound.succeeded or bound.user_id is None:
        return _error_redirect(
            pending.redirect_uri,
            "access_denied",
            bound.reason,
            pending.downstream_state,
            oauth.issuer,
        )
    # Gate on active QKA + verified Google subject binding (never email-alone).
    deny = assert_mcp_token_subject_active(session, bound.user_id)
    if deny:
        return _error_redirect(pending.redirect_uri, "access_denied", deny, pending.downstream_state, oauth.issuer)

    auth_code = oauth.create_authorization_code(
        client_id=pending.client_id,
        redirect_uri=pending.redirect_uri,
        code_challenge=pending.code_challenge,
        code_challenge_method=pending.code_challenge_method,
        scope=pending.scope,
        resource=pending.resource,
        qka_user_id=bound.user_id,
    )
    query = urlencode(
        {
            "code": auth_code,
            "state": pending.downstream_state,
            "iss": oauth.issuer,
        }
    )
    redirect = RedirectResponse(f"{pending.redirect_uri}?{query}", status_code=302)
    # Establish QKA session so a later ChatGPT Connect in the same browser skips Google.
    set_session_cookie(redirect, settings, issue_session(settings, bound.user_id))
    log.info("mcp.oauth.google_callback.ok user_id=%s", bound.user_id)
    return redirect


@router.post("/oauth/token")
async def post_token(
    request: Request,
    oauth: McpOAuthService = Depends(get_mcp_oauth),
    session: Session = Depends(get_session),
) -> Response:
    _require_enabled(oauth)
    form = dict(await request.form())
    grant = str(form.get("grant_type") or "")
    if grant == "authorization_code":
        code = str(form.get("code") or "")
        redirect_uri = str(form.get("redirect_uri") or "")
        client_id = str(form.get("client_id") or "")
        code_verifier = str(form.get("code_verifier") or "")
        resource = form.get("resource")
        row = oauth.consume_authorization_code(code)
        if row is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if row.client_id != client_id or row.redirect_uri != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if not oauth.verify_pkce(code_verifier, row.code_challenge, row.code_challenge_method):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
        if resource and not oauth.is_resource_allowed(str(resource)):
            return JSONResponse({"error": "invalid_target"}, status_code=400)
        deny = assert_mcp_token_subject_active(session, row.qka_user_id)
        if deny:
            return JSONResponse({"error": "invalid_grant", "error_description": deny}, status_code=400)
        include_refresh = "offline_access" in row.scope.split()
        access, refresh, expires = oauth.issue_tokens(
            client_id=client_id,
            qka_user_id=row.qka_user_id,
            scope=row.scope,
            resource=str(resource) if resource else row.resource,
            include_refresh=include_refresh,
        )
        record_mcp_connection(session, row.qka_user_id)
        session.commit()
        body = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": expires,
            "scope": row.scope,
            "resource": oauth.normalize_resource(str(resource) if resource else row.resource),
        }
        if refresh:
            body["refresh_token"] = refresh
        return JSONResponse(body)
    if grant == "refresh_token":
        refresh_token = str(form.get("refresh_token") or "")
        client_id = str(form.get("client_id") or "")
        resource = form.get("resource")
        row = oauth.consume_refresh_token(refresh_token)
        if row is None or row.client_id != client_id:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Refresh token is invalid or revoked."},
                status_code=400,
            )
        if resource and not oauth.is_resource_allowed(str(resource)):
            return JSONResponse({"error": "invalid_target"}, status_code=400)
        deny = assert_mcp_token_subject_active(session, row.qka_user_id)
        if deny:
            oauth.revoke_tokens_for_user(row.qka_user_id)
            return JSONResponse({"error": "invalid_grant", "error_description": deny}, status_code=400)
        access, new_refresh, expires = oauth.issue_tokens(
            client_id=client_id,
            qka_user_id=row.qka_user_id,
            scope=row.scope,
            resource=str(resource) if resource else row.resource,
            include_refresh=True,
        )
        record_mcp_connection(session, row.qka_user_id)
        session.commit()
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": expires,
                "refresh_token": new_refresh,
                "scope": row.scope,
                "resource": oauth.normalize_resource(str(resource) if resource else row.resource),
            }
        )
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


@router.post("/oauth/revoke")
async def post_revoke(
    request: Request,
    oauth: McpOAuthService = Depends(get_mcp_oauth),
) -> Response:
    """RFC 7009 token revocation (refresh or access). Always returns 200 when OAuth is enabled."""
    _require_enabled(oauth)
    form = dict(await request.form())
    token = str(form.get("token") or "")
    hint = str(form.get("token_type_hint") or "").lower()
    if token:
        if hint == "access_token":
            oauth.revoke_access_token(token) or oauth.revoke_refresh_token(token)
        else:
            oauth.revoke_refresh_token(token) or oauth.revoke_access_token(token)
    return Response(status_code=200)


class RegisterBody(BaseModel):
    redirect_uris: list[str] = Field(default_factory=list)
    token_endpoint_auth_method: str = "none"
    client_name: str | None = None


@router.post("/oauth/register")
def post_register(body: RegisterBody, oauth: McpOAuthService = Depends(get_mcp_oauth)) -> Response:
    _require_enabled(oauth)
    try:
        client = oauth.register_client(body.redirect_uris, body.token_endpoint_auth_method)
    except ValueError as exc:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": client.client_id_issued_at,
            "redirect_uris": client.redirect_uris,
            "token_endpoint_auth_method": client.token_endpoint_auth_method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


@router.get("/oauth/userinfo")
def userinfo(request: Request, oauth: McpOAuthService = Depends(get_mcp_oauth)) -> Response:
    _require_enabled(oauth)
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    token = oauth.validate_access_token(auth[7:].strip())
    if token is None or not token.subject:
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return JSONResponse({"sub": token.subject, "iss": oauth.issuer})


def _validate_authorize(
    oauth: McpOAuthService,
    client_id: str,
    redirect_uri: str,
    response_type: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str | None,
) -> tuple[str, str] | None:
    if response_type != "code":
        return ("unsupported_response_type", "Only response_type=code is supported.")
    if code_challenge_method.upper() != "S256" or not code_challenge:
        return ("invalid_request", "PKCE S256 is required.")
    if not oauth.is_redirect_uri_allowed(redirect_uri):
        return ("invalid_request", "redirect_uri is not allowed.")
    if oauth.find_client(client_id) is None:
        return ("unauthorized_client", "Unknown client_id.")
    if not oauth.is_resource_allowed(resource):
        return ("invalid_target", "resource is not allowed.")
    return None


def _error_redirect(redirect_uri: str, error: str, description: str, state: str, issuer: str) -> RedirectResponse:
    query = urlencode({"error": error, "error_description": description, "state": state or "", "iss": issuer})
    return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)
