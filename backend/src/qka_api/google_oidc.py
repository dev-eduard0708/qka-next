from __future__ import annotations

import base64
import hashlib
import secrets
import logging
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from qka_shared.config import Settings
from qka_shared.google_oidc import GoogleOidcIdentity, accept_google_identity

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
DISCOVERY_PATH = "/.well-known/openid-configuration"
log = logging.getLogger("qka.oidc")


class GoogleOidcError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class GoogleOidcService:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, jwks: PyJWKClient | None = None):
        self.settings = settings
        self._http = http
        self._jwks = jwks
        self._discovery: dict | None = None

    def is_configured(self) -> bool:
        return bool(self.settings.google_client_id.strip() and self.settings.google_redirect_uri.strip())

    def hosted_domain(self) -> str:
        return (self.settings.google_hosted_domain or "qehc.edu.sa").strip() or "qehc.edu.sa"

    def build_authorization_url(
        self,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        *,
        prompt: str | None = "select_account",
        login_hint: str | None = None,
    ) -> str:
        if not self.is_configured():
            raise GoogleOidcError("Google Workspace sign-in is not configured.")
        query = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "hd": self.hosted_domain(),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        # Omit prompt for MCP seamless reuse of an existing Google browser session.
        if prompt:
            query["prompt"] = prompt
        if login_hint and login_hint.strip():
            query["login_hint"] = login_hint.strip()
        return f"{self._authorization_endpoint()}?{urlencode(query)}"

    def redeem(self, code: str, expected_nonce: str, code_verifier: str, redirect_uri: str) -> GoogleOidcIdentity:
        if not self.is_configured():
            raise GoogleOidcError("Google Workspace sign-in is not configured.")
        if not code.strip() or not expected_nonce.strip() or not code_verifier.strip():
            raise GoogleOidcError("Google authorization response is incomplete.")
        token_response = self._exchange_code(code, code_verifier, redirect_uri)
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token.strip():
            raise GoogleOidcError("Google token response did not include an ID token.")
        claims = self.validate_id_token(id_token, expected_nonce)
        identity = GoogleOidcIdentity(
            sub=str(claims.get("sub") or ""),
            email=str(claims.get("email") or ""),
            email_verified=_as_bool(claims.get("email_verified")),
            hosted_domain=_optional_str(claims.get("hd")),
            name=_optional_str(claims.get("name")),
        )
        _, error = accept_google_identity(identity, self.hosted_domain())
        if error:
            raise GoogleOidcError(error)
        log.info(
            "google_oidc.claims_ok issuer_ok=%s audience_ok=true email_verified=%s hosted_domain_ok=true sub_present=%s",
            claims.get("iss") in GOOGLE_ISSUERS,
            identity.email_verified,
            bool(identity.sub.strip()),
        )
        return identity

    def validate_id_token(self, id_token: str, expected_nonce: str) -> dict:
        signing_key = self._jwks_client().get_signing_key_from_jwt(id_token)
        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.google_client_id,
                issuer=GOOGLE_ISSUERS,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
                leeway=120,
            )
        except jwt.ExpiredSignatureError as exc:
            raise GoogleOidcError("Google ID token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise GoogleOidcError("Google ID token audience is invalid.") from exc
        except jwt.InvalidIssuerError as exc:
            raise GoogleOidcError("Google ID token issuer is invalid.") from exc
        except jwt.InvalidTokenError as exc:
            raise GoogleOidcError("Google ID token signature or claims are invalid.") from exc
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise GoogleOidcError("Google ID token nonce mismatch.")
        return claims

    def _exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> dict:
        payload = {
            "code": code,
            "client_id": self.settings.google_client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        secret = self.settings.google_client_secret.strip()
        if secret:
            payload["client_secret"] = secret
        try:
            response = self._client().post(self._token_endpoint(), data=payload, timeout=20.0)
        except httpx.HTTPError as exc:
            raise GoogleOidcError("Google token exchange failed.") from exc
        if response.status_code >= 400:
            raise GoogleOidcError("Google token exchange failed.")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOidcError("Google token exchange failed.") from exc
        if not isinstance(body, dict):
            raise GoogleOidcError("Google token exchange failed.")
        return body

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client()
        return self._http

    def _jwks_client(self) -> PyJWKClient:
        if self._jwks is None:
            jwks_uri = str(self._discovery_doc().get("jwks_uri") or "https://www.googleapis.com/oauth2/v3/certs")
            self._jwks = PyJWKClient(jwks_uri)
        return self._jwks

    def _authorization_endpoint(self) -> str:
        return str(self._discovery_doc().get("authorization_endpoint") or "https://accounts.google.com/o/oauth2/v2/auth")

    def _token_endpoint(self) -> str:
        return str(self._discovery_doc().get("token_endpoint") or "https://oauth2.googleapis.com/token")

    def _discovery_doc(self) -> dict:
        if self._discovery is not None:
            return self._discovery
        url = self.settings.google_authority.rstrip("/") + DISCOVERY_PATH
        try:
            response = self._client().get(url, timeout=20.0)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            self._discovery = {}
            return self._discovery
        self._discovery = body if isinstance(body, dict) else {}
        return self._discovery


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1"}
    return False


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
