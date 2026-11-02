"""Super Admin Source Access Management (scopes, grants, diagnostics, audit)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, load_verified_emails
from qka_shared.config import Settings
from qka_shared.email_address import try_normalize
from qka_shared.models import (
    AllowedEmail,
    ExternalIdentity,
    QkaUser,
    SourceGrant,
    SourceGrantAudit,
    SourceScope,
    SynologyApprovedShare,
    SynologyDocumentContent,
    SynologyFileInventory,
)
from qka_shared.source_access import (
    FILE_TYPE_EXTENSIONS,
    GrantStatus,
    PathMode,
    ScopeStatus,
    TrustClass,
    evaluate_document_access,
    normalize_prefix,
    parse_json_list,
    under_prefix,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _grant_snapshot(row: SourceGrant) -> dict:
    return {
        "pathMode": "all" if int(getattr(row, "path_mode", 0) or 0) == PathMode.ALL_IN_SCOPE else "selected",
        "includeSubfolders": bool(getattr(row, "include_subfolders", True)),
        "selectedPaths": parse_json_list(getattr(row, "selected_paths_json", "[]")),
        "fileTypes": parse_json_list(getattr(row, "file_types_json", "[]")),
        "capabilityRead": bool(row.capability_read),
        "capabilitySearch": bool(row.capability_search),
        "capabilityPreview": bool(row.capability_preview),
        "status": {GrantStatus.ACTIVE: "active", GrantStatus.DISABLED: "disabled", GrantStatus.REVOKED: "revoked"}.get(
            row.status, "unknown"
        ),
        "reason": row.reason,
    }


def _audit(
    session: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    target_user_id: uuid.UUID | None = None,
    scope_id: uuid.UUID | None = None,
    grant_id: uuid.UUID | None = None,
    detail: str = "",
    employee_email: str = "",
    share_name: str = "",
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    session.add(
        SourceGrantAudit(
            id=uuid.uuid4(),
            action=action,
            actor_user_id=actor_id,
            target_user_id=target_user_id,
            scope_id=scope_id,
            grant_id=grant_id,
            detail=(detail or "")[:2000],
            employee_email=(employee_email or "")[:256],
            share_name=(share_name or "")[:256],
            before_detail=json.dumps(before or {}, separators=(",", ":"))[:8000],
            after_detail=json.dumps(after or {}, separators=(",", ":"))[:8000],
            created_at=_now(),
        )
    )


def _scope_dto(row: SourceScope) -> dict:
    trust = "corporate_designated" if row.trust_class == TrustClass.CORPORATE_DESIGNATED else "personal"
    status = "active" if row.status == ScopeStatus.ACTIVE else "revoked"
    return {
        "id": str(row.id),
        "approvedShareId": str(row.approved_share_id),
        "folderPrefixNormalized": row.folder_prefix_normalized,
        "trustClass": trust,
        "status": status,
        "displayName": row.display_name,
        "sourceOwnerName": row.source_owner_name,
        "sourceOwnerEmail": row.source_owner_email,
        "approvalReference": row.approval_reference,
        "approvalNotes": row.approval_notes,
        "approvedAt": row.approved_at.isoformat() if row.approved_at else None,
        "approvedByUserId": str(row.approved_by_user_id) if row.approved_by_user_id else None,
        "serviceReadVerified": bool(row.service_read_verified),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _grant_dto(row: SourceGrant) -> dict:
    status = {GrantStatus.ACTIVE: "active", GrantStatus.DISABLED: "disabled", GrantStatus.REVOKED: "revoked"}.get(
        row.status, "unknown"
    )
    snap = _grant_snapshot(row)
    return {
        "id": str(row.id),
        "scopeId": str(row.scope_id),
        "qkaUserId": str(row.qka_user_id),
        "capabilityRead": bool(row.capability_read),
        "capabilitySearch": bool(row.capability_search),
        "capabilityPreview": bool(row.capability_preview),
        "pathMode": snap["pathMode"],
        "includeSubfolders": snap["includeSubfolders"],
        "selectedPaths": snap["selectedPaths"],
        "fileTypes": snap["fileTypes"],
        "status": status,
        "reason": row.reason,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _file_icon(label: str, is_file: bool) -> str:
    if not is_file:
        return "folder"
    ext = label.rsplit(".", 1)[-1].lower() if "." in label else ""
    for name, exts in FILE_TYPE_EXTENSIONS.items():
        if ext in exts:
            return {
                "excel": "grid_on",
                "word": "description",
                "pdf": "picture_as_pdf",
                "powerpoint": "slideshow",
                "images": "image",
                "csv": "table_chart",
                "text": "article",
            }.get(name, "insert_drive_file")
    return "insert_drive_file"


def _google_users_by_verified_email(session: Session) -> dict[str, list[QkaUser]]:
    """Index linked Google users by verified email for status display only (never creates/merges identities)."""
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
    seen_user_email: set[tuple[uuid.UUID, str]] = set()
    for identity in identities:
        user = identity.user
        if user is None:
            continue
        for email in load_verified_emails(session, user.id):
            key = (user.id, email)
            if key in seen_user_email:
                continue
            seen_user_email.add(key)
            by_email.setdefault(email, []).append(user)
    for email, users in by_email.items():
        users.sort(key=lambda u: str(u.id))
    return by_email


def _employee_ad_idents(user: QkaUser) -> list[dict]:
    return [
        {
            "domainUsername": ad.domain_username,
            "sidSuffix": (ad.stable_external_id or "").split("-")[-1] if ad.stable_external_id else None,
            "linked": True,
        }
        for ad in user.identities
        if ad.provider == PROVIDER_AD and ad.link_status == LINKED and ad.account_status == ACCOUNT_ENABLED
    ]


def _employee_google_sub(user: QkaUser) -> str | None:
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


def list_employees(session: Session) -> list[dict]:
    """List every Allowed Email with Google identity status (linked / pending / disabled).

    Pending allowlisted emails are visible for Super Admin planning but cannot receive
    document grants until a verified Google sub is linked via OAuth (never by email merge).
    """
    rows = session.execute(select(AllowedEmail).order_by(AllowedEmail.email_normalized)).scalars().all()
    by_email = _google_users_by_verified_email(session)
    out: list[dict] = []
    for row in rows:
        email = row.email_normalized
        linked_users = by_email.get(email, [])
        user = linked_users[0] if linked_users else None
        if not row.enabled:
            identity_status = "disabled"
        elif user is not None:
            identity_status = "linked"
        else:
            identity_status = "pending"
        google_sub = _employee_google_sub(user) if user is not None else None
        out.append(
            {
                "allowlistId": str(row.id),
                "qkaUserId": str(user.id) if user is not None else None,
                "displayName": user.display_name if user is not None else email.split("@", 1)[0],
                "primaryEmail": email,
                "googleSub": google_sub,
                "googleLinked": identity_status == "linked",
                "identityStatus": identity_status,
                "allowlisted": True,
                "allowlistEnabled": bool(row.enabled),
                "canGrant": identity_status == "linked" and user is not None,
                "adIdentities": _employee_ad_idents(user) if user is not None else [],
                "superAdmin": bool(user.is_super_admin) if user is not None else False,
            }
        )
    return out


def list_sources(session: Session, settings: Settings) -> list[dict]:
    shares = session.execute(
        select(SynologyApprovedShare).where(SynologyApprovedShare.is_active.is_(True)).order_by(SynologyApprovedShare.share_name)
    ).scalars().all()
    scopes = session.execute(select(SourceScope).where(SourceScope.status == ScopeStatus.ACTIVE)).scalars().all()
    by_share: dict[uuid.UUID, list[SourceScope]] = {}
    for scope in scopes:
        by_share.setdefault(scope.approved_share_id, []).append(scope)

    result: list[dict] = []
    for share in shares:
        inventory_count = session.scalar(
            select(func.count()).select_from(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share.id,
                SynologyFileInventory.is_present.is_(True),
            )
        )
        acl_captured = session.scalar(
            select(func.count()).select_from(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share.id,
                SynologyFileInventory.is_present.is_(True),
                SynologyFileInventory.acl_capture_status == 1,
            )
        )
        result.append(
            {
                "approvedShareId": str(share.id),
                "shareName": share.share_name,
                "inventoryCount": int(inventory_count or 0),
                "aclCapturedCount": int(acl_captured or 0),
                "pilotFolderPrefix": settings.pilot_folder_prefix,
                "isConfiguredPilotShare": settings.approved_share_id is not None and share.id == settings.approved_share_id,
                "scopes": [_scope_dto(s) for s in by_share.get(share.id, [])],
                "eligibleForGrants": any(
                    s.trust_class == TrustClass.CORPORATE_DESIGNATED
                    and s.service_read_verified
                    and bool(s.approval_reference.strip())
                    for s in by_share.get(share.id, [])
                ),
            }
        )
    return result


def eligible_scopes_for_share(session: Session, share_id: uuid.UUID) -> dict:
    scopes = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
            SourceScope.trust_class == TrustClass.CORPORATE_DESIGNATED,
        )
    ).scalars().all()
    eligible = [
        _scope_dto(s)
        for s in scopes
        if s.service_read_verified and s.approval_reference.strip() and s.approved_at is not None
    ]
    if eligible:
        return {"eligible": True, "scopes": eligible, "reason": None}
    if scopes:
        return {
            "eligible": False,
            "scopes": [],
            "reason": "Corporate scopes exist but source-owner approval or service-read verification is incomplete.",
        }
    return {
        "eligible": False,
        "scopes": [],
        "reason": "No corporate designated scope is registered for this source. Configure approval under Synology source settings first.",
    }


def folder_tree(session: Session, settings: Settings, share_id: uuid.UUID, root_prefix: str | None = None) -> list[dict]:
    # None → default pilot prefix; "" → entire share; otherwise the given folder prefix.
    if root_prefix is None:
        prefix = normalize_prefix(settings.pilot_folder_prefix or "")
    else:
        prefix = normalize_prefix(root_prefix)
    rows = session.execute(
        select(SynologyFileInventory.relative_path_normalized, SynologyFileInventory.acl_capture_status)
        .where(
            SynologyFileInventory.approved_share_id == share_id,
            SynologyFileInventory.is_present.is_(True),
        )
        .order_by(SynologyFileInventory.relative_path_normalized)
        .limit(8000)
    ).all()
    nodes: dict[str, dict] = {}
    for path, acl_status in rows:
        norm = normalize_prefix(path or "")
        if prefix and not under_prefix(norm, prefix):
            continue
        parts = [p for p in norm.split("\\") if p]
        if not parts:
            continue
        # folder nodes = all ancestors; file leaf marked separately
        accum: list[str] = []
        for i, part in enumerate(parts):
            accum.append(part)
            key = "\\".join(accum)
            is_file = i == len(parts) - 1
            if key not in nodes:
                nodes[key] = {
                    "id": key,
                    "label": part,
                    "path": key,
                    "lazy": False,
                    "children": [],
                    "isFile": is_file,
                    "icon": _file_icon(part, is_file),
                    "aclCaptureStatus": int(acl_status or 0) if is_file else None,
                }
            elif is_file:
                nodes[key]["isFile"] = True
                nodes[key]["icon"] = _file_icon(part, True)
                nodes[key]["aclCaptureStatus"] = int(acl_status or 0)
            parent_key = "\\".join(accum[:-1])
            if parent_key and parent_key in nodes:
                child_ids = {c["id"] if isinstance(c, dict) else c for c in nodes[parent_key]["children"]}
                if key not in child_ids:
                    nodes[parent_key]["children"].append(nodes[key])
                    nodes[parent_key]["isFile"] = False
                    nodes[parent_key]["icon"] = "folder"

    roots = []
    if prefix and prefix in nodes:
        roots.append(nodes[prefix])
    else:
        for key, node in nodes.items():
            if "\\" not in key:
                roots.append(node)

    def prune(node: dict) -> dict:
        kids = [prune(c) for c in node["children"] if isinstance(c, dict)]
        is_file = bool(node.get("isFile"))
        return {
            "id": node["id"],
            "label": node["label"],
            "path": node["path"],
            "isFile": is_file,
            "icon": node.get("icon") or _file_icon(node["label"], is_file),
            "aclCaptureStatus": node.get("aclCaptureStatus"),
            "children": kids,
        }

    return [prune(r) for r in roots]


def list_scopes(session: Session) -> list[dict]:
    rows = session.execute(select(SourceScope).order_by(SourceScope.created_at.desc())).scalars().all()
    return [_scope_dto(row) for row in rows]


def create_corporate_scope(
    session: Session,
    actor: QkaUser,
    *,
    approved_share_id: uuid.UUID,
    folder_prefix: str,
    display_name: str,
    source_owner_name: str,
    source_owner_email: str,
    approval_reference: str,
    approval_notes: str,
    service_read_verified: bool,
) -> SourceScope:
    prefix = normalize_prefix(folder_prefix)
    if not prefix:
        raise ValueError("Folder prefix is required.")
    if not approval_reference.strip():
        raise ValueError("Approval reference is required for corporate designation.")
    if not source_owner_name.strip():
        raise ValueError("Source owner name is required.")
    owner_email, email_err = try_normalize(source_owner_email)
    if owner_email is None:
        raise ValueError(email_err or "Source owner email is invalid.")
    if not service_read_verified:
        raise ValueError("Service read authority must be verified before corporate designation.")

    share = session.get(SynologyApprovedShare, approved_share_id)
    if share is None or not share.is_active:
        raise ValueError("Approved share was not found.")

    existing = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == approved_share_id,
            SourceScope.folder_prefix_normalized == prefix,
            SourceScope.status == ScopeStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError("An active scope already exists for that folder.")

    now = _now()
    row = SourceScope(
        id=uuid.uuid4(),
        approved_share_id=approved_share_id,
        folder_prefix_normalized=prefix,
        trust_class=TrustClass.CORPORATE_DESIGNATED,
        status=ScopeStatus.ACTIVE,
        display_name=(display_name or prefix).strip()[:512],
        source_owner_name=source_owner_name.strip()[:256],
        source_owner_email=owner_email,
        approval_reference=approval_reference.strip()[:512],
        approval_notes=(approval_notes or "").strip()[:2000],
        approved_at=now,
        approved_by_user_id=actor.id,
        service_read_verified=True,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        action="register_corporate_scope",
        actor_id=actor.id,
        scope_id=row.id,
        detail=f"prefix={prefix}; ref={row.approval_reference}; owner={owner_email}",
    )
    return row


def revoke_scope(session: Session, actor: QkaUser, scope_id: uuid.UUID) -> SourceScope:
    row = session.get(SourceScope, scope_id)
    if row is None:
        raise LookupError("Scope not found.")
    row.status = ScopeStatus.REVOKED
    row.updated_at = _now()
    for grant in session.execute(select(SourceGrant).where(SourceGrant.scope_id == scope_id)).scalars().all():
        if grant.status == GrantStatus.ACTIVE:
            grant.status = GrantStatus.REVOKED
            grant.updated_at = row.updated_at
            grant.updated_by_user_id = actor.id
    session.flush()
    _audit(session, action="revoke_scope", actor_id=actor.id, scope_id=row.id, detail="scope revoked")
    return row


def list_grants(session: Session, scope_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> list[dict]:
    stmt = select(SourceGrant).options(selectinload(SourceGrant.scope)).order_by(SourceGrant.updated_at.desc())
    if scope_id is not None:
        stmt = stmt.where(SourceGrant.scope_id == scope_id)
    if user_id is not None:
        stmt = stmt.where(SourceGrant.qka_user_id == user_id)
    rows = session.execute(stmt.limit(500)).scalars().all()
    out = []
    for row in rows:
        dto = _grant_dto(row)
        dto["scope"] = _scope_dto(row.scope) if row.scope else None
        out.append(dto)
    return out


def _resolve_share_name(session: Session, scope: SourceScope | None) -> str:
    if scope is None:
        return ""
    share = session.get(SynologyApprovedShare, scope.approved_share_id)
    return share.share_name if share else ""


def _primary_email_for_user(session: Session, user_id: uuid.UUID) -> str:
    emails = sorted(load_verified_emails(session, user_id))
    return emails[0] if emails else ""


def upsert_grant(
    session: Session,
    actor: QkaUser,
    *,
    scope_id: uuid.UUID,
    qka_user_id: uuid.UUID,
    capability_read: bool,
    capability_search: bool,
    capability_preview: bool,
    reason: str,
    path_mode: str = "all",
    include_subfolders: bool = True,
    selected_paths: list[str] | None = None,
    file_types: list[str] | None = None,
) -> SourceGrant:
    scope = session.get(SourceScope, scope_id)
    if scope is None or scope.status != ScopeStatus.ACTIVE:
        raise ValueError("Active corporate scope is required.")
    if scope.trust_class != TrustClass.CORPORATE_DESIGNATED:
        raise ValueError("Grants are only issued on corporate designated scopes.")
    if not scope.service_read_verified or not scope.approval_reference.strip():
        raise ValueError("Scope is missing recorded source-owner approval.")

    user = session.get(QkaUser, qka_user_id)
    if user is None:
        raise ValueError("Target user was not found.")
    google = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.qka_user_id == qka_user_id,
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
        )
    ).scalar_one_or_none()
    if google is None:
        raise ValueError("Target user must have a verified linked Google identity.")
    emails = load_verified_emails(session, qka_user_id)
    allowed = session.execute(
        select(AllowedEmail).where(AllowedEmail.email_normalized.in_(emails), AllowedEmail.enabled.is_(True))
    ).scalars().first()
    if allowed is None:
        raise ValueError("Target user must be on the enabled Google allowlist.")
    if not (capability_read or capability_search or capability_preview):
        raise ValueError("At least one capability is required.")

    mode = PathMode.ALL_IN_SCOPE if (path_mode or "all").lower() != "selected" else PathMode.SELECTED
    paths = [normalize_prefix(p) for p in (selected_paths or []) if normalize_prefix(p)]
    scope_prefix = normalize_prefix(scope.folder_prefix_normalized)
    for path in paths:
        if not under_prefix(path, scope_prefix):
            raise ValueError(f"Selected path is outside the approved corporate scope: {path}")
    if mode == PathMode.SELECTED and not paths:
        raise ValueError("Select at least one folder or file for specific-folder access.")
    allowed_types = set(FILE_TYPE_EXTENSIONS.keys()) | {"all"}
    types = [t.lower().strip() for t in (file_types or []) if t and t.lower().strip()]
    if any(t not in allowed_types for t in types):
        raise ValueError("One or more file types are invalid.")
    if "all" in types:
        types = []

    now = _now()
    employee_email = _primary_email_for_user(session, qka_user_id)
    share_name = _resolve_share_name(session, scope)
    existing = session.execute(
        select(SourceGrant).where(
            SourceGrant.scope_id == scope_id,
            SourceGrant.qka_user_id == qka_user_id,
            SourceGrant.status == GrantStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if existing is not None:
        before = _grant_snapshot(existing)
        existing.capability_read = capability_read
        existing.capability_search = capability_search
        existing.capability_preview = capability_preview
        existing.path_mode = int(mode)
        existing.include_subfolders = bool(include_subfolders)
        existing.selected_paths_json = json.dumps(paths)
        existing.file_types_json = json.dumps(types)
        existing.reason = (reason or "").strip()[:1024]
        existing.updated_at = now
        existing.updated_by_user_id = actor.id
        session.flush()
        after = _grant_snapshot(existing)
        _audit(
            session,
            action="Updated",
            actor_id=actor.id,
            target_user_id=qka_user_id,
            scope_id=scope_id,
            grant_id=existing.id,
            detail=existing.reason or "grant updated",
            employee_email=employee_email,
            share_name=share_name,
            before=before,
            after=after,
        )
        return existing

    row = SourceGrant(
        id=uuid.uuid4(),
        scope_id=scope_id,
        qka_user_id=qka_user_id,
        capability_read=capability_read,
        capability_search=capability_search,
        capability_preview=capability_preview,
        path_mode=int(mode),
        include_subfolders=bool(include_subfolders),
        selected_paths_json=json.dumps(paths),
        file_types_json=json.dumps(types),
        status=GrantStatus.ACTIVE,
        reason=(reason or "").strip()[:1024],
        created_at=now,
        updated_at=now,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    session.add(row)
    session.flush()
    after = _grant_snapshot(row)
    _audit(
        session,
        action="Created",
        actor_id=actor.id,
        target_user_id=qka_user_id,
        scope_id=scope_id,
        grant_id=row.id,
        detail=row.reason or "grant created",
        employee_email=employee_email,
        share_name=share_name,
        before={},
        after=after,
    )
    return row


def set_grant_status(session: Session, actor: QkaUser, grant_id: uuid.UUID, status: int, reason: str = "") -> SourceGrant:
    row = session.get(SourceGrant, grant_id)
    if row is None:
        raise LookupError("Grant not found.")
    if status not in (GrantStatus.ACTIVE, GrantStatus.DISABLED, GrantStatus.REVOKED):
        raise ValueError("Invalid grant status.")
    before = _grant_snapshot(row)
    row.status = status
    if reason.strip():
        row.reason = reason.strip()[:1024]
    row.updated_at = _now()
    row.updated_by_user_id = actor.id
    session.flush()
    action = {GrantStatus.ACTIVE: "Updated", GrantStatus.DISABLED: "Disabled", GrantStatus.REVOKED: "Revoked"}[status]
    scope = session.get(SourceScope, row.scope_id)
    _audit(
        session,
        action=action,
        actor_id=actor.id,
        target_user_id=row.qka_user_id,
        scope_id=row.scope_id,
        grant_id=row.id,
        detail=row.reason,
        employee_email=_primary_email_for_user(session, row.qka_user_id),
        share_name=_resolve_share_name(session, scope),
        before=before,
        after=_grant_snapshot(row),
    )
    return row


def effective_for_user(
    session: Session,
    settings: Settings,
    user_id: uuid.UUID,
    *,
    document_id: uuid.UUID | None = None,
) -> dict:
    user = session.execute(
        select(QkaUser).options(selectinload(QkaUser.identities)).where(QkaUser.id == user_id)
    ).scalar_one_or_none()
    if user is None:
        raise LookupError("User not found.")

    grants = list_grants(session, user_id=user_id)
    sample = None
    if document_id is not None:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.inventory_file_id == document_id)
        ).scalar_one_or_none()
        inventory = session.get(SynologyFileInventory, document_id) if content else None
        if content and inventory:
            eff = evaluate_document_access(session, user, inventory, content, capability="search")
            sample = {
                "documentId": document_id.hex,
                "fileName": content.file_name,
                "relativePath": content.relative_path_normalized,
                "qkaGrant": eff.qka_grant,
                "sourcePermission": eff.source_permission,
                "effectiveAccess": eff.effective,
                "denialReason": eff.denial_reason,
                "allowed": eff.allowed,
            }
    elif settings.approved_share_id:
        # Probe pilot document if present (metadata only).
        content = session.execute(
            select(SynologyDocumentContent)
            .where(
                SynologyDocumentContent.approved_share_id == settings.approved_share_id,
                SynologyDocumentContent.pilot_scoped.is_(True),
                SynologyDocumentContent.status == 1,
            )
            .order_by(SynologyDocumentContent.file_name)
            .limit(1)
        ).scalar_one_or_none()
        if content is not None:
            inventory = session.get(SynologyFileInventory, content.inventory_file_id)
            if inventory is not None:
                eff = evaluate_document_access(session, user, inventory, content, capability="search")
                sample = {
                    "documentId": content.inventory_file_id.hex,
                    "fileName": content.file_name,
                    "relativePath": content.relative_path_normalized,
                    "qkaGrant": eff.qka_grant,
                    "sourcePermission": eff.source_permission,
                    "effectiveAccess": eff.effective,
                    "denialReason": eff.denial_reason,
                    "allowed": eff.allowed,
                }

    return {
        "qkaUserId": str(user.id),
        "displayName": user.display_name,
        "grants": grants,
        "sampleDocument": sample,
    }


def list_audit(
    session: Session,
    *,
    search: str | None = None,
    employee_email: str | None = None,
    share_name: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(SourceGrantAudit)
    if search and search.strip():
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                SourceGrantAudit.detail.ilike(like),
                SourceGrantAudit.employee_email.ilike(like),
                SourceGrantAudit.share_name.ilike(like),
                SourceGrantAudit.action.ilike(like),
            )
        )
    if employee_email and employee_email.strip():
        stmt = stmt.where(SourceGrantAudit.employee_email == employee_email.strip().lower())
    if share_name and share_name.strip():
        stmt = stmt.where(SourceGrantAudit.share_name.ilike(f"%{share_name.strip()}%"))
    if action and action.strip():
        stmt = stmt.where(SourceGrantAudit.action == action.strip())

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = session.execute(
        stmt.order_by(SourceGrantAudit.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    actor_ids = {row.actor_user_id for row in rows if row.actor_user_id}
    actors = {}
    if actor_ids:
        for user in session.execute(select(QkaUser).where(QkaUser.id.in_(actor_ids))).scalars().all():
            actors[user.id] = user.display_name

    items = []
    for row in rows:
        before = {}
        after = {}
        try:
            before = json.loads(row.before_detail or "{}")
        except json.JSONDecodeError:
            before = {}
        try:
            after = json.loads(row.after_detail or "{}")
        except json.JSONDecodeError:
            after = {}
        scope_label = ""
        if row.scope_id:
            scope = session.get(SourceScope, row.scope_id)
            if scope:
                scope_label = scope.folder_prefix_normalized
        file_types = after.get("fileTypes") if isinstance(after, dict) else []
        if not file_types:
            file_types = ["all"]
        path_mode = after.get("pathMode") if isinstance(after, dict) else ""
        items.append(
            {
                "id": str(row.id),
                "action": row.action,
                "actorUserId": str(row.actor_user_id) if row.actor_user_id else None,
                "changedBy": actors.get(row.actor_user_id, "—") if row.actor_user_id else "—",
                "targetUserId": str(row.target_user_id) if row.target_user_id else None,
                "employeeEmail": row.employee_email or "",
                "shareName": row.share_name or "",
                "scope": scope_label or path_mode or "",
                "fileTypes": file_types,
                "scopeId": str(row.scope_id) if row.scope_id else None,
                "grantId": str(row.grant_id) if row.grant_id else None,
                "detail": row.detail,
                "before": before,
                "after": after,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"items": items, "total": int(total), "page": page, "pageSize": page_size}
