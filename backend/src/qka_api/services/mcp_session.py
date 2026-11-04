"""MCP OAuth helpers: QKA browser session reuse + per-user activity stamps."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_shared.allowlist import (
    ACCOUNT_ENABLED,
    LINKED,
    PROVIDER_GOOGLE,
    evaluate_user,
    load_active_user,
)
from qka_shared.models import ExternalIdentity, QkaUser


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_verified_google_identity(session: Session, user_id: uuid.UUID) -> bool:
    row = session.execute(
        select(ExternalIdentity.id).where(
            ExternalIdentity.qka_user_id == user_id,
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
        )
    ).scalar_one_or_none()
    return row is not None


def google_login_hint(session: Session, user_id: uuid.UUID) -> str | None:
    identity = session.execute(
        select(ExternalIdentity)
        .options(selectinload(ExternalIdentity.google))
        .where(
            ExternalIdentity.qka_user_id == user_id,
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
        )
        .limit(1)
    ).scalar_one_or_none()
    if identity is None:
        return None
    if identity.google is not None and identity.google.primary_email:
        return identity.google.primary_email
    return identity.primary_email


def resolve_reusable_mcp_user(session: Session, user_id: uuid.UUID) -> QkaUser | None:
    """Active QKA user with verified Google identity eligible to authorize MCP without re-prompting Google."""
    user = load_active_user(session, user_id)
    if user is None:
        return None
    access = evaluate_user(session, user.id)
    if not access.allowed:
        return None
    if not has_verified_google_identity(session, user.id):
        return None
    return user


def assert_mcp_token_subject_active(session: Session, user_id: uuid.UUID) -> str | None:
    """Return an OAuth error_description when the bound user may no longer receive tokens."""
    user = load_active_user(session, user_id)
    if user is None:
        return "The linked QKA account is not active."
    access = evaluate_user(session, user.id)
    if not access.allowed:
        if access.reason == "disabled":
            return "Access for this email is disabled."
        return "Access denied."
    if not has_verified_google_identity(session, user.id):
        return "Verified Google identity is required."
    return None


def record_mcp_connection(session: Session, user_id: uuid.UUID) -> None:
    user = session.get(QkaUser, user_id)
    if user is None:
        return
    now = _now()
    if user.mcp_connected_at is None:
        user.mcp_connected_at = now
    user.mcp_last_activity_at = now
    user.updated_at = now
    session.flush()


def record_mcp_activity(session: Session, user_id: uuid.UUID) -> None:
    user = session.get(QkaUser, user_id)
    if user is None:
        return
    now = _now()
    user.mcp_last_activity_at = now
    user.updated_at = now
    session.flush()
