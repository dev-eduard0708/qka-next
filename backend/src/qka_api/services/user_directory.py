"""Super Admin user directory (AllowedEmail + chat channels + ChatGPT Business membership badges)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, load_verified_emails
from qka_shared.chat_access import (
    get_or_create_chat_access,
    get_or_create_chat_access_for_user,
    set_chat_access,
    set_chat_access_for_user,
)
from qka_shared.email_address import try_normalize
from qka_api.services import google_identity_link as gil
from qka_shared.models import (
    AdDirectoryUser,
    AllowedEmail,
    ChatAccess,
    ChatgptMembership,
    Department,
    ExternalIdentity,
    MembershipImportBatch,
    PENDING_GOOGLE_LINK_PENDING,
    PendingGoogleLink,
    QkaUser,
    SourceGrantAudit,
)

# Fallback QKA department catalog when the department table is empty.
_BASELINE_DEPARTMENTS = (
    "Admin Support",
    "Executive",
    "Finance",
    "HR",
    "IT",
    "Legal",
    "Project",
    "Operations",
)

# ChatGPT Business membership badge statuses (informational only).
MEMBERSHIP_UNKNOWN = 0
MEMBERSHIP_MEMBER = 1
MEMBERSHIP_NONMEMBER = 2

MEMBERSHIP_SYNC_MODE = "manual_csv_import"
MEMBERSHIP_SYNC_NOTE = (
    "ChatGPT Business does not document a supported read-only membership API "
    "(Admin API list-users is documented for Enterprise/Edu, not Business). "
    "QKA uses authorized admin CSV import only — never OpenAI API org members or page scraping."
)


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


def _google_users_by_email(session: Session) -> dict[str, list[QkaUser]]:
    identities = session.execute(
        select(ExternalIdentity)
        .options(
            selectinload(ExternalIdentity.google),
            selectinload(ExternalIdentity.user)
            .selectinload(QkaUser.identities)
            .selectinload(ExternalIdentity.google),
        )
        .where(
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
            ExternalIdentity.qka_user_id.is_not(None),
        )
    ).scalars().all()
    by_email: dict[str, list[QkaUser]] = {}
    seen: set[tuple[uuid.UUID, str]] = set()
    for identity in identities:
        user = identity.user
        if user is None:
            continue
        for email in load_verified_emails(session, user.id):
            key = (user.id, email)
            if key in seen:
                continue
            seen.add(key)
            by_email.setdefault(email, []).append(user)
    for users in by_email.values():
        users.sort(key=lambda u: str(u.id))
    return by_email


def _google_sub(user: QkaUser | None) -> str | None:
    if user is None:
        return None
    for identity in user.identities:
        if (
            identity.provider == PROVIDER_GOOGLE
            and identity.link_status == LINKED
            and identity.account_status == ACCOUNT_ENABLED
        ):
            if identity.google is not None and identity.google.google_user_id:
                return identity.google.google_user_id
            return identity.stable_external_id
    return None


def _ad_idents(user: QkaUser | None) -> list[dict]:
    if user is None:
        return []
    return [
        {
            "domainUsername": ad.domain_username,
            "sid": ad.stable_external_id or None,
            "sidSuffix": (ad.stable_external_id or "").split("-")[-1] if ad.stable_external_id else None,
            "upn": ad.upn,
            "linked": True,
        }
        for ad in user.identities
        if ad.provider == PROVIDER_AD and ad.link_status == LINKED and ad.account_status == ACCOUNT_ENABLED
    ]


def _pending_by_user(session: Session) -> dict[uuid.UUID, PendingGoogleLink]:
    rows = session.execute(
        select(PendingGoogleLink)
        .where(PendingGoogleLink.status == PENDING_GOOGLE_LINK_PENDING)
        .order_by(PendingGoogleLink.created_at.desc())
    ).scalars().all()
    out: dict[uuid.UUID, PendingGoogleLink] = {}
    for row in rows:
        if row.target_qka_user_id not in out:
            out[row.target_qka_user_id] = row
    return out


def _identity_badges(
    *,
    ad_linked: bool,
    google_linked: bool,
    google_pending: bool,
) -> list[str]:
    """Separate badges: AD / Google / Google Pending (not a single combined source)."""
    badges: list[str] = []
    if ad_linked:
        badges.append("ad")
    if google_linked:
        badges.append("google")
    elif google_pending:
        badges.append("google_pending")
    return badges


def _membership_label(status: int) -> str:
    if status == MEMBERSHIP_MEMBER:
        return "verified_member"
    if status == MEMBERSHIP_NONMEMBER:
        return "verified_nonmember"
    return "unknown"


def _compact_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _looks_like_email_local_part(name: str, email_normalized: str) -> bool:
    local = email_normalized.split("@", 1)[0]
    return _compact_name(name) == _compact_name(local)


def _humanize_local_part(email_normalized: str) -> str:
    local = email_normalized.split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) or local


def directory_display_name(row: AllowedEmail, user: QkaUser | None) -> str:
    """Prefer an admin Full Name; never leave a row looking like a raw email local-part."""
    admin = (row.display_name or "").strip()
    if admin and not _looks_like_email_local_part(admin, row.email_normalized):
        return admin[:256]
    if user is not None:
        linked = (user.display_name or "").strip()
        if linked and not _looks_like_email_local_part(linked, row.email_normalized):
            return linked[:256]
    profile = (row.google_profile_name or "").strip()
    if profile:
        return profile[:256]
    if admin:
        return admin[:256]
    return _humanize_local_part(row.email_normalized)[:256]


def promote_allowlist_row_to_user(session: Session, row: AllowedEmail, user: QkaUser | None) -> None:
    """Existing Allowed Emails are directory users — persist a Full Name and channel row."""
    name = directory_display_name(row, user)
    if (row.display_name or "").strip() != name:
        row.display_name = name
        row.updated_at = _now()
    if user is not None and not (row.google_profile_name or "").strip():
        linked = (user.display_name or "").strip()
        if linked:
            row.google_profile_name = linked[:256]
    get_or_create_chat_access(session, row.id)


def _identity_source(user: QkaUser | None, *, has_allowlist: bool) -> str:
    has_ad = bool(_ad_idents(user))
    has_google = _google_sub(user) is not None
    if has_ad and has_google:
        return "both"
    if has_ad:
        return "ad"
    if has_google or has_allowlist:
        return "google"
    return "none"


def _linked_identities_badge(
    *,
    identity_source: str,
    identity_status: str,
    google_linked: bool,
    google_pending: bool = False,
) -> str:
    """Legacy combined field. 'both' ONLY when AD and Google are actually linked."""
    if identity_source == "both":
        return "both"
    if identity_source == "ad":
        return "ad"
    if identity_source == "google" and google_linked:
        return "google"
    if google_pending:
        return "pending"
    if identity_status in {"pending", "awaiting_google_login"} or identity_source in {"google", "none"}:
        return "pending"
    return "pending"


def match_department_option(ad_department: str | None, options: list[str]) -> str | None:
    """Return the catalog option matching AD department (case-insensitive), else None."""
    needle = (ad_department or "").strip().lower()
    if not needle:
        return None
    for opt in options:
        if opt.strip().lower() == needle:
            return opt
    return None


def list_department_options(session: Session) -> dict:
    """QKA department dropdown options (not free-text)."""
    names: set[str] = set()
    for row in session.execute(
        select(Department).where(Department.status == 0).order_by(Department.name)
    ).scalars().all():
        name = (row.name or row.code or "").strip()
        if name:
            names.add(name)
    for (value,) in session.execute(
        select(QkaUser.directory_department)
        .where(QkaUser.directory_department.is_not(None), QkaUser.directory_department != "")
        .distinct()
    ):
        if (value or "").strip():
            names.add(value.strip())
    for (value,) in session.execute(
        select(AllowedEmail.directory_department)
        .where(AllowedEmail.directory_department.is_not(None), AllowedEmail.directory_department != "")
        .distinct()
    ):
        if (value or "").strip():
            names.add(value.strip())
    for (value,) in session.execute(
        select(AdDirectoryUser.department)
        .where(AdDirectoryUser.department.is_not(None), AdDirectoryUser.department != "")
        .distinct()
    ):
        if (value or "").strip():
            names.add(value.strip())
    # Always include the baseline QKA catalog so admin overrides remain selectable
    # even when Active Directory has not yet published that department name.
    names.update(_BASELINE_DEPARTMENTS)
    ordered = sorted(names, key=lambda s: s.lower())
    return {
        "items": [{"value": name, "label": name} for name in ordered],
        "total": len(ordered),
    }


def _user_dto(
    row: AllowedEmail,
    *,
    user: QkaUser | None,
    chat: ChatAccess | None,
    membership: ChatgptMembership | None,
    pending_google: PendingGoogleLink | None = None,
) -> dict:
    ad_idents = _ad_idents(user)
    has_google = _google_sub(user) is not None
    google_pending = pending_google is not None and not has_google
    if not row.enabled:
        identity_status = "disabled"
        account_status = "disabled"
    elif google_pending:
        identity_status = "ad_enrolled" if ad_idents else "pending"
        account_status = "awaiting_admin_approval"
    elif user is not None and (has_google or user.login_enabled):
        # Verified Google bind (or login-enabled QKA user) — active.
        identity_status = "linked"
        account_status = "active"
    elif user is not None and ad_idents:
        # Canonical AD user that also has an explicit Google allowlist row (not yet bound).
        identity_status = "ad_enrolled"
        account_status = "active"
    else:
        # Explicit Google allowlist awaiting first verified Workspace sign-in.
        identity_status = "pending"
        account_status = "awaiting_google_login"
    admin_name = directory_display_name(row, user)
    mem_status = membership.status if membership is not None else MEMBERSHIP_UNKNOWN
    identity_source = _identity_source(user, has_allowlist=True)
    department = (
        (user.directory_department if user and (user.directory_department or "").strip() else "")
        or (row.directory_department or "")
    )
    return {
        "allowlistId": str(row.id),
        "qkaUserId": str(user.id) if user is not None else None,
        "displayName": admin_name,
        "googleProfileName": (row.google_profile_name or "").strip() or None,
        "primaryEmail": row.email_normalized,
        "contactEmail": (user.contact_email if user else None) or row.email_normalized,
        "domainUsername": ad_idents[0]["domainUsername"] if ad_idents else None,
        "department": department,
        "identitySource": identity_source,
        "linkedIdentities": _linked_identities_badge(
            identity_source=identity_source,
            identity_status=identity_status,
            google_linked=has_google,
            google_pending=google_pending,
        ),
        "identityBadges": _identity_badges(
            ad_linked=bool(ad_idents),
            google_linked=has_google,
            google_pending=google_pending or (identity_status == "pending" and not ad_idents and not has_google),
        ),
        "googleSub": _google_sub(user),
        "googleLinked": has_google,
        "adLinked": bool(ad_idents),
        "googlePending": google_pending,
        "pendingGoogleLink": gil.pending_link_dto(pending_google) if google_pending else None,
        "identityConflict": bool(pending_google.conflict) if google_pending and pending_google else False,
        "identityConflictReason": (
            pending_google.conflict_reason if google_pending and pending_google and pending_google.conflict else None
        ),
        "adBrowserLoginImplemented": False,
        "identityStatus": identity_status,
        "accountStatus": account_status,
        "chatgptMembership": _membership_label(mem_status),
        "chatgptMembershipSource": membership.source if membership else "unknown",
        "chatgptMembershipVerifiedAt": membership.verified_at.isoformat() if membership and membership.verified_at else None,
        "chatgptMemberName": (membership.member_name if membership else "") or None,
        "localChatEnabled": bool(chat.local_chat_enabled) if chat else False,
        "mcpEnabled": bool(chat.mcp_enabled) if chat else False,
        "allowlisted": True,
        "allowlistEnabled": bool(row.enabled),
        "canGrant": identity_status in {"linked", "pending", "ad_enrolled"} and bool(row.enabled),
        "adIdentities": ad_idents,
        "superAdmin": bool(user.is_super_admin) if user is not None else False,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ad_only_user_dto(
    user: QkaUser,
    chat: ChatAccess | None,
    pending_google: PendingGoogleLink | None = None,
) -> dict:
    ad_idents = _ad_idents(user)
    has_google = _google_sub(user) is not None
    google_pending = pending_google is not None and not has_google
    if user.status != 0:
        identity_status = "disabled"
        account_status = "disabled"
    elif google_pending:
        identity_status = "ad_enrolled"
        account_status = "awaiting_admin_approval"
    elif has_google or user.login_enabled:
        identity_status = "linked"
        account_status = "active"
    else:
        identity_status = "ad_enrolled"
        account_status = "active"
    identity_source = _identity_source(user, has_allowlist=False)
    return {
        "allowlistId": None,
        "qkaUserId": str(user.id),
        "displayName": user.display_name,
        "googleProfileName": (
            (pending_google.google_profile_name or "").strip() or None
            if google_pending and pending_google
            else None
        ),
        "primaryEmail": user.contact_email or "",
        "contactEmail": user.contact_email,
        "domainUsername": ad_idents[0]["domainUsername"] if ad_idents else None,
        "department": user.directory_department or "",
        "identitySource": identity_source,
        "linkedIdentities": _linked_identities_badge(
            identity_source=identity_source,
            identity_status=identity_status,
            google_linked=has_google,
            google_pending=google_pending,
        ),
        "identityBadges": _identity_badges(
            ad_linked=bool(ad_idents),
            google_linked=has_google,
            google_pending=google_pending,
        ),
        "googleSub": _google_sub(user),
        "googleLinked": has_google,
        "adLinked": bool(ad_idents),
        "googlePending": google_pending,
        "pendingGoogleLink": gil.pending_link_dto(pending_google) if google_pending else None,
        "identityConflict": bool(pending_google.conflict) if google_pending and pending_google else False,
        "identityConflictReason": (
            pending_google.conflict_reason if google_pending and pending_google and pending_google.conflict else None
        ),
        "adBrowserLoginImplemented": False,
        "identityStatus": identity_status,
        "accountStatus": account_status,
        "chatgptMembership": "unknown",
        "chatgptMembershipSource": "unknown",
        "chatgptMembershipVerifiedAt": None,
        "chatgptMemberName": None,
        "localChatEnabled": bool(chat.local_chat_enabled) if chat else False,
        "mcpEnabled": bool(chat.mcp_enabled) if chat else False,
        "allowlisted": False,
        "allowlistEnabled": True,
        "canGrant": bool(ad_idents),
        "adIdentities": ad_idents,
        "superAdmin": bool(user.is_super_admin),
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "updatedAt": user.updated_at.isoformat() if user.updated_at else None,
    }


def _eligible_ad_pending_dto(row: AdDirectoryUser) -> dict:
    """Eligible AD inventory row not yet enrolled into a QkaUser (Access enroll queue)."""
    dept = (row.department or "").strip()
    mail = (row.mail or "").strip() or None
    return {
        "allowlistId": None,
        "qkaUserId": None,
        "adDirectoryUserId": str(row.id),
        "objectGuid": row.object_guid,
        "objectSid": row.object_sid or None,
        "displayName": row.display_name or row.sam_account_name,
        "googleProfileName": None,
        "primaryEmail": mail or "",
        "contactEmail": mail,
        "domainUsername": row.sam_account_name,
        "department": dept,
        "identitySource": "ad",
        "linkedIdentities": "pending",
        "identityBadges": ["ad"],
        "googleSub": None,
        "googleLinked": False,
        "adLinked": False,
        "googlePending": False,
        "pendingGoogleLink": None,
        "identityConflict": False,
        "identityConflictReason": None,
        "adBrowserLoginImplemented": False,
        "identityStatus": "eligible",
        "accountStatus": "eligible_pending_enroll",
        "chatgptMembership": "unknown",
        "chatgptMembershipSource": "unknown",
        "chatgptMembershipVerifiedAt": None,
        "chatgptMemberName": None,
        "localChatEnabled": False,
        "mcpEnabled": False,
        "allowlisted": False,
        "allowlistEnabled": True,
        "canGrant": False,
        "canEnroll": True,
        "adIdentities": [],
        "superAdmin": False,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ad_users_by_contact_email(session: Session) -> dict[str, QkaUser]:
    """Map normalized contact email → AD-linked QkaUser (never merges by name alone)."""
    rows = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .join(ExternalIdentity, ExternalIdentity.qka_user_id == QkaUser.id)
        .where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
            QkaUser.is_super_admin.is_(False),
            QkaUser.contact_email.is_not(None),
        )
    ).scalars().unique().all()
    by_email: dict[str, QkaUser] = {}
    for user in rows:
        email = (user.contact_email or "").strip().lower()
        if email and email not in by_email:
            by_email[email] = user
    return by_email


def _allowlist_has_independent_use(
    session: Session,
    row: AllowedEmail,
    *,
    chat: ChatAccess | None,
    membership: ChatgptMembership | None,
) -> bool:
    """True when the allowlist row owns Google/channel/source identity of its own."""
    if membership is not None and int(membership.status or 0) != MEMBERSHIP_UNKNOWN:
        return True
    if chat is not None and (chat.local_chat_enabled or chat.mcp_enabled):
        return True
    from qka_shared.models import SourceGrant

    grant_count = session.execute(
        select(SourceGrant.id).where(SourceGrant.allowed_email_id == row.id).limit(1)
    ).scalar_one_or_none()
    return grant_count is not None


def membership_sync_info() -> dict:
    return {
        "mode": MEMBERSHIP_SYNC_MODE,
        "automaticSyncSupported": False,
        "note": MEMBERSHIP_SYNC_NOTE,
        "acceptedCsvColumns": ["email", "name", "role", "seat type", "seat_type"],
    }


def list_users(
    session: Session,
    *,
    search: str | None = None,
    account_status: str | None = None,
    identity_status: str | None = None,
    chatgpt_membership: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    from qka_shared.chat_access import get_or_create_chat_access_for_user

    rows = session.execute(select(AllowedEmail).order_by(AllowedEmail.email_normalized)).scalars().all()
    by_email = _google_users_by_email(session)
    ad_by_contact = _ad_users_by_contact_email(session)
    pending_by_user = _pending_by_user(session)
    chat_rows = session.execute(select(ChatAccess)).scalars().all()
    chat_by_allow = {c.allowed_email_id: c for c in chat_rows if c.allowed_email_id}
    chat_by_user = {c.qka_user_id: c for c in chat_rows if c.qka_user_id}
    mem_by_id = {m.allowed_email_id: m for m in session.execute(select(ChatgptMembership)).scalars().all()}

    items = []
    seen_user_ids: set[uuid.UUID] = set()
    for row in rows:
        linked = by_email.get(row.email_normalized) or []
        google_user = linked[0] if linked else None
        ad_contact_user = ad_by_contact.get(row.email_normalized)
        chat = chat_by_allow.get(row.id) or get_or_create_chat_access(session, row.id)
        chat_by_allow[row.id] = chat
        membership = mem_by_id.get(row.id)

        # Redundant AD-enroll contact allowlist (no Google bind, no independent grants/channels):
        # collapse to the canonical AD QkaUser row — never emit a second "Google pending" card.
        if (
            google_user is None
            and ad_contact_user is not None
            and not _allowlist_has_independent_use(session, row, chat=chat, membership=membership)
        ):
            if ad_contact_user.id not in seen_user_ids:
                user_chat = chat_by_user.get(ad_contact_user.id) or get_or_create_chat_access_for_user(
                    session, ad_contact_user.id
                )
                items.append(
                    _ad_only_user_dto(
                        ad_contact_user,
                        user_chat,
                        pending_google=pending_by_user.get(ad_contact_user.id),
                    )
                )
                seen_user_ids.add(ad_contact_user.id)
            continue

        user = google_user or ad_contact_user
        if user is not None:
            seen_user_ids.add(user.id)
        promote_allowlist_row_to_user(session, row, user)
        dto = _user_dto(
            row,
            user=user,
            chat=chat,
            membership=membership,
            pending_google=pending_by_user.get(user.id) if user is not None else None,
        )
        items.append(dto)

    # Include AD-enrolled QkaUsers that are not already represented by an allowlist row.
    ad_users = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .join(ExternalIdentity, ExternalIdentity.qka_user_id == QkaUser.id)
        .where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
            QkaUser.is_super_admin.is_(False),
        )
    ).scalars().unique().all()
    for user in ad_users:
        if user.id in seen_user_ids:
            continue
        chat = chat_by_user.get(user.id) or get_or_create_chat_access_for_user(session, user.id)
        items.append(_ad_only_user_dto(user, chat, pending_google=pending_by_user.get(user.id)))
        seen_user_ids.add(user.id)

    # Eligible AD inventory users not yet enrolled — visible in Access so Super Admin can enroll.
    seen_guids: set[str] = {
        (item.get("objectGuid") or "").strip().lower()
        for item in items
        if item.get("objectGuid")
    }
    linked_sids = {
        (ident.stable_external_id or "").strip()
        for user in ad_users
        for ident in user.identities
        if ident.provider == PROVIDER_AD and ident.link_status == LINKED
    }
    eligible_rows = session.execute(
        select(AdDirectoryUser).where(
            AdDirectoryUser.qka_eligible.is_(True),
            AdDirectoryUser.is_present.is_(True),
            AdDirectoryUser.account_enabled.is_(True),
            AdDirectoryUser.qka_archived.is_(False),
        )
    ).scalars().all()
    for row in eligible_rows:
        guid = (row.object_guid or "").strip().lower()
        sid = (row.object_sid or "").strip()
        if guid and guid in seen_guids:
            continue
        if sid and sid in linked_sids:
            continue
        if row.linked_qka_user_id and row.linked_qka_user_id in seen_user_ids:
            continue
        if row.mapping_status == 2 and row.linked_qka_user_id:
            continue
        items.append(_eligible_ad_pending_dto(row))
        if guid:
            seen_guids.add(guid)

    q = (search or "").strip().lower()
    if q:
        items = [
            item
            for item in items
            if q in (item.get("primaryEmail") or "").lower()
            or q in (item.get("displayName") or "").lower()
            or q in (item.get("domainUsername") or "").lower()
            or q in (item.get("department") or "").lower()
            or q in (item.get("chatgptMemberName") or "").lower()
        ]
    if account_status:
        want_acct = account_status.strip().lower()
        if want_acct == "pending":
            # Legacy filter value — treat as awaiting Google login.
            items = [item for item in items if item["accountStatus"] in {"pending", "awaiting_google_login"}]
        else:
            items = [item for item in items if item["accountStatus"] == want_acct]
    if identity_status:
        want = identity_status.strip().lower()
        if want == "pending_google_login":
            want = "pending"
        items = [item for item in items if item["identityStatus"] == want]
    if chatgpt_membership:
        items = [item for item in items if item["chatgptMembership"] == chatgpt_membership.strip().lower()]

    items.sort(key=lambda i: ((i.get("displayName") or "").lower(), i.get("primaryEmail") or ""))
    total = len(items)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "membershipSync": membership_sync_info(),
        "adOnlyLoginImplemented": False,
    }


def create_user(
    session: Session,
    actor: QkaUser,
    *,
    display_name: str,
    email_raw: str,
    department: str | None = None,
    local_chat_enabled: bool = False,
    mcp_enabled: bool = False,
    enabled: bool = True,
) -> dict:
    email, err = try_normalize(email_raw)
    if email is None:
        raise ValueError(err or "Email is invalid.")
    name = (display_name or "").strip()
    if not name:
        raise ValueError("Full name is required.")
    if len(name) > 256:
        raise ValueError("Full name is too long.")
    dept = (department or "").strip()
    if not dept:
        raise ValueError("QKA department is required.")
    options = {i["value"].lower() for i in list_department_options(session)["items"]}
    if dept.lower() not in options:
        raise ValueError("Select a QKA department from the list.")

    exists = session.execute(select(AllowedEmail).where(AllowedEmail.email_normalized == email)).scalar_one_or_none()
    if exists is not None:
        raise LookupError("That company email is already in the user directory.")

    # Soft alias conflict: same local-part with dots/plus already covered by normalization.
    now = _now()
    row = AllowedEmail(
        id=uuid.uuid4(),
        email=email,
        email_normalized=email,
        enabled=bool(enabled),
        display_name=name[:256],
        google_profile_name="",
        directory_department=dept[:512],
        created_at=now,
        updated_at=now,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    session.add(row)
    session.flush()
    chat = get_or_create_chat_access(session, row.id)
    if local_chat_enabled or mcp_enabled:
        set_chat_access(
            session,
            actor.id,
            row.id,
            local_chat_enabled=bool(local_chat_enabled),
            mcp_enabled=bool(mcp_enabled),
        )
        chat = session.execute(select(ChatAccess).where(ChatAccess.allowed_email_id == row.id)).scalar_one()
    _audit(
        session,
        action="UserCreated",
        actor_id=actor.id,
        employee_email=email,
        detail=f"name={name}; department={dept}; localChat={bool(local_chat_enabled)}; mcp={bool(mcp_enabled)}",
        after={
            "displayName": name,
            "email": email,
            "department": dept,
            "enabled": bool(enabled),
            "localChat": bool(local_chat_enabled),
            "mcp": bool(mcp_enabled),
            "note": "Allowlist only — Google login requires verified OAuth bind; email is not login verification.",
        },
    )
    return _user_dto(row, user=None, chat=chat, membership=None)


def update_user(
    session: Session,
    actor: QkaUser,
    allowlist_id: uuid.UUID,
    *,
    display_name: str | None = None,
    department: str | None = None,
    enabled: bool | None = None,
    local_chat_enabled: bool | None = None,
    mcp_enabled: bool | None = None,
) -> dict:
    row = session.get(AllowedEmail, allowlist_id)
    if row is None:
        raise LookupError("User was not found.")
    before = {
        "displayName": row.display_name,
        "enabled": row.enabled,
        "department": row.directory_department or "",
    }
    chat = get_or_create_chat_access(session, row.id)
    before["localChat"] = chat.local_chat_enabled
    before["mcp"] = chat.mcp_enabled

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValueError("Full name is required.")
        row.display_name = name[:256]
    if department is not None:
        dept = department.strip()
        if not dept:
            raise ValueError("QKA department is required.")
        options = {i["value"].lower() for i in list_department_options(session)["items"]}
        if dept.lower() not in options:
            raise ValueError("Select a QKA department from the list.")
        row.directory_department = dept[:512]
        # Keep linked QkaUser department in sync when present (never changes Google/AD IDs).
        by_email = _google_users_by_email(session)
        linked_user = (by_email.get(row.email_normalized) or [None])[0]
        if linked_user is not None:
            linked_user.directory_department = dept[:512]
            linked_user.updated_at = _now()
    if enabled is not None:
        row.enabled = bool(enabled)
    row.updated_at = _now()
    row.updated_by_user_id = actor.id
    session.flush()

    if local_chat_enabled is not None or mcp_enabled is not None:
        set_chat_access(
            session,
            actor.id,
            row.id,
            local_chat_enabled=bool(local_chat_enabled if local_chat_enabled is not None else chat.local_chat_enabled),
            mcp_enabled=bool(mcp_enabled if mcp_enabled is not None else chat.mcp_enabled),
        )
        chat = session.execute(select(ChatAccess).where(ChatAccess.allowed_email_id == row.id)).scalar_one()

    actions = []
    if before["displayName"] != row.display_name or before["department"] != (row.directory_department or ""):
        actions.append("UserEdited")
    if before["enabled"] != row.enabled:
        actions.append("UserEnabled" if row.enabled else "UserDisabled")
    if before["localChat"] != chat.local_chat_enabled or before["mcp"] != chat.mcp_enabled:
        actions.append("ChannelAccessChanged")
    after = {
        "displayName": row.display_name,
        "enabled": row.enabled,
        "department": row.directory_department or "",
        "localChat": chat.local_chat_enabled,
        "mcp": chat.mcp_enabled,
    }
    for action in actions or ["UserEdited"]:
        _audit(
            session,
            action=action,
            actor_id=actor.id,
            employee_email=row.email_normalized,
            detail=f"name={row.display_name}",
            before=before,
            after=after,
        )

    by_email = _google_users_by_email(session)
    linked = by_email.get(row.email_normalized) or []
    membership = session.execute(
        select(ChatgptMembership).where(ChatgptMembership.allowed_email_id == row.id)
    ).scalar_one_or_none()
    return _user_dto(row, user=linked[0] if linked else None, chat=chat, membership=membership)


def update_ad_only_user(
    session: Session,
    actor: QkaUser,
    qka_user_id: uuid.UUID,
    *,
    display_name: str | None = None,
    enabled: bool | None = None,
    local_chat_enabled: bool | None = None,
    mcp_enabled: bool | None = None,
    department: str | None = None,
    contact_email: str | None = None,
) -> dict:
    """Update an AD-enrolled QkaUser. Never rewrites AD GUID/SID or Google identities."""
    user = session.get(QkaUser, qka_user_id)
    if user is None:
        raise LookupError("User was not found.")
    chat = get_or_create_chat_access_for_user(session, user.id)
    before = {
        "displayName": user.display_name,
        "enabled": user.status == 0,
        "localChat": chat.local_chat_enabled,
        "mcp": chat.mcp_enabled,
        "department": user.directory_department or "",
        "contactEmail": user.contact_email or "",
    }
    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValueError("Full name is required.")
        user.display_name = name[:256]
    if department is not None:
        dept = department.strip()
        if not dept:
            raise ValueError("QKA department is required.")
        options = {i["value"].lower() for i in list_department_options(session)["items"]}
        if dept.lower() not in options:
            raise ValueError("Select a QKA department from the list.")
        user.directory_department = dept[:512]
    if contact_email is not None:
        raw = contact_email.strip()
        if not raw:
            user.contact_email = None
        else:
            normalized, err = try_normalize(raw)
            if normalized is None:
                raise ValueError(err or "Contact email is invalid.")
            user.contact_email = normalized
    if enabled is not None:
        # Soft-disable QKA account only — never writes to Active Directory.
        user.status = 0 if enabled else 1
    user.updated_at = _now()
    session.flush()
    if local_chat_enabled is not None or mcp_enabled is not None:
        set_chat_access_for_user(
            session,
            actor.id,
            user.id,
            local_chat_enabled=bool(
                local_chat_enabled if local_chat_enabled is not None else chat.local_chat_enabled
            ),
            mcp_enabled=bool(mcp_enabled if mcp_enabled is not None else chat.mcp_enabled),
        )
        chat = get_or_create_chat_access_for_user(session, user.id)
    after = {
        "displayName": user.display_name,
        "enabled": user.status == 0,
        "localChat": chat.local_chat_enabled,
        "mcp": chat.mcp_enabled,
        "department": user.directory_department or "",
        "contactEmail": user.contact_email or "",
    }
    actions = []
    if (
        before["displayName"] != after["displayName"]
        or before["department"] != after["department"]
        or before["contactEmail"] != after["contactEmail"]
    ):
        actions.append("UserEdited")
    if before["enabled"] != after["enabled"]:
        actions.append("UserEnabled" if after["enabled"] else "UserDisabled")
    if before["localChat"] != after["localChat"] or before["mcp"] != after["mcp"]:
        actions.append("ChannelAccessChanged")
    for action in actions or ["UserEdited"]:
        _audit(
            session,
            action=action,
            actor_id=actor.id,
            employee_email=user.contact_email or "",
            detail=f"qkaUserId={user.id}; name={user.display_name}",
            before=before,
            after=after,
        )
    return _ad_only_user_dto(user, chat, pending_google=gil.get_open_pending_for_user(session, user.id))


def approve_google_identity_link(session: Session, actor: QkaUser, pending_id: uuid.UUID) -> dict:
    result = gil.approve_pending_google_link(session, actor, pending_id)
    user_id = uuid.UUID(result["qkaUserId"])
    user = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .where(QkaUser.id == user_id)
    ).scalar_one()
    chat = get_or_create_chat_access_for_user(session, user.id)
    allow = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized == result["googleEmail"])
    ).scalar_one_or_none()
    if allow is not None:
        membership = session.execute(
            select(ChatgptMembership).where(ChatgptMembership.allowed_email_id == allow.id)
        ).scalar_one_or_none()
        allow_chat = get_or_create_chat_access(session, allow.id)
        dto = _user_dto(allow, user=user, chat=allow_chat, membership=membership, pending_google=None)
    else:
        dto = _ad_only_user_dto(user, chat, pending_google=None)
    result["user"] = dto
    return result


def reject_google_identity_link(session: Session, actor: QkaUser, pending_id: uuid.UUID) -> dict:
    result = gil.reject_pending_google_link(session, actor, pending_id)
    target_id = uuid.UUID(result["pendingGoogleLink"]["targetQkaUserId"])
    user = session.execute(
        select(QkaUser)
        .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
        .where(QkaUser.id == target_id)
    ).scalar_one_or_none()
    if user is not None:
        chat = get_or_create_chat_access_for_user(session, user.id)
        result["user"] = _ad_only_user_dto(user, chat, pending_google=None)
    return result


def set_google_profile_name(session: Session, email_normalized: str, profile_name: str) -> None:
    """Capture Google-provided name on bind without overwriting admin display_name."""
    row = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized == email_normalized)
    ).scalar_one_or_none()
    if row is None:
        return
    name = (profile_name or "").strip()[:256]
    if name and row.google_profile_name != name:
        row.google_profile_name = name
        row.updated_at = _now()
        session.flush()


def _parse_membership_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV must include a header row with an email column.")
    fields = {((f or "").strip().lower()): (f or "") for f in reader.fieldnames}
    email_key = None
    for candidate in ("email", "email address", "work email", "user email"):
        if candidate in fields:
            email_key = fields[candidate]
            break
    if email_key is None:
        raise ValueError("CSV must include an Email column.")
    name_key = None
    for candidate in ("name", "full name", "display name", "member name"):
        if candidate in fields:
            name_key = fields[candidate]
            break
    role_key = fields.get("role")
    seat_key = fields.get("seat type") or fields.get("seat_type") or fields.get("seat")

    rows: list[dict] = []
    for raw in reader:
        email_raw = (raw.get(email_key) or "").strip()
        if not email_raw:
            continue
        email, err = try_normalize(email_raw)
        if email is None:
            raise ValueError(f"Invalid company email in CSV: {email_raw} ({err})")
        rows.append(
            {
                "email": email,
                "name": ((raw.get(name_key) if name_key else "") or "").strip()[:256],
                "role": ((raw.get(role_key) if role_key else "") or "").strip()[:64],
                "seatType": ((raw.get(seat_key) if seat_key else "") or "").strip()[:64],
            }
        )
    if not rows:
        raise ValueError("CSV contained no member rows.")
    return rows


def import_chatgpt_membership_csv(
    session: Session,
    actor: QkaUser,
    *,
    csv_text: str,
    file_name: str = "",
    mark_missing_as_nonmember: bool = False,
    notes: str = "",
) -> dict:
    """Import ChatGPT Business members from an authorized admin CSV export.

    Does not enable QKA login, chat channels, or source grants automatically.
    """
    parsed = _parse_membership_csv(csv_text)
    now = _now()
    batch = MembershipImportBatch(
        id=uuid.uuid4(),
        source_label="csv_import",
        file_name=(file_name or "")[:512],
        row_count=len(parsed),
        matched_count=0,
        created_count=0,
        member_count=0,
        nonmember_count=0,
        mark_missing_nonmember=bool(mark_missing_as_nonmember),
        notes=(notes or "")[:2000],
        created_at=now,
        created_by_user_id=actor.id,
    )
    session.add(batch)
    session.flush()

    imported_emails: set[str] = set()
    for item in parsed:
        email = item["email"]
        imported_emails.add(email)
        allow = session.execute(select(AllowedEmail).where(AllowedEmail.email_normalized == email)).scalar_one_or_none()
        created = False
        if allow is None:
            # Pending directory entry only — login stays disabled until admin enables / Add User flow.
            local = email.split("@", 1)[0]
            allow = AllowedEmail(
                id=uuid.uuid4(),
                email=email,
                email_normalized=email,
                enabled=False,
                display_name=(item["name"] or local)[:256],
                google_profile_name="",
                created_at=now,
                updated_at=now,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            session.add(allow)
            session.flush()
            get_or_create_chat_access(session, allow.id)
            created = True
            batch.created_count += 1
            _audit(
                session,
                action="UserCreated",
                actor_id=actor.id,
                employee_email=email,
                detail="created_from_membership_import; login_disabled",
                after={"displayName": allow.display_name, "email": email, "enabled": False, "source": "membership_import"},
            )
        else:
            batch.matched_count += 1
            if item["name"] and not (allow.display_name or "").strip():
                allow.display_name = item["name"][:256]
                allow.updated_at = now
                allow.updated_by_user_id = actor.id

        mem = session.execute(
            select(ChatgptMembership).where(ChatgptMembership.allowed_email_id == allow.id)
        ).scalar_one_or_none()
        if mem is None:
            mem = ChatgptMembership(
                id=uuid.uuid4(),
                allowed_email_id=allow.id,
                status=MEMBERSHIP_MEMBER,
                source="manual_csv_import",
                member_name=(item["name"] or "")[:256],
                workspace_role=item["role"],
                seat_type=item["seatType"],
                verified_at=now,
                last_import_batch_id=batch.id,
                created_at=now,
                updated_at=now,
                updated_by_user_id=actor.id,
            )
            session.add(mem)
        else:
            mem.status = MEMBERSHIP_MEMBER
            mem.source = "manual_csv_import"
            if item["name"]:
                mem.member_name = item["name"][:256]
            mem.workspace_role = item["role"]
            mem.seat_type = item["seatType"]
            mem.verified_at = now
            mem.last_import_batch_id = batch.id
            mem.updated_at = now
            mem.updated_by_user_id = actor.id
        batch.member_count += 1
        _audit(
            session,
            action="MembershipImportMember",
            actor_id=actor.id,
            employee_email=email,
            detail=f"batch={batch.id}; createdDirectory={created}",
            after={"status": "verified_member", "source": "manual_csv_import"},
        )

    if mark_missing_as_nonmember:
        all_allows = session.execute(select(AllowedEmail)).scalars().all()
        for allow in all_allows:
            if allow.email_normalized in imported_emails:
                continue
            mem = session.execute(
                select(ChatgptMembership).where(ChatgptMembership.allowed_email_id == allow.id)
            ).scalar_one_or_none()
            if mem is None:
                mem = ChatgptMembership(
                    id=uuid.uuid4(),
                    allowed_email_id=allow.id,
                    status=MEMBERSHIP_NONMEMBER,
                    source="manual_csv_import",
                    member_name="",
                    workspace_role="",
                    seat_type="",
                    verified_at=now,
                    last_import_batch_id=batch.id,
                    created_at=now,
                    updated_at=now,
                    updated_by_user_id=actor.id,
                )
                session.add(mem)
            else:
                mem.status = MEMBERSHIP_NONMEMBER
                mem.source = "manual_csv_import"
                mem.verified_at = now
                mem.last_import_batch_id = batch.id
                mem.updated_at = now
                mem.updated_by_user_id = actor.id
            batch.nonmember_count += 1

    session.flush()
    _audit(
        session,
        action="MembershipImportCompleted",
        actor_id=actor.id,
        detail=(
            f"batch={batch.id}; rows={batch.row_count}; matched={batch.matched_count}; "
            f"created={batch.created_count}; members={batch.member_count}; "
            f"nonmembers={batch.nonmember_count}; file={file_name}"
        ),
        after={
            "batchId": str(batch.id),
            "mode": MEMBERSHIP_SYNC_MODE,
            "markMissingNonmember": bool(mark_missing_as_nonmember),
        },
    )
    return {
        "batchId": str(batch.id),
        "mode": MEMBERSHIP_SYNC_MODE,
        "rowCount": batch.row_count,
        "matchedCount": batch.matched_count,
        "createdCount": batch.created_count,
        "memberCount": batch.member_count,
        "nonmemberCount": batch.nonmember_count,
        "markMissingAsNonmember": bool(mark_missing_as_nonmember),
        "note": MEMBERSHIP_SYNC_NOTE,
    }


def cleanup_redundant_ad_contact_allowlists(
    session: Session,
    actor_id: uuid.UUID | None,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove AllowedEmail rows that exist only as AD contact side-effects.

    Preserves AD QkaUser, ExternalIdentity(AD), ChatAccess(qka_user_id), Source grants on
    the user, Google OAuth, MCP, and any allowlist with independent identity/grants/channels.
    """
    from qka_shared.models import SourceGrant

    ad_by_contact = _ad_users_by_contact_email(session)
    removed: list[dict] = []
    skipped: list[dict] = []
    for email, user in ad_by_contact.items():
        # Skip if a verified Google identity already uses this email or user.
        google_on_user = _google_sub(user) is not None
        google_by_email = bool(_google_users_by_email(session).get(email))
        if google_on_user or google_by_email:
            skipped.append({"email": email, "reason": "google_identity_present", "qkaUserId": str(user.id)})
            continue
        row = session.execute(
            select(AllowedEmail).where(AllowedEmail.email_normalized == email)
        ).scalar_one_or_none()
        if row is None:
            continue
        chat = session.execute(
            select(ChatAccess).where(ChatAccess.allowed_email_id == row.id)
        ).scalar_one_or_none()
        membership = session.execute(
            select(ChatgptMembership).where(ChatgptMembership.allowed_email_id == row.id)
        ).scalar_one_or_none()
        if _allowlist_has_independent_use(session, row, chat=chat, membership=membership):
            skipped.append(
                {
                    "email": email,
                    "reason": "independent_allowlist_use",
                    "allowlistId": str(row.id),
                    "qkaUserId": str(user.id),
                }
            )
            continue
        backup = {
            "allowlistId": str(row.id),
            "email": row.email_normalized,
            "displayName": row.display_name,
            "enabled": bool(row.enabled),
            "createdAt": row.created_at.isoformat() if row.created_at else None,
            "chatAccessId": str(chat.id) if chat else None,
            "localChatEnabled": bool(chat.local_chat_enabled) if chat else False,
            "mcpEnabled": bool(chat.mcp_enabled) if chat else False,
            "membershipStatus": int(membership.status) if membership else None,
            "canonicalQkaUserId": str(user.id),
        }
        _audit(
            session,
            action="RedundantAdContactAllowlistCleanup",
            actor_id=actor_id,
            employee_email=row.email_normalized,
            target_user_id=user.id,
            detail=f"allowlist={row.id}; dry_run={dry_run}",
            before=backup,
            after={"removed": not dry_run, "preservedQkaUserId": str(user.id)},
        )
        removed.append(backup)
        if dry_run:
            continue
        # No independent SourceGrant rows (guarded above); still clear any zero-cap leftovers.
        for grant in session.execute(
            select(SourceGrant).where(SourceGrant.allowed_email_id == row.id)
        ).scalars().all():
            session.delete(grant)
        if membership is not None:
            session.delete(membership)
        if chat is not None:
            session.delete(chat)
        session.delete(row)
    session.flush()
    return {
        "dryRun": dry_run,
        "removedCount": len(removed),
        "removed": removed,
        "skippedCount": len(skipped),
        "skipped": skipped,
    }
