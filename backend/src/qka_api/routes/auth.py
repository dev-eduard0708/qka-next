from __future__ import annotations

import hmac
import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_api.auth import (
    CSRF_COOKIE,
    OIDC_NONCE_COOKIE,
    OIDC_REDIRECT_COOKIE,
    OIDC_STATE_COOKIE,
    OIDC_VERIFIER_COOKIE,
    clear_oidc_cookies,
    clear_session_cookie,
    issue_session,
    require_csrf,
    require_principal,
    set_oidc_cookies,
    set_session_cookie,
)
from qka_api.deps import get_google_oidc, get_session, get_settings_dep
from qka_api.google_oidc import GoogleOidcError, GoogleOidcService, pkce_pair
from qka_api.identity_bind import bind_verified_google
from qka_api.local_login import authenticate_local
from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, deny_message, evaluate_user, load_active_user
from qka_shared.config import Settings
from qka_shared.email_address import try_normalize
from qka_shared.models import ExternalIdentity, QkaUser

router = APIRouter(prefix="/api/v1/auth")
compat_router = APIRouter()
log = logging.getLogger("qka.auth")


class DevSessionRequest(BaseModel):
    email: str


class PasswordLoginRequest(BaseModel):
    username: str
    password: str


def _user_payload(user: QkaUser, access_allowed: bool | None = None) -> dict:
    payload = {
        "id": str(user.id),
        "displayName": user.display_name,
        "superAdmin": user.is_super_admin,
        "directoryOperator": user.is_directory_operator,
    }
    if access_allowed is not None:
        payload["allowlist"] = access_allowed
    return payload


def _frontend_origin(settings: Settings) -> str:
    origin = (settings.frontend_origin or "").strip() or "http://127.0.0.1:5186"
    return origin.rstrip("/")


def _safe_frontend_redirect(settings: Settings, path: str) -> str:
    origin = _frontend_origin(settings)
    if not path.startswith("/"):
        path = "/"
    return origin + path


@router.get("/status")
def auth_status(
    settings: Settings = Depends(get_settings_dep),
    oidc: GoogleOidcService = Depends(get_google_oidc),
) -> dict:
    return {
        "googleConfigured": oidc.is_configured(),
        "googleHostedDomain": oidc.hosted_domain(),
        "devSessionEnabled": settings.dev_session_allowed(),
        "liveGoogleLoginVerified": False,
    }


@router.get("/google/start")
def google_start(
    settings: Settings = Depends(get_settings_dep),
    oidc: GoogleOidcService = Depends(get_google_oidc),
):
    if not oidc.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Workspace sign-in is not configured. See docs/05-GOOGLE-OIDC.md.",
        )
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    redirect_uri = settings.google_redirect_uri.strip()
    url = oidc.build_authorization_url(state, nonce, challenge, redirect_uri)
    redirect = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    set_oidc_cookies(
        redirect,
        settings,
        state=state,
        nonce=nonce,
        verifier=verifier,
        redirect_uri=redirect_uri,
    )
    return redirect


@router.get("/google/callback")
def google_callback(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    oidc: GoogleOidcService = Depends(get_google_oidc),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    def fail(message: str, log_reason: str) -> RedirectResponse:
        log.warning("google_oidc.denied reason=%s", log_reason)
        clear_oidc_cookies(response, settings)
        redirect = RedirectResponse(
            _safe_frontend_redirect(settings, "/login?authError=" + quote(message)),
            status_code=status.HTTP_302_FOUND,
        )
        clear_oidc_cookies(redirect, settings)
        return redirect

    if error:
        return fail("Google sign-in was cancelled or denied.", "google_error")
    expected_state = request.cookies.get(OIDC_STATE_COOKIE) or ""
    nonce = request.cookies.get(OIDC_NONCE_COOKIE) or ""
    verifier = request.cookies.get(OIDC_VERIFIER_COOKIE) or ""
    redirect_uri = request.cookies.get(OIDC_REDIRECT_COOKIE) or settings.google_redirect_uri.strip()
    if not code or not state or not expected_state or not hmac.compare_digest(state, expected_state):
        return fail("Google sign-in state is invalid.", "state")
    if not nonce or not verifier:
        return fail("Google sign-in session expired. Try again.", "oidc_cookies")

    try:
        identity = oidc.redeem(code, nonce, verifier, redirect_uri)
    except GoogleOidcError as exc:
        return fail(str(exc), "redeem")

    bound = bind_verified_google(
        session,
        identity,
        oidc.hosted_domain(),
        allow_write=not settings.database_read_only,
    )
    if not bound.succeeded or bound.user_id is None:
        return fail(bound.reason, bound.reason)

    access = evaluate_user(session, bound.user_id)
    if not access.allowed:
        return fail(deny_message(access.reason), access.reason)
    user = load_active_user(session, bound.user_id)
    if user is None:
        return fail("The linked QKA account is not active.", "inactive")

    token = issue_session(settings, user.id)
    redirect = RedirectResponse(_safe_frontend_redirect(settings, "/search"), status_code=status.HTTP_302_FOUND)
    set_session_cookie(redirect, settings, token)
    clear_oidc_cookies(redirect, settings)
    log.info("google_oidc.login user_id=%s", user.id)
    return redirect


@compat_router.get("/auth/google/callback")
def google_callback_registered(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    oidc: GoogleOidcService = Depends(get_google_oidc),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    return google_callback(request, response, settings, session, oidc, code, state, error)


@router.post("/login")
def password_login(
    body: PasswordLoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    if request.cookies.get(CSRF_COOKIE):
        require_csrf(request)
    user = authenticate_local(session, body.username, body.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    token = issue_session(settings, user.id)
    set_session_cookie(response, settings, token)
    log.info("local_login.ok user_id=%s", user.id)
    return _user_payload(user)


@router.post("/dev-session")
def create_dev_session(
    body: DevSessionRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    if not settings.dev_session_allowed():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DEV session is disabled.")
    if request.cookies.get(CSRF_COOKIE):
        require_csrf(request)
    email, error = try_normalize(body.email)
    if email is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    identity = session.execute(
        select(ExternalIdentity)
        .options(selectinload(ExternalIdentity.google), selectinload(ExternalIdentity.user))
        .where(
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
            ExternalIdentity.qka_user_id.is_not(None),
        )
    ).scalars().all()
    matched = None
    for item in identity:
        primary, _ = try_normalize(item.primary_email)
        google = None
        if item.google is not None:
            google, _ = try_normalize(item.google.primary_email)
        if email in {primary, google}:
            matched = item
            break
    if matched is None or matched.qka_user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    access = evaluate_user(session, matched.qka_user_id)
    if not access.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=deny_message(access.reason))
    user = load_active_user(session, matched.qka_user_id)
    if user is None or not user.login_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    token = issue_session(settings, user.id)
    set_session_cookie(response, settings, token)
    return _user_payload(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    if request.cookies.get(CSRF_COOKIE):
        require_csrf(request)
    clear_session_cookie(response, settings)
    return {"ok": True}


@router.get("/me")
def me(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    access = evaluate_user(session, user.id)
    payload = _user_payload(user, access.allowed)
    payload["allowlistReason"] = access.reason
    return payload
