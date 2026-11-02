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
from qka_shared.chat_access import get_or_create_chat_access, set_chat_access
from qka_shared.email_address import try_normalize
from qka_shared.models import (
    AllowedEmail,
    ChatAccess,
    ChatgptMembership,
    ExternalIdentity,
    MembershipImportBatch,
    QkaUser,
    SourceGrantAudit,
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
            "sidSuffix": (ad.stable_external_id or "").split("-")[-1] if ad.stable_external_id else None,
            "linked": True,
        }
        for ad in user.identities
        if ad.provider == PROVIDER_AD and ad.link_status == LINKED and ad.account_status == ACCOUNT_ENABLED
    ]


def _membership_label(status: int) -> str:
    if status == MEMBERSHIP_MEMBER:
        return "verified_member"
    if status == MEMBERSHIP_NONMEMBER:
        return "verified_nonmember"
    return "unknown"


def _user_dto(
    row: AllowedEmail,
    *,
    user: QkaUser | None,
    chat: ChatAccess | None,
    membership: ChatgptMembership | None,
) -> dict:
    if not row.enabled:
        identity_status = "disabled"
        account_status = "disabled"
    elif user is not None:
        identity_status = "linked"
        account_status = "active"
    else:
        identity_status = "pending"
        account_status = "pending"
    admin_name = (row.display_name or "").strip() or row.email_normalized.split("@", 1)[0]
    mem_status = membership.status if membership is not None else MEMBERSHIP_UNKNOWN
    return {
        "allowlistId": str(row.id),
        "qkaUserId": str(user.id) if user is not None else None,
        "displayName": admin_name,
        "googleProfileName": (row.google_profile_name or "").strip() or None,
        "primaryEmail": row.email_normalized,
        "googleSub": _google_sub(user),
        "googleLinked": identity_status == "linked",
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
        "canGrant": identity_status in {"linked", "pending"} and bool(row.enabled),
        "adIdentities": _ad_idents(user),
        "superAdmin": bool(user.is_super_admin) if user is not None else False,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


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
    rows = session.execute(select(AllowedEmail).order_by(AllowedEmail.email_normalized)).scalars().all()
    by_email = _google_users_by_email(session)
    chat_by_id = {c.allowed_email_id: c for c in session.execute(select(ChatAccess)).scalars().all()}
    mem_by_id = {m.allowed_email_id: m for m in session.execute(select(ChatgptMembership)).scalars().all()}

    items = []
    for row in rows:
        linked = by_email.get(row.email_normalized) or []
        user = linked[0] if linked else None
        dto = _user_dto(row, user=user, chat=chat_by_id.get(row.id), membership=mem_by_id.get(row.id))
        items.append(dto)

    q = (search or "").strip().lower()
    if q:
        items = [
            item
            for item in items
            if q in item["primaryEmail"].lower()
            or q in (item["displayName"] or "").lower()
            or q in (item.get("chatgptMemberName") or "").lower()
        ]
    if account_status:
        items = [item for item in items if item["accountStatus"] == account_status.strip().lower()]
    if identity_status:
        want = identity_status.strip().lower()
        if want == "pending_google_login":
            want = "pending"
        items = [item for item in items if item["identityStatus"] == want]
    if chatgpt_membership:
        items = [item for item in items if item["chatgptMembership"] == chatgpt_membership.strip().lower()]

    total = len(items)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "membershipSync": membership_sync_info(),
    }


def create_user(
    session: Session,
    actor: QkaUser,
    *,
    display_name: str,
    email_raw: str,
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
        detail=f"name={name}; localChat={bool(local_chat_enabled)}; mcp={bool(mcp_enabled)}",
        after={
            "displayName": name,
            "email": email,
            "enabled": bool(enabled),
            "localChat": bool(local_chat_enabled),
            "mcp": bool(mcp_enabled),
        },
    )
    return _user_dto(row, user=None, chat=chat, membership=None)


def update_user(
    session: Session,
    actor: QkaUser,
    allowlist_id: uuid.UUID,
    *,
    display_name: str | None = None,
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
    }
    chat = get_or_create_chat_access(session, row.id)
    before["localChat"] = chat.local_chat_enabled
    before["mcp"] = chat.mcp_enabled

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValueError("Full name is required.")
        row.display_name = name[:256]
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
    if before["displayName"] != row.display_name:
        actions.append("UserEdited")
    if before["enabled"] != row.enabled:
        actions.append("UserEnabled" if row.enabled else "UserDisabled")
    if before["localChat"] != chat.local_chat_enabled or before["mcp"] != chat.mcp_enabled:
        actions.append("ChannelAccessChanged")
    after = {
        "displayName": row.display_name,
        "enabled": row.enabled,
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
