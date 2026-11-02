from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_GOOGLE, USER_ACTIVE
from qka_shared.google_oidc import GoogleOidcIdentity, accept_google_identity
from qka_shared.models import AllowedEmail, ExternalIdentity, GoogleWorkspaceAccount, QkaUser

UNMATCHED = 0


@dataclass(frozen=True)
class BindResult:
    succeeded: bool
    user_id: uuid.UUID | None
    reason: str


def bind_verified_google(
    session: Session,
    identity: GoogleOidcIdentity,
    hosted_domain: str,
    *,
    allow_write: bool,
) -> BindResult:
    email, error = accept_google_identity(identity, hosted_domain)
    if email is None:
        return BindResult(False, None, error)

    sub = identity.sub.strip()
    existing = session.execute(
        select(ExternalIdentity)
        .options(selectinload(ExternalIdentity.google), selectinload(ExternalIdentity.user))
        .where(
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.stable_external_id == sub,
        )
    ).scalar_one_or_none()

    if not _email_allowlisted(session, email):
        if existing is None and allow_write:
            _create_pending_google_identity(session, sub, email)
        return BindResult(
            False,
            None,
            "This Google account is not on the Super Admin allowlist.",
        )

    if existing is not None and existing.qka_user_id is not None and existing.link_status == LINKED:
        if existing.account_status != ACCOUNT_ENABLED:
            return BindResult(False, None, "The Google identity is disabled.")
        user = existing.user
        if user is None or user.status != USER_ACTIVE or not user.login_enabled:
            return BindResult(False, None, "The linked QKA account is not active.")
        if allow_write:
            existing.primary_email = email
            existing.updated_at = datetime.now(timezone.utc)
            if existing.google is not None:
                existing.google.primary_email = email
            session.flush()
        return BindResult(True, user.id, "ok")

    if not allow_write:
        return BindResult(
            False,
            None,
            "This Google account is waiting for administrator identity approval.",
        )

    user = _create_google_login_user(session, email, identity.name)
    if existing is None:
        _create_linked_google_identity(session, user.id, sub, email)
    else:
        now = datetime.now(timezone.utc)
        existing.qka_user_id = user.id
        existing.primary_email = email
        existing.account_status = ACCOUNT_ENABLED
        existing.link_status = LINKED
        existing.updated_at = now
        if existing.google is not None:
            existing.google.primary_email = email
        else:
            session.add(
                GoogleWorkspaceAccount(
                    id=uuid.uuid4(),
                    external_identity_id=existing.id,
                    google_user_id=sub,
                    primary_email=email,
                    drive_onboarded=False,
                )
            )
        session.flush()
    return BindResult(True, user.id, "ok")


def _email_allowlisted(session: Session, email: str) -> bool:
    row = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized == email, AllowedEmail.enabled.is_(True))
    ).scalar_one_or_none()
    return row is not None


def _create_google_login_user(session: Session, email: str, name: str | None) -> QkaUser:
    now = datetime.now(timezone.utc)
    label = (name or "").strip() or email.split("@", 1)[0]
    user = QkaUser(
        id=uuid.uuid4(),
        display_name=label[:256],
        status=USER_ACTIVE,
        login_enabled=True,
        is_directory_operator=False,
        is_super_admin=False,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    return user


def _create_linked_google_identity(session: Session, user_id: uuid.UUID, sub: str, email: str) -> None:
    now = datetime.now(timezone.utc)
    identity_id = uuid.uuid4()
    session.add(
        ExternalIdentity(
            id=identity_id,
            qka_user_id=user_id,
            provider=PROVIDER_GOOGLE,
            stable_external_id=sub,
            primary_email=email,
            account_status=ACCOUNT_ENABLED,
            link_status=LINKED,
            directory_presence=0,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        GoogleWorkspaceAccount(
            id=uuid.uuid4(),
            external_identity_id=identity_id,
            google_user_id=sub,
            primary_email=email,
            drive_onboarded=False,
        )
    )
    session.flush()


def _create_pending_google_identity(session: Session, sub: str, email: str) -> None:
    now = datetime.now(timezone.utc)
    identity_id = uuid.uuid4()
    session.add(
        ExternalIdentity(
            id=identity_id,
            qka_user_id=None,
            provider=PROVIDER_GOOGLE,
            stable_external_id=sub,
            primary_email=email,
            account_status=ACCOUNT_ENABLED,
            link_status=UNMATCHED,
            directory_presence=0,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        GoogleWorkspaceAccount(
            id=uuid.uuid4(),
            external_identity_id=identity_id,
            google_user_id=sub,
            primary_email=email,
            drive_onboarded=False,
        )
    )
    session.flush()
