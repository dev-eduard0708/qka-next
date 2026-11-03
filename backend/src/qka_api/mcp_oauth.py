"""MCP OAuth 2.1 (authorization code + PKCE S256 + refresh) for QKA-Next staging.

In-memory single-process store modeled on the legacy .NET McpOAuthTokenService.
Tokens bind to an individual QkaUser id — never a shared pilot BoundUserId for Google users.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken

from qka_shared.config import Settings

CHATGPT_REDIRECT_PREFIXES = (
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/",
    "http://127.0.0.1:",
    "http://localhost:",
    "https://localhost",
)


@dataclass
class ClientRegistration:
    client_id: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str = "none"
    client_id_issued_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    resource: str
    qka_user_id: uuid.UUID
    expires_at: float


@dataclass
class RefreshRecord:
    token: str
    client_id: str
    qka_user_id: uuid.UUID
    scope: str
    resource: str
    expires_at: float | None


@dataclass
class AccessRecord:
    token: str
    client_id: str
    qka_user_id: uuid.UUID
    scope: str
    resource: str
    expires_at: float | None


@dataclass
class PendingAuthorization:
    state: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    resource: str
    downstream_state: str
    expires_at: float
    google_nonce: str | None = None
    google_verifier: str | None = None


class McpOAuthService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._clients: dict[str, ClientRegistration] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessRecord] = {}
        self._refresh: dict[str, RefreshRecord] = {}
        self._pending: dict[str, PendingAuthorization] = {}
        self._stats = {
            "tokenIssues": 0,
            "tokenValidations": 0,
            "tokenValidationFailures": 0,
            "lastTokenIssuedAt": None,
            "lastValidationAt": None,
            "lastValidationError": None,
        }

    @property
    def is_enabled(self) -> bool:
        return bool(
            self.settings.mcp_oauth_enabled
            and self.settings.mcp_issuer
            and (
                self.settings.google_client_id.strip()
                and self.settings.google_client_secret.strip()
                or self.settings.mcp_inspector_bearer.strip()
            )
        )

    @property
    def issuer(self) -> str:
        return self.settings.mcp_issuer

    @property
    def resource(self) -> str:
        return self.settings.mcp_resource

    @property
    def documents_scope(self) -> str:
        return self.settings.mcp_documents_scope or "mcp:documents.read"

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def is_redirect_uri_allowed(self, redirect_uri: str) -> bool:
        if not redirect_uri or not redirect_uri.strip():
            return False
        try:
            uri = urlparse(redirect_uri)
        except Exception:
            return False
        if uri.scheme not in {"https", "http"}:
            return False
        if uri.scheme == "http" and uri.hostname not in {"127.0.0.1", "localhost"}:
            return False
        return any(redirect_uri.startswith(prefix) for prefix in CHATGPT_REDIRECT_PREFIXES)

    def is_client_id_metadata_allowed(self, client_id: str) -> bool:
        try:
            uri = urlparse(client_id)
        except Exception:
            return False
        if uri.scheme != "https" or not uri.hostname:
            return False
        allowed = {"chatgpt.com", "www.chatgpt.com", "openai.com"}
        return uri.hostname.lower() in allowed or uri.hostname.lower().endswith(".openai.com")

    def is_resource_allowed(self, resource: str | None) -> bool:
        if not resource or not resource.strip():
            return True
        value = resource.strip()
        if value in {self.resource, self.issuer}:
            return True
        return value in self.settings.mcp_additional_resources_list

    def normalize_resource(self, resource: str | None) -> str:
        return (resource or "").strip() or self.resource

    def register_client(self, redirect_uris: list[str], token_endpoint_auth_method: str = "none") -> ClientRegistration:
        if not redirect_uris or any(not self.is_redirect_uri_allowed(u) for u in redirect_uris):
            raise ValueError("redirect_uri is not allowed")
        client_id = f"qka-next-{secrets.token_urlsafe(18)}"
        row = ClientRegistration(
            client_id=client_id,
            redirect_uris=list(redirect_uris),
            token_endpoint_auth_method=token_endpoint_auth_method or "none",
        )
        self._clients[client_id] = row
        return row

    def find_client(self, client_id: str) -> ClientRegistration | None:
        if client_id in self._clients:
            return self._clients[client_id]
        if self.is_client_id_metadata_allowed(client_id):
            # CIMD: accept ChatGPT metadata client ids without prior DCR.
            row = ClientRegistration(client_id=client_id, redirect_uris=list(CHATGPT_REDIRECT_PREFIXES[:2]))
            self._clients[client_id] = row
            return row
        return None

    def create_pending(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        resource: str,
        downstream_state: str,
        google_nonce: str | None = None,
        google_verifier: str | None = None,
    ) -> PendingAuthorization:
        state = secrets.token_urlsafe(24)
        pending = PendingAuthorization(
            state=state,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            resource=self.normalize_resource(resource),
            downstream_state=downstream_state,
            expires_at=time.time() + 600,
            google_nonce=google_nonce,
            google_verifier=google_verifier,
        )
        self._pending[state] = pending
        return pending

    def consume_pending(self, state: str) -> PendingAuthorization | None:
        pending = self._pending.pop(state, None)
        if pending is None or pending.expires_at < time.time():
            return None
        return pending

    def create_authorization_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        resource: str,
        qka_user_id: uuid.UUID,
    ) -> str:
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            resource=self.normalize_resource(resource),
            qka_user_id=qka_user_id,
            expires_at=time.time() + 300,
        )
        return code

    def consume_authorization_code(self, code: str) -> AuthorizationCode | None:
        row = self._codes.pop(code, None)
        if row is None or row.expires_at < time.time():
            return None
        return row

    def verify_pkce(self, code_verifier: str, code_challenge: str, method: str) -> bool:
        if method.upper() != "S256" or not code_verifier or not code_challenge:
            return False
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return hmac.compare_digest(expected, code_challenge)

    def issue_tokens(
        self,
        *,
        client_id: str,
        qka_user_id: uuid.UUID,
        scope: str,
        resource: str,
        include_refresh: bool,
    ) -> tuple[str, str | None, int]:
        # Company-domain Google login once: access tokens do not time-expire.
        # Clients that require expires_in still get a large value (10 years).
        # Tokens remain in-memory — an API process restart still requires re-login.
        access_seconds = int(getattr(self.settings, "mcp_oauth_access_token_seconds", 0) or 0)
        refresh_seconds = int(getattr(self.settings, "mcp_oauth_refresh_token_seconds", 0) or 0)
        access = secrets.token_urlsafe(32)
        now = time.time()
        if access_seconds <= 0:
            access_expires_at: float | None = None
            expires_in = 315360000  # 10y sentinel for OAuth clients
        else:
            access_expires_at = now + access_seconds
            expires_in = access_seconds
        self._access[access] = AccessRecord(
            token=access,
            client_id=client_id,
            qka_user_id=qka_user_id,
            scope=scope,
            resource=self.normalize_resource(resource),
            expires_at=access_expires_at,
        )
        refresh = None
        if include_refresh:
            refresh = secrets.token_urlsafe(32)
            if refresh_seconds <= 0:
                refresh_expires_at: float | None = None
            else:
                refresh_expires_at = now + refresh_seconds
            self._refresh[refresh] = RefreshRecord(
                token=refresh,
                client_id=client_id,
                qka_user_id=qka_user_id,
                scope=scope,
                resource=self.normalize_resource(resource),
                expires_at=refresh_expires_at,
            )
        self._stats["tokenIssues"] = int(self._stats["tokenIssues"]) + 1
        self._stats["lastTokenIssuedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return access, refresh, expires_in

    def consume_refresh_token(self, refresh_token: str) -> RefreshRecord | None:
        row = self._refresh.pop(refresh_token, None)
        if row is None:
            return None
        if row.expires_at is not None and row.expires_at < time.time():
            return None
        return row

    def validate_access_token(self, token: str) -> AccessToken | None:
        self._stats["tokenValidations"] = int(self._stats["tokenValidations"]) + 1
        self._stats["lastValidationAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not token:
            self._fail_validation("missing_token")
            return None
        inspector = (self.settings.mcp_inspector_bearer or "").strip()
        if inspector and hmac.compare_digest(token, inspector):
            user_id = self.settings.mcp_inspector_user_id
            if user_id is None:
                self._fail_validation("inspector_user_not_configured")
                return None
            return AccessToken(
                token=token,
                client_id="qka-next-inspector",
                scopes=[self.documents_scope, "openid", "email"],
                expires_at=None,
                resource=self.resource,
                subject=str(user_id),
                claims={"qka_user_id": str(user_id), "auth": "inspector"},
            )
        row = self._access.get(token)
        if row is None:
            self._fail_validation("invalid_or_expired")
            return None
        if row.expires_at is not None and row.expires_at < time.time():
            self._fail_validation("invalid_or_expired")
            return None
        if not self.is_resource_allowed(row.resource):
            self._fail_validation("resource_mismatch")
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=[s for s in row.scope.split() if s],
            expires_at=None if row.expires_at is None else int(row.expires_at),
            resource=row.resource,
            subject=str(row.qka_user_id),
            claims={"qka_user_id": str(row.qka_user_id), "auth": "oauth"},
        )

    def _fail_validation(self, reason: str) -> None:
        self._stats["tokenValidationFailures"] = int(self._stats["tokenValidationFailures"]) + 1
        self._stats["lastValidationError"] = reason


class QkaTokenVerifier:
    def __init__(self, oauth: McpOAuthService):
        self.oauth = oauth

    async def verify_token(self, token: str) -> AccessToken | None:
        return self.oauth.validate_access_token(token)
