from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_shared.email_address import try_normalize
from qka_shared.models import AllowedEmail, ExternalIdentity, QkaUser

LINKED = 2
ACCOUNT_ENABLED = 0
USER_ACTIVE = 0
PROVIDER_AD = 0
PROVIDER_GOOGLE = 1


@dataclass(frozen=True)
class EmailAccess:
    allowed: bool
    reason: str


def deny_message(reason: str) -> str:
    if reason == "no_verified_email":
        return "Verified email is not available for this identity."
    if reason == "disabled":
        return "Access for this email is disabled."
    return "Access denied."


def evaluate_user(session: Session, user_id: uuid.UUID) -> EmailAccess:
    emails = load_verified_emails(session, user_id)
    if not emails:
        return EmailAccess(False, "no_verified_email")
    rows = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized.in_(emails))
    ).scalars().all()
    if not rows:
        return EmailAccess(False, "unlisted")
    if not any(row.enabled for row in rows):
        return EmailAccess(False, "disabled")
    return EmailAccess(True, "allowed")


def load_verified_emails(session: Session, user_id: uuid.UUID) -> set[str]:
    identities = session.execute(
        select(ExternalIdentity)
        .options(selectinload(ExternalIdentity.google))
        .where(
            ExternalIdentity.qka_user_id == user_id,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
        )
    ).scalars().all()
    emails: set[str] = set()
    for identity in identities:
        normalized, _ = try_normalize(identity.primary_email)
        if normalized:
            emails.add(normalized)
        if identity.google is not None:
            gnorm, _ = try_normalize(identity.google.primary_email)
            if gnorm:
                emails.add(gnorm)
    return emails


def load_active_user(session: Session, user_id: uuid.UUID) -> QkaUser | None:
    return session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .where(
            QkaUser.id == user_id,
            QkaUser.status == USER_ACTIVE,
            QkaUser.login_enabled.is_(True),
        )
    ).scalar_one_or_none()
