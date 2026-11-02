from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from qka_shared.config import Settings

COOKIE_NAME = "qka_next_session"
CSRF_COOKIE = "qka_next_csrf"
CSRF_HEADER = "X-CSRF-Token"
OIDC_STATE_COOKIE = "qka_oidc_state"
OIDC_NONCE_COOKIE = "qka_oidc_nonce"
OIDC_VERIFIER_COOKIE = "qka_oidc_verifier"
OIDC_REDIRECT_COOKIE = "qka_oidc_redirect"
OIDC_COOKIE_MAX_AGE = 600


@dataclass(frozen=True)
class SessionPrincipal:
    user_id: uuid.UUID
    exp: int


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{digest}"


def issue_session(settings: Settings, user_id: uuid.UUID) -> str:
    payload = json.dumps(
        {
            "sub": str(user_id),
            "exp": int(time.time()) + settings.session_hours * 3600,
        },
        separators=(",", ":"),
    )
    return _sign(settings.session_secret, payload)


def read_session(settings: Settings, token: str | None) -> SessionPrincipal | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(settings.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(payload)
        user_id = uuid.UUID(str(data["sub"]))
        exp = int(data["exp"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if exp < int(time.time()):
        return None
    if user_id == uuid.UUID(int=0):
        return None
    return SessionPrincipal(user_id=user_id, exp=exp)


def _cookie_kwargs(settings: Settings, httponly: bool, max_age: int | None = None) -> dict:
    kwargs: dict = {
        "httponly": httponly,
        "samesite": "lax",
        "secure": settings.cookie_secure or settings.is_production,
        "path": "/",
    }
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


def set_session_cookie(response: Response, settings: Settings, token: str) -> str:
    response.set_cookie(COOKIE_NAME, token, **_cookie_kwargs(settings, httponly=True))
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, csrf, **_cookie_kwargs(settings, httponly=False))
    return csrf


def clear_session_cookie(response: Response, settings: Settings) -> None:
    empty = _cookie_kwargs(settings, httponly=True)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="lax", secure=empty["secure"])
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", secure=empty["secure"])


def set_oidc_cookies(
    response: Response,
    settings: Settings,
    *,
    state: str,
    nonce: str,
    verifier: str,
    redirect_uri: str,
) -> None:
    kwargs = _cookie_kwargs(settings, httponly=True, max_age=OIDC_COOKIE_MAX_AGE)
    response.set_cookie(OIDC_STATE_COOKIE, state, **kwargs)
    response.set_cookie(OIDC_NONCE_COOKIE, nonce, **kwargs)
    response.set_cookie(OIDC_VERIFIER_COOKIE, verifier, **kwargs)
    response.set_cookie(OIDC_REDIRECT_COOKIE, redirect_uri, **kwargs)


def clear_oidc_cookies(response: Response, settings: Settings) -> None:
    secure = settings.cookie_secure or settings.is_production
    for name in (OIDC_STATE_COOKIE, OIDC_NONCE_COOKIE, OIDC_VERIFIER_COOKIE, OIDC_REDIRECT_COOKIE):
        response.delete_cookie(name, path="/", samesite="lax", secure=secure)


def require_principal(request: Request, settings: Settings) -> SessionPrincipal:
    token = request.cookies.get(COOKIE_NAME)
    principal = read_session(settings, token)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    if request.query_params.get("userId") or request.headers.get("X-QKA-User-Id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Caller-supplied user identity is not accepted.",
        )
    return principal


def require_csrf(request: Request) -> None:
    header = request.headers.get(CSRF_HEADER) or ""
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")
