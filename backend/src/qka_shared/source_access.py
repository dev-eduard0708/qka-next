"""Shared Synology document authorization (REST + MCP + Local AI Chat).

Two layers:
  A) QKA login — verified Google + enabled allowlist (checked by callers via allowlist.evaluate_user)
  B) QKA document access — Source ACL (AD/NAS) OR QKA Managed (explicit grants)

QKA Managed does not require a linked AD identity or NAS account.
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
from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, USER_ACTIVE
from qka_shared.models import (
    DEVICE_REVOKED,
    DesktopDevice,
    QkaUser,
    SourceGrant,
    SourceScope,
    SynologyApprovedShare,
    SynologyDocumentContent,
    SynologyFileInventory,
)

ACCESS_MODEL_SOURCE_ACL = 0
ACCESS_MODEL_QKA_MANAGED = 1


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
    """Document capabilities. VIEW covers search/preview/indexed text; DOWNLOAD is separate."""

    VIEW = "view"
    DOWNLOAD = "download"
    # Legacy aliases accepted by grant_has_capability / callers during transition.
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


def _grant_view(grant: SourceGrant) -> bool:
    # New column or any legacy R/S/P (pre-migration / dual-write mirror).
    return bool(
        getattr(grant, "capability_view", False)
        or grant.capability_read
        or grant.capability_search
        or grant.capability_preview
    )


def _grant_download(grant: SourceGrant) -> bool:
    return bool(getattr(grant, "capability_download", False))


def grant_has_capability(grant: SourceGrant, capability: str) -> bool:
    cap = (capability or Capability.VIEW).lower()
    if cap in {Capability.VIEW, Capability.SEARCH, Capability.PREVIEW, Capability.READ}:
        return _grant_view(grant)
    if cap == Capability.DOWNLOAD:
        return _grant_download(grant)
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


def is_authorized_managed_scope(scope: SourceScope) -> bool:
    return (
        scope.trust_class == TrustClass.CORPORATE_DESIGNATED
        and scope.status == ScopeStatus.ACTIVE
        and bool(scope.service_read_verified)
        and bool((scope.approval_reference or "").strip())
        and scope.approved_at is not None
    )


def share_is_qka_managed(share: SynologyApprovedShare | None) -> bool:
    if share is None:
        return False
    if int(getattr(share, "access_model", ACCESS_MODEL_SOURCE_ACL) or ACCESS_MODEL_SOURCE_ACL) != ACCESS_MODEL_QKA_MANAGED:
        return False
    return (
        bool(getattr(share, "qka_managed_service_read_verified", False))
        and bool((getattr(share, "qka_managed_approval_reference", "") or "").strip())
        and getattr(share, "qka_managed_enabled_at", None) is not None
    )


def has_verified_google_identity(user: QkaUser) -> bool:
    return any(
        identity.provider == PROVIDER_GOOGLE
        and identity.link_status == LINKED
        and identity.account_status == ACCOUNT_ENABLED
        for identity in (user.identities or [])
    )


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


def _load_share_grants_for_user(
    session: Session,
    share_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[tuple[SourceScope, SourceGrant]]:
    scopes = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
        )
    ).scalars().all()
    if not scopes:
        return []
    by_id = {row.id: row for row in scopes}
    grants = session.execute(
        select(SourceGrant).where(
            SourceGrant.scope_id.in_(list(by_id)),
            SourceGrant.qka_user_id == user_id,
            SourceGrant.status.in_((GrantStatus.ACTIVE, GrantStatus.DISABLED)),
        )
    ).scalars().all()
    return [(by_id[grant.scope_id], grant) for grant in grants if grant.scope_id in by_id]


def _deny(
    *,
    reason: str,
    qka_grant: str = "none",
    source_permission: str = "n/a",
    covering_scope_id: uuid.UUID | None = None,
    grant_id: uuid.UUID | None = None,
) -> EffectiveAccess:
    return EffectiveAccess(
        allowed=False,
        qka_grant=qka_grant,
        source_permission=source_permission,
        effective="deny",
        denial_reason=reason,
        covering_scope_id=covering_scope_id,
        grant_id=grant_id,
    )


def _evaluate_qka_managed_grants(
    pairs: list[tuple[SourceScope, SourceGrant]],
    *,
    relative_path: str,
    extension: str,
    capability: str,
    source_permission: str,
) -> EffectiveAccess:
    missing_cap: tuple[SourceScope, SourceGrant] | None = None
    type_mismatch: tuple[SourceScope, SourceGrant] | None = None
    outside: tuple[SourceScope, SourceGrant] | None = None
    disabled: tuple[SourceScope, SourceGrant] | None = None
    for scope, grant in pairs:
        if grant.status == GrantStatus.DISABLED:
            disabled = (scope, grant)
            continue
        if grant.status != GrantStatus.ACTIVE:
            continue
        if not grant_matches_path(grant, scope, relative_path):
            outside = (scope, grant)
            continue
        if not grant_matches_file_type(grant, extension):
            type_mismatch = (scope, grant)
            continue
        if not grant_has_capability(grant, capability):
            missing_cap = (scope, grant)
            continue
        return EffectiveAccess(
            allowed=True,
            qka_grant="qka_managed",
            source_permission=source_permission,
            effective="allow",
            denial_reason=None,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )
    if missing_cap is not None:
        scope, grant = missing_cap
        reason = (
            "download_requires_explicit_grant"
            if capability == Capability.DOWNLOAD
            else "grant_missing_capability"
        )
        return _deny(
            reason=reason,
            qka_grant="insufficient_capability",
            source_permission=source_permission,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )
    if type_mismatch is not None:
        scope, grant = type_mismatch
        return _deny(
            reason="file_type_not_permitted",
            qka_grant="none",
            source_permission=source_permission,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )
    if outside is not None:
        scope, grant = outside
        return _deny(
            reason="outside_allowed_scope",
            qka_grant="none",
            source_permission=source_permission,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )
    if disabled is not None:
        scope, grant = disabled
        return _deny(
            reason="qka_grant_disabled",
            qka_grant="disabled",
            source_permission=source_permission,
            covering_scope_id=scope.id,
            grant_id=grant.id,
        )
    return _deny(reason="qka_grant_missing", source_permission=source_permission)


def evaluate_document_access(
    session: Session,
    user: QkaUser,
    inventory: SynologyFileInventory,
    content: SynologyDocumentContent,
    *,
    capability: str = Capability.VIEW,
    require_extracted: bool | None = None,
) -> EffectiveAccess:
    cap = (capability or Capability.VIEW).lower()
    wants_download = cap == Capability.DOWNLOAD
    # Indexed-text operations require verified extracted content in PostgreSQL.
    # Original-file download and derived formatted previews do not.
    if require_extracted is None:
        require_extracted = not wants_download
    if require_extracted:
        content_ok = content.status == 1 and bool(content.extracted_text)
        if not content_ok:
            return _deny(reason="content_not_verified")

    needles = acl_needles(user)
    source_acl = evaluate_needles(inventory, needles)
    source_label = {
        SourceAclState.MISSING: "missing",
        SourceAclState.DENIED: "denied",
        SourceAclState.ALLOWED: "allowed",
    }[source_acl]

    # Explicit source DENY overrides grants only when an AD identity can be matched.
    if needles and source_acl == SourceAclState.DENIED:
        return _deny(
            reason="source_acl_deny",
            qka_grant="blocked_by_source_deny",
            source_permission=source_label,
        )

    share = session.get(SynologyApprovedShare, content.approved_share_id)
    corporate = load_active_corporate_scopes(
        session,
        approved_share_id=content.approved_share_id,
        relative_path_normalized=content.relative_path_normalized,
    )
    authorized_corporate = [row for row in corporate if is_authorized_managed_scope(row)]
    qka_managed = share_is_qka_managed(share) or bool(authorized_corporate)
    managed_permission = source_label if needles else "qka_managed"

    if qka_managed:
        if not has_verified_google_identity(user):
            return _deny(reason="pending_google_identity", source_permission=managed_permission)
        pairs = _load_share_grants_for_user(session, content.approved_share_id, user.id)
        if not share_is_qka_managed(share):
            allowed_ids = {row.id for row in authorized_corporate}
            pairs = [(scope, grant) for scope, grant in pairs if scope.id in allowed_ids]
        return _evaluate_qka_managed_grants(
            pairs,
            relative_path=content.relative_path_normalized,
            extension=content.extension,
            capability=cap,
            source_permission=managed_permission,
        )

    # Source ACL mode: personal NAS/AD rights authorize VIEW only — never Download.
    if not wants_download and source_acl == SourceAclState.ALLOWED:
        return EffectiveAccess(
            allowed=True,
            qka_grant="none_personal_acl",
            source_permission=source_label,
            effective="allow",
            denial_reason=None,
        )

    if wants_download:
        reason = "download_requires_explicit_grant"
    elif not needles:
        reason = "no_linked_ad_identity"
    elif source_acl == SourceAclState.MISSING:
        reason = "source_acl_unknown_or_stale"
    else:
        reason = "source_policy_not_configured"
    return _deny(reason=reason, source_permission=source_label)


def authorize_document(
    session: Session,
    user: QkaUser,
    inventory: SynologyFileInventory,
    content: SynologyDocumentContent,
    *,
    capability: str = Capability.VIEW,
    require_extracted: bool | None = None,
) -> bool:
    return evaluate_document_access(
        session,
        user,
        inventory,
        content,
        capability=capability,
        require_extracted=require_extracted,
    ).allowed


def evaluate_desktop_source_access(
    session: Session,
    user: QkaUser | None,
    device: DesktopDevice | None,
    *,
    capability: str = Capability.VIEW,
) -> EffectiveAccess:
    """Authorize retrieval of a workstation agent's indexed source.

    Ownership (device.qka_user_id) is association only — never deploy authority.
    Default deny when unassigned, revoked, login-disabled, or caller is not the owner.
    Indexing alone never grants document access. Synology/ACL rules remain separate.
    """
    _ = session  # reserved for future grant/ACL overlays on desktop sources
    _ = capability
    if device is None or device.status == DEVICE_REVOKED:
        return _deny(reason="desktop_device_unavailable")
    if device.qka_user_id is None:
        return _deny(reason="desktop_source_unassigned")
    if user is None:
        return _deny(reason="qka_user_required")
    if int(getattr(user, "status", -1)) != USER_ACTIVE:
        return _deny(reason="qka_user_inactive")
    if not bool(getattr(user, "login_enabled", False)):
        return _deny(reason="qka_login_disabled")
    if user.id != device.qka_user_id:
        return _deny(reason="desktop_source_not_owner")
    return EffectiveAccess(
        allowed=True,
        qka_grant="desktop_owner",
        source_permission="owner",
        effective="allow",
        denial_reason=None,
    )


def authorize_desktop_source(
    session: Session,
    user: QkaUser | None,
    device: DesktopDevice | None,
    *,
    capability: str = Capability.VIEW,
) -> bool:
    return evaluate_desktop_source_access(
        session, user, device, capability=capability
    ).allowed
