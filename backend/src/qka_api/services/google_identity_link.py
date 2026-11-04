"""Explicit Google ↔ QkaUser linking (never auto-merge by email).

Flow:
1. Verified Google OAuth proves the Google `sub`.
2. If that email matches an AD-enrolled QkaUser contact email and Google is not
   yet linked, create a PendingGoogleLink — do NOT create a second QkaUser and
   do NOT grant login.
3. Super Admin approves or rejects. Approve attaches the Google identity to the
   same QkaUser. Reject leaves accounts separate.
4. Conflict when another active QkaUser already owns a linked Google identity
   for that email/sub — approve is refused (no silent merge).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, USER_ACTIVE
from qka_shared.chat_access import activate_pending_grants_for_email, get_or_create_chat_access
from qka_shared.models import (
    AllowedEmail,
    ExternalIdentity,
    GoogleWorkspaceAccount,
    PENDING_GOOGLE_LINK_APPROVED,
    PENDING_GOOGLE_LINK_PENDING,
    PENDING_GOOGLE_LINK_REJECTED,
    PendingGoogleLink,
    QkaUser,
    SourceGrantAudit,
)

UNMATCHED = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    session: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    employee_email: str = "",
    detail: str = "",
    before: dict | None = None,
    after: dict | None = None,
    target_user_id: uuid.UUID | None = None,
) -> None:
    session.add(
        SourceGrantAudit(
            id=uuid.uuid4(),
            action=action,
            actor_user_id=actor_id,
            target_user_id=target_user_id,
            scope_id=None,
            grant_id=None,
            detail=(detail or "")[:2000],
            employee_email=(employee_email or "")[:256],
            share_name="",
            before_detail=json.dumps(before or {}, separators=(",", ":"))[:8000],
            after_detail=json.dumps(after or {}, separators=(",", ":"))[:8000],
            created_at=_now(),
        )
    )


def user_has_linked_google(user: QkaUser | None) -> bool:
    if user is None:
        return False
    for identity in user.identities:
        if (
            identity.provider == PROVIDER_GOOGLE
            and identity.link_status == LINKED
            and identity.account_status == ACCOUNT_ENABLED
        ):
            return True
    return False


def user_has_linked_ad(user: QkaUser | None) -> bool:
    if user is None:
        return False
    for identity in user.identities:
        if (
            identity.provider == PROVIDER_AD
            and identity.link_status == LINKED
            and identity.account_status == ACCOUNT_ENABLED
        ):
            return True
    return False


def find_ad_candidate_for_email(session: Session, email: str) -> QkaUser | None:
    """AD-linked QkaUser whose contact email matches and Google is not yet linked."""
    needle = (email or "").strip().lower()
    if not needle:
        return None
    rows = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .join(ExternalIdentity, ExternalIdentity.qka_user_id == QkaUser.id)
        .where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
            QkaUser.is_super_admin.is_(False),
            QkaUser.status == USER_ACTIVE,
        )
    ).scalars().unique().all()
    for user in rows:
        contact = (user.contact_email or "").strip().lower()
        if contact == needle and not user_has_linked_google(user):
            return user
    return None


def find_linked_google_owner(session: Session, email: str) -> QkaUser | None:
    """QkaUser that already owns a linked Google identity for this email.

    If multiple linked Google identities share the email (legacy fixture / dual-sub
    cases), return the first active owner — never merges them.
    """
    needle = (email or "").strip().lower()
    if not needle:
        return None
    identities = session.execute(
        select(ExternalIdentity)
        .options(selectinload(ExternalIdentity.user).selectinload(QkaUser.identities))
        .where(
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
            ExternalIdentity.qka_user_id.is_not(None),
            ExternalIdentity.primary_email == needle,
        )
        .order_by(ExternalIdentity.created_at.asc())
    ).scalars().all()
    for identity in identities:
        if identity.user is not None and identity.user.status == USER_ACTIVE:
            return identity.user
    return None


def get_open_pending_for_user(session: Session, qka_user_id: uuid.UUID) -> PendingGoogleLink | None:
    return session.execute(
        select(PendingGoogleLink)
        .where(
            PendingGoogleLink.target_qka_user_id == qka_user_id,
            PendingGoogleLink.status == PENDING_GOOGLE_LINK_PENDING,
        )
        .order_by(PendingGoogleLink.created_at.desc())
    ).scalars().first()


def get_open_pending_for_sub(session: Session, google_sub: str) -> PendingGoogleLink | None:
    return session.execute(
        select(PendingGoogleLink)
        .where(
            PendingGoogleLink.google_sub == google_sub,
            PendingGoogleLink.status == PENDING_GOOGLE_LINK_PENDING,
        )
        .order_by(PendingGoogleLink.created_at.desc())
    ).scalars().first()


def pending_link_dto(row: PendingGoogleLink | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "targetQkaUserId": str(row.target_qka_user_id),
        "googleSub": row.google_sub,
        "googleEmail": row.google_email_normalized,
        "googleProfileName": row.google_profile_name or None,
        "status": (
            "pending"
            if row.status == PENDING_GOOGLE_LINK_PENDING
            else "approved"
            if row.status == PENDING_GOOGLE_LINK_APPROVED
            else "rejected"
        ),
        "conflict": bool(row.conflict),
        "conflictReason": row.conflict_reason or None,
        "conflictingQkaUserId": str(row.conflicting_qka_user_id) if row.conflicting_qka_user_id else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def ensure_unmatched_google_identity(
    session: Session,
    *,
    sub: str,
    email: str,
    existing: ExternalIdentity | None = None,
) -> ExternalIdentity:
    if existing is not None:
        existing.primary_email = email
        existing.updated_at = _now()
        if existing.google is not None:
            existing.google.primary_email = email
        session.flush()
        return existing
    now = _now()
    identity_id = uuid.uuid4()
    identity = ExternalIdentity(
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
    session.add(identity)
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
    return identity


def record_pending_google_link(
    session: Session,
    *,
    target: QkaUser,
    sub: str,
    email: str,
    profile_name: str | None,
    existing_identity: ExternalIdentity | None,
    conflicting_user: QkaUser | None = None,
) -> PendingGoogleLink:
    """Create or refresh an open pending link. Does not grant login."""
    identity = ensure_unmatched_google_identity(
        session, sub=sub, email=email, existing=existing_identity
    )
    open_row = get_open_pending_for_sub(session, sub)
    conflict = conflicting_user is not None and conflicting_user.id != target.id
    reason = ""
    if conflict:
        reason = (
            f"Google email already belongs to another active QKA user "
            f"(qkaUserId={conflicting_user.id}). Cannot silently merge."
        )
    now = _now()
    if open_row is not None:
        open_row.target_qka_user_id = target.id
        open_row.google_email_normalized = email
        open_row.google_profile_name = (profile_name or "").strip()[:256]
        open_row.conflict = conflict
        open_row.conflict_reason = reason[:1024]
        open_row.conflicting_qka_user_id = conflicting_user.id if conflict else None
        open_row.external_identity_id = identity.id
        session.flush()
        return open_row

    row = PendingGoogleLink(
        id=uuid.uuid4(),
        target_qka_user_id=target.id,
        google_sub=sub,
        google_email_normalized=email,
        google_profile_name=(profile_name or "").strip()[:256],
        status=PENDING_GOOGLE_LINK_PENDING,
        conflict=conflict,
        conflict_reason=reason[:1024],
        conflicting_qka_user_id=conflicting_user.id if conflict else None,
        external_identity_id=identity.id,
        created_at=now,
        resolved_at=None,
        resolved_by_user_id=None,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        action="GoogleLinkPending",
        actor_id=None,
        employee_email=email,
        target_user_id=target.id,
        detail=f"sub={sub}; conflict={conflict}",
        after=pending_link_dto(row) or {},
    )
    return row


def approve_pending_google_link(
    session: Session,
    actor: QkaUser,
    pending_id: uuid.UUID,
) -> dict:
    row = session.get(PendingGoogleLink, pending_id)
    if row is None:
        raise LookupError("Pending Google link was not found.")
    if row.status != PENDING_GOOGLE_LINK_PENDING:
        raise ValueError("That Google link request is no longer pending.")
    if row.conflict:
        raise ValueError(
            row.conflict_reason
            or "Identity conflict: cannot merge two active QKA users. Reject this request instead."
        )

    target = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .where(QkaUser.id == row.target_qka_user_id)
    ).scalar_one_or_none()
    if target is None:
        raise LookupError("Target QKA user was not found.")
    if user_has_linked_google(target):
        raise ValueError("Target QKA user already has a verified Google identity.")

    # Sub must not already be linked elsewhere.
    other = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.stable_external_id == row.google_sub,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.qka_user_id.is_not(None),
            ExternalIdentity.qka_user_id != target.id,
        )
    ).scalar_one_or_none()
    if other is not None:
        raise ValueError(
            f"Google subject is already linked to another QKA user (qkaUserId={other.qka_user_id})."
        )

    now = _now()
    identity: ExternalIdentity | None = None
    if row.external_identity_id:
        identity = session.get(ExternalIdentity, row.external_identity_id)
    if identity is None:
        identity = session.execute(
            select(ExternalIdentity)
            .options(selectinload(ExternalIdentity.google))
            .where(
                ExternalIdentity.provider == PROVIDER_GOOGLE,
                ExternalIdentity.stable_external_id == row.google_sub,
            )
        ).scalar_one_or_none()

    if identity is None:
        identity_id = uuid.uuid4()
        identity = ExternalIdentity(
            id=identity_id,
            qka_user_id=target.id,
            provider=PROVIDER_GOOGLE,
            stable_external_id=row.google_sub,
            primary_email=row.google_email_normalized,
            account_status=ACCOUNT_ENABLED,
            link_status=LINKED,
            directory_presence=0,
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
        session.add(
            GoogleWorkspaceAccount(
                id=uuid.uuid4(),
                external_identity_id=identity_id,
                google_user_id=row.google_sub,
                primary_email=row.google_email_normalized,
                drive_onboarded=False,
            )
        )
    else:
        identity.qka_user_id = target.id
        identity.primary_email = row.google_email_normalized
        identity.account_status = ACCOUNT_ENABLED
        identity.link_status = LINKED
        identity.updated_at = now
        if identity.google is not None:
            identity.google.primary_email = row.google_email_normalized
        else:
            session.add(
                GoogleWorkspaceAccount(
                    id=uuid.uuid4(),
                    external_identity_id=identity.id,
                    google_user_id=row.google_sub,
                    primary_email=row.google_email_normalized,
                    drive_onboarded=False,
                )
            )

    # Ensure allowlist row exists for grants / directory (does not itself verify login).
    allow = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized == row.google_email_normalized)
    ).scalar_one_or_none()
    if allow is None:
        allow = AllowedEmail(
            id=uuid.uuid4(),
            email=row.google_email_normalized,
            email_normalized=row.google_email_normalized,
            enabled=True,
            display_name=(target.display_name or "")[:256],
            google_profile_name=(row.google_profile_name or "")[:256],
            directory_department=(target.directory_department or "")[:512],
            created_at=now,
            updated_at=now,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        session.add(allow)
        session.flush()
        get_or_create_chat_access(session, allow.id)
    else:
        allow.enabled = True
        if row.google_profile_name and not (allow.google_profile_name or "").strip():
            allow.google_profile_name = row.google_profile_name[:256]
        allow.updated_at = now
        allow.updated_by_user_id = actor.id

    if not (target.contact_email or "").strip():
        target.contact_email = row.google_email_normalized
    target.login_enabled = True
    target.updated_at = now

    row.status = PENDING_GOOGLE_LINK_APPROVED
    row.resolved_at = now
    row.resolved_by_user_id = actor.id
    session.flush()

    activate_pending_grants_for_email(session, row.google_email_normalized, target.id)
    _audit(
        session,
        action="GoogleLinkApproved",
        actor_id=actor.id,
        employee_email=row.google_email_normalized,
        target_user_id=target.id,
        detail=f"pendingId={row.id}; sub={row.google_sub}",
        after={"qkaUserId": str(target.id), "googleSub": row.google_sub},
    )
    return {
        "ok": True,
        "qkaUserId": str(target.id),
        "googleSub": row.google_sub,
        "googleEmail": row.google_email_normalized,
        "pendingGoogleLink": pending_link_dto(row),
    }


def reject_pending_google_link(
    session: Session,
    actor: QkaUser,
    pending_id: uuid.UUID,
) -> dict:
    row = session.get(PendingGoogleLink, pending_id)
    if row is None:
        raise LookupError("Pending Google link was not found.")
    if row.status != PENDING_GOOGLE_LINK_PENDING:
        raise ValueError("That Google link request is no longer pending.")

    now = _now()
    row.status = PENDING_GOOGLE_LINK_REJECTED
    row.resolved_at = now
    row.resolved_by_user_id = actor.id
    session.flush()
    _audit(
        session,
        action="GoogleLinkRejected",
        actor_id=actor.id,
        employee_email=row.google_email_normalized,
        target_user_id=row.target_qka_user_id,
        detail=f"pendingId={row.id}; sub={row.google_sub}",
        after=pending_link_dto(row) or {},
    )
    return {
        "ok": True,
        "rejected": True,
        "pendingGoogleLink": pending_link_dto(row),
    }
