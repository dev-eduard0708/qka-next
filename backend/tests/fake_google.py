from __future__ import annotations

from qka_api.google_oidc import GoogleOidcError
from qka_shared.google_oidc import GoogleOidcIdentity


class FakeGoogleOidcService:
    def __init__(self, identities_by_code: dict[str, GoogleOidcIdentity], configured: bool = True):
        self.identities_by_code = identities_by_code
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def hosted_domain(self) -> str:
        return "qehc.edu.sa"

    def build_authorization_url(self, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?state={state}&nonce={nonce}&code_challenge={code_challenge}&redirect_uri={redirect_uri}"
        )

    def redeem(self, code: str, expected_nonce: str, code_verifier: str, redirect_uri: str) -> GoogleOidcIdentity:
        if not expected_nonce or not code_verifier:
            raise GoogleOidcError("Google authorization response is incomplete.")
        identity = self.identities_by_code.get(code)
        if identity is None:
            raise GoogleOidcError("Unknown Google authorization code.")
        return identity
