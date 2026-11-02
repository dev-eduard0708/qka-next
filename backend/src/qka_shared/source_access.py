"""Shared Synology document authorization (REST + MCP).

Two layers:
  A) QKA login — verified Google + enabled allowlist (checked by callers via allowlist.evaluate_user)
  B) QKA document access — personal source ACL OR corporate designated grant

Never treat SMB service-account read authority as an employee grant.
Never merge Google and AD identities by email.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import IntEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_shared.acl import SourceAclState, evaluate_needles
from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD
from qka_shared.models import QkaUser, SourceGrant, SourceScope, SynologyDocumentContent, SynologyFileInventory


class TrustClass(IntEnum):
    PERSONAL = 0
    CORPORATE_DESIGNATED = 1


class ScopeStatus(IntEnum):
    ACTIVE = 0
    REVOKED = 1


class GrantStatus(IntEnum):
    ACTIVE = 0
    DISABLED = 1
    REVOKED = 2


class PathMode(IntEnum):
    ALL_IN_SCOPE = 0
    SELECTED = 1


class Capability:
    READ = "read"
    SEARCH = "search"
    PREVIEW = "preview"


FILE_TYPE_EXTENSIONS: dict[str, set[str]] = {
    "excel": {"xlsx", "xls", "xlsm", "xlsb"},
    "word": {"docx", "doc"},
    "pdf": {"pdf"},
    "powerpoint": {"pptx", "ppt"},
    "images": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"},
    "csv": {"csv"},
    "text": {"txt", "md", "log", "json", "xml", "csv"},
    "other": set(),
}


@dataclass(frozen=True)
class EffectiveAccess:
    allowed: bool
    qka_grant: str
    source_permission: str
    effective: str
    denial_reason: str | None
    covering_scope_id: uuid.UUID | None = None
    grant_id: uuid.UUID | None = None


def normalize_prefix(prefix: str) -> str:
    return (prefix or "").replace("/", "\\").strip().strip("\\").upper()


def under_prefix(path: str, prefix: str) -> bool:
    if not prefix:
        return True
    normalized = normalize_prefix(path)
    return normalized == prefix or normalized.startswith(prefix + "\\")


def classify_extension(extension: str) -> str:
    ext = (extension or "").lower().lstrip(".")
    for name, exts in FILE_TYPE_EXTENSIONS.items():
        if name == "other":
            continue
        if ext in exts:
            return name
    return "other"


def parse_json_list(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def acl_needles(user: QkaUser) -> list[str]:
    needles: list[str] = []
    for identity in user.identities:
        if identity.link_status != LINKED or identity.account_status != ACCOUNT_ENABLED:
            continue
        if identity.provider != PROVIDER_AD:
            continue
        for value in (identity.domain_username, identity.stable_external_id, identity.upn):
            text = (value or "").strip()
            if text and text not in needles:
                needles.append(text)
    return needles


def grant_has_capability(grant: SourceGrant, capability: str) -> bool:
    cap = (capability or Capability.PREVIEW).lower()
    if cap == Capability.SEARCH:
        return bool(grant.capability_search)
    if cap == Capability.READ:
        return bool(grant.capability_read)
    if cap == Capability.PREVIEW:
        return bool(grant.capability_preview or grant.capability_read)
    return False


def grant_matches_file_type(grant: SourceGrant, extension: str) -> bool:
    types = [item.lower() for item in parse_json_list(grant.file_types_json)]
    if not types or "all" in types:
        return True
    return classify_extension(extension) in set(types)


def grant_matches_path(grant: SourceGrant, scope: SourceScope, relative_path_normalized: str) -> bool:
    path = normalize_prefix(relative_path_normalized)
    scope_prefix = normalize_prefix(scope.folder_prefix_normalized)
    if not under_prefix(path, scope_prefix):
        return False
    mode = int(getattr(grant, "path_mode", PathMode.ALL_IN_SCOPE) or PathMode.ALL_IN_SCOPE)
    if mode == PathMode.ALL_IN_SCOPE:
        return True
    selected = [normalize_prefix(item) for item in parse_json_list(grant.selected_paths_json)]
    if not selected:
        return False
    include_sub = bool(getattr(grant, "include_subfolders", True))
    for item in selected:
        if not item:
            continue
        if path == item:
            return True
        if include_sub and under_prefix(path, item):
            return True
        if not include_sub:
            # Exact selected folder only: allow files directly under it (one level).
            parent = "\\".join(path.split("\\")[:-1])
            if parent == item:
                return True
    return False


def load_active_corporate_scopes(
    session: Session,
    *,
    approved_share_id: uuid.UUID,
    relative_path_normalized: str,
) -> list[SourceScope]:
    rows = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == approved_share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
            SourceScope.trust_class == TrustClass.CORPORATE_DESIGNATED,
        )
    ).scalars().all()
    path = normalize_prefix(relative_path_normalized)
    covering = [row for row in rows if under_prefix(path, normalize_prefix(row.folder_prefix_normalized))]
    covering.sort(key=lambda row: len(normalize_prefix(row.folder_prefix_normalized)), reverse=True)
    return covering


def load_active_grant(session: Session, scope_id: uuid.UUID, user_id: uuid.UUID) -> SourceGrant | None:
    return session.execute(
        select(SourceGrant).where(
            SourceGrant.scope_id == scope_id,
            SourceGrant.qka_user_id == user_id,
            SourceGrant.status == GrantStatus.ACTIVE,
        )
    ).scalar_one_or_none()


def evaluate_document_access(
    session: Session,
    user: QkaUser,
    inventory: SynologyFileInventory,
    content: SynologyDocumentContent,
    *,
    capability: str = Capability.PREVIEW,
) -> EffectiveAccess:
    content_ok = content.status == 1 and bool(content.extracted_text)
    if not content_ok:
        return EffectiveAccess(
            allowed=False,
            qka_grant="none",
            source_permission="n/a",
            effective="deny",
            denial_reason="content_not_verified",
        )

    needles = acl_needles(user)
    source_acl = evaluate_needles(inventory, needles)
    source_label = {
        SourceAclState.MISSING: "missing",
        SourceAclState.DENIED: "denied",
        SourceAclState.ALLOWED: "allowed",
    }[source_acl]

    # Explicit source DENY always overrides any QKA grant.
    if source_acl == SourceAclState.DENIED:
        return EffectiveAccess(
            allowed=False,
            qka_grant="blocked_by_source_deny",
            source_permission=source_label,
            effective="deny",
            denial_reason="source_acl_deny",
        )

    scopes = load_active_corporate_scopes(
        session,
        approved_share_id=content.approved_share_id,
        relative_path_normalized=content.relative_path_normalized,
    )
    for scope in scopes:
        if not scope.service_read_verified:
            continue
        if not scope.approval_reference.strip() or scope.approved_at is None:
            continue
        grant = load_active_grant(session, scope.id, user.id)
        if grant is None:
            continue
        if not grant_has_capability(grant, capability):
            return EffectiveAccess(
                allowed=False,
                qka_grant="insufficient_capability",
                source_permission=source_label,
                effective="deny",
                denial_reason="grant_missing_capability",
                covering_scope_id=scope.id,
                grant_id=grant.id,
            )
        if not grant_matches_path(grant, scope, content.relative_path_normalized):
            continue
        if not grant_matches_file_type(grant, content.extension):
            continue
        return EffectiveAccess(
            allowed=True,
            qka_grant="corporate_delegated",
            source_permission=source_label,
            effective="allow",
            denial_reason=None,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )

    # Personal workspace / individual source ACL path.
    if source_acl == SourceAclState.ALLOWED:
        return EffectiveAccess(
            allowed=True,
            qka_grant="none_personal_acl",
            source_permission=source_label,
            effective="allow",
            denial_reason=None,
        )

    if not needles:
        reason = "no_linked_ad_identity"
    elif source_acl == SourceAclState.MISSING:
        reason = "source_acl_unknown_or_stale"
    else:
        reason = "no_qka_grant_and_no_source_acl"
    return EffectiveAccess(
        allowed=False,
        qka_grant="none",
        source_permission=source_label,
        effective="deny",
        denial_reason=reason,
    )


def authorize_document(
    session: Session,
    user: QkaUser,
    inventory: SynologyFileInventory,
    content: SynologyDocumentContent,
    *,
    capability: str = Capability.PREVIEW,
) -> bool:
    return evaluate_document_access(session, user, inventory, content, capability=capability).allowed
