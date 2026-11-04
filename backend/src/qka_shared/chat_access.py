"""QKA chat channel permissions (Local AI Chat / ChatGPT MCP). Independent of ChatGPT Business seats."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_shared.allowlist import USER_ACTIVE, evaluate_user, load_verified_emails
from qka_shared.models import AllowedEmail, ChatAccess, QkaUser, SourceGrant, SourceGrantAudit
from qka_shared.source_access import GrantStatus


@dataclass(frozen=True)
class ChannelAccess:
    allowed: bool
    reason: str
    local_chat: bool = False
    mcp: bool = False
    allowlist_id: uuid.UUID | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chat_row_for_emails(session: Session, emails: set[str]) -> tuple[AllowedEmail | None, ChatAccess | None]:
    if not emails:
        return None, None
    allow = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized.in_(emails), AllowedEmail.enabled.is_(True))
    ).scalars().first()
    if allow is None:
        return None, None
    chat = session.execute(select(ChatAccess).where(ChatAccess.allowed_email_id == allow.id)).scalar_one_or_none()
    return allow, chat


def resolve_channel_access(session: Session, user_id: uuid.UUID) -> ChannelAccess:
    user = session.get(QkaUser, user_id)
    if user is None:
        return ChannelAccess(False, "inactive")
    if user.is_super_admin:
        return ChannelAccess(True, "super_admin", local_chat=True, mcp=True)
    # Disabled / non-login QKA accounts must not keep MCP tool access (403-style deny, no re-login).
    if user.status != USER_ACTIVE or not user.login_enabled:
        return ChannelAccess(False, "inactive")
    login = evaluate_user(session, user_id)
    if not login.allowed:
        return ChannelAccess(False, login.reason)
    emails = load_verified_emails(session, user_id)
    allow, chat = _chat_row_for_emails(session, emails)
    if allow is None:
        return ChannelAccess(False, "unlisted")
    if chat is None:
        return ChannelAccess(True, "allowed", local_chat=False, mcp=False, allowlist_id=allow.id)
    return ChannelAccess(
        True,
        "allowed",
        local_chat=bool(chat.local_chat_enabled),
        mcp=bool(chat.mcp_enabled),
        allowlist_id=allow.id,
    )


def require_local_chat(session: Session, user_id: uuid.UUID) -> ChannelAccess:
    access = resolve_channel_access(session, user_id)
    if not access.allowed:
        return access
    if not access.local_chat:
        return ChannelAccess(False, "local_chat_disabled", local_chat=False, mcp=access.mcp, allowlist_id=access.allowlist_id)
    return access


def require_mcp_chat(session: Session, user_id: uuid.UUID) -> ChannelAccess:
    access = resolve_channel_access(session, user_id)
    if not access.allowed:
        return access
    if not access.mcp:
        return ChannelAccess(False, "mcp_disabled", local_chat=access.local_chat, mcp=False, allowlist_id=access.allowlist_id)
    return access


def channel_deny_message(reason: str) -> str:
    if reason == "local_chat_disabled":
        return "QKA Local AI Chat is not enabled for this account."
    if reason == "mcp_disabled":
        return "ChatGPT Business MCP access is not enabled for this QKA account."
    if reason == "no_verified_email":
        return "Verified email is not available for this identity."
    if reason == "disabled":
        return "Access for this email is disabled."
    if reason == "inactive":
        return "The linked QKA account is not active."
    return "Access denied."


def get_or_create_chat_access(session: Session, allowlist_id: uuid.UUID) -> ChatAccess:
    row = session.execute(select(ChatAccess).where(ChatAccess.allowed_email_id == allowlist_id)).scalar_one_or_none()
    if row is not None:
        return row
    now = _now()
    row = ChatAccess(
        id=uuid.uuid4(),
        allowed_email_id=allowlist_id,
        qka_user_id=None,
        local_chat_enabled=False,
        mcp_enabled=False,
        created_at=now,
        updated_at=now,
        updated_by_user_id=None,
    )
    session.add(row)
    session.flush()
    return row


def get_or_create_chat_access_for_user(session: Session, qka_user_id: uuid.UUID) -> ChatAccess:
    """Chat channel row for AD-enrolled users (no AllowedEmail required). Defaults OFF."""
    row = session.execute(select(ChatAccess).where(ChatAccess.qka_user_id == qka_user_id)).scalar_one_or_none()
    if row is not None:
        return row
    now = _now()
    row = ChatAccess(
        id=uuid.uuid4(),
        allowed_email_id=None,
        qka_user_id=qka_user_id,
        local_chat_enabled=False,
        mcp_enabled=False,
        created_at=now,
        updated_at=now,
        updated_by_user_id=None,
    )
    session.add(row)
    session.flush()
    return row


def set_chat_access(
    session: Session,
    actor_id: uuid.UUID,
    allowlist_id: uuid.UUID,
    *,
    local_chat_enabled: bool,
    mcp_enabled: bool,
) -> ChatAccess:
    allow = session.get(AllowedEmail, allowlist_id)
    if allow is None:
        raise LookupError("Allowlist entry was not found.")
    row = get_or_create_chat_access(session, allowlist_id)
    before = {"localChat": row.local_chat_enabled, "mcp": row.mcp_enabled}
    row.local_chat_enabled = bool(local_chat_enabled)
    row.mcp_enabled = bool(mcp_enabled)
    row.updated_at = _now()
    row.updated_by_user_id = actor_id
    session.flush()
    after = {"localChat": row.local_chat_enabled, "mcp": row.mcp_enabled}
    actions = []
    if before["localChat"] != after["localChat"]:
        actions.append("LocalChatEnabled" if after["localChat"] else "LocalChatDisabled")
    if before["mcp"] != after["mcp"]:
        actions.append("McpEnabled" if after["mcp"] else "McpDisabled")
    for action in actions or ["ChatAccessUpdated"]:
        session.add(
            SourceGrantAudit(
                id=uuid.uuid4(),
                action=action,
                actor_user_id=actor_id,
                target_user_id=None,
                scope_id=None,
                grant_id=None,
                detail=f"email={allow.email_normalized}",
                employee_email=allow.email_normalized,
                share_name="",
                before_detail=str(before),
                after_detail=str(after),
                created_at=_now(),
            )
        )
    return row


def set_chat_access_for_user(
    session: Session,
    actor_id: uuid.UUID,
    qka_user_id: uuid.UUID,
    *,
    local_chat_enabled: bool,
    mcp_enabled: bool,
) -> ChatAccess:
    """Update Local AI / MCP flags for an AD-enrolled QkaUser (no AllowedEmail required)."""
    user = session.get(QkaUser, qka_user_id)
    if user is None:
        raise LookupError("User was not found.")
    row = get_or_create_chat_access_for_user(session, qka_user_id)
    before = {"localChat": row.local_chat_enabled, "mcp": row.mcp_enabled}
    row.local_chat_enabled = bool(local_chat_enabled)
    row.mcp_enabled = bool(mcp_enabled)
    row.updated_at = _now()
    row.updated_by_user_id = actor_id
    session.flush()
    after = {"localChat": row.local_chat_enabled, "mcp": row.mcp_enabled}
    actions = []
    if before["localChat"] != after["localChat"]:
        actions.append("LocalChatEnabled" if after["localChat"] else "LocalChatDisabled")
    if before["mcp"] != after["mcp"]:
        actions.append("McpEnabled" if after["mcp"] else "McpDisabled")
    email = (user.contact_email or "").strip()
    for action in actions or ["ChatAccessUpdated"]:
        session.add(
            SourceGrantAudit(
                id=uuid.uuid4(),
                action=action,
                actor_user_id=actor_id,
                target_user_id=qka_user_id,
                scope_id=None,
                grant_id=None,
                detail=f"qkaUserId={qka_user_id}",
                employee_email=email,
                share_name="",
                before_detail=str(before),
                after_detail=str(after),
                created_at=_now(),
            )
        )
    return row


def activate_pending_grants_for_email(session: Session, email_normalized: str, user_id: uuid.UUID) -> int:
    """Bind pending allowlist grants to the verified Google user after first login."""
    allow = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized == email_normalized)
    ).scalar_one_or_none()
    if allow is None:
        return 0
    rows = session.execute(
        select(SourceGrant).where(
            SourceGrant.allowed_email_id == allow.id,
            SourceGrant.status == GrantStatus.ACTIVE,
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        if row.qka_user_id is None:
            row.qka_user_id = user_id
            row.updated_at = _now()
            changed += 1
        elif row.qka_user_id != user_id:
            # Do not steal grants from another linked user.
            continue
    if changed:
        session.flush()
    return changed
