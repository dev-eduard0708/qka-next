from __future__ import annotations

from dataclasses import dataclass

from qka_shared.email_address import ALLOWED_DOMAIN, try_normalize


@dataclass(frozen=True)
class GoogleOidcIdentity:
    sub: str
    email: str
    email_verified: bool
    hosted_domain: str | None
    name: str | None = None


def accept_google_identity(identity: GoogleOidcIdentity, hosted_domain: str) -> tuple[str | None, str]:
    if identity is None or not (identity.sub or "").strip():
        return None, "Google subject is missing."
    if not identity.email_verified:
        return None, "Google email is not verified."
    expected_hd = (hosted_domain or ALLOWED_DOMAIN).strip() or ALLOWED_DOMAIN
    actual_hd = (identity.hosted_domain or "").strip()
    if actual_hd.lower() != expected_hd.lower():
        return None, f"Google hosted domain must be {expected_hd}."
    email, error = try_normalize(identity.email)
    if email is None:
        return None, error
    return email, ""
