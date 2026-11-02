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
    ChatAccess,
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


def _grant_view_flag(row: SourceGrant) -> bool:
    return bool(
        getattr(row, "capability_view", False)
        or row.capability_read
        or row.capability_search
        or row.capability_preview
    )


def _grant_snapshot(row: SourceGrant) -> dict:
    view = _grant_view_flag(row)
    download = bool(getattr(row, "capability_download", False))
    return {
        "pathMode": "all" if int(getattr(row, "path_mode", 0) or 0) == PathMode.ALL_IN_SCOPE else "selected",
        "includeSubfolders": bool(getattr(row, "include_subfolders", True)),
        "selectedPaths": parse_json_list(getattr(row, "selected_paths_json", "[]")),
        "fileTypes": parse_json_list(getattr(row, "file_types_json", "[]")),
        "capabilityView": view,
        "capabilityDownload": download,
        # Legacy mirrors for older audit consumers.
        "capabilityRead": view,
        "capabilitySearch": view,
        "capabilityPreview": view,
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
    pending = row.qka_user_id is None and row.allowed_email_id is not None
    return {
        "id": str(row.id),
        "scopeId": str(row.scope_id),
        "qkaUserId": str(row.qka_user_id) if row.qka_user_id else None,
        "allowedEmailId": str(row.allowed_email_id) if row.allowed_email_id else None,
        "pendingActivation": pending,
        "capabilityView": snap["capabilityView"],
        "capabilityDownload": snap["capabilityDownload"],
        "capabilityRead": snap["capabilityView"],
        "capabilitySearch": snap["capabilityView"],
        "capabilityPreview": snap["capabilityView"],
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
    """Unified Users directory for Source Access selector (all allowlisted emails)."""
    from qka_api.services import user_directory as directory

    page = directory.list_users(session, page=1, page_size=5000)
    return page["items"]


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
                SynologyFileInventory.is_directory.is_(False),
            )
        )
        acl_captured = session.scalar(
            select(func.count()).select_from(SynologyFileInventory).where(
                SynologyFileInventory.approved_share_id == share.id,
                SynologyFileInventory.is_present.is_(True),
                SynologyFileInventory.is_directory.is_(False),
                SynologyFileInventory.acl_capture_status == 1,
            )
        )
        share_scopes = by_share.get(share.id, [])
        qka_managed = [
            s
            for s in share_scopes
            if s.trust_class == TrustClass.CORPORATE_DESIGNATED
            and s.service_read_verified
            and bool(s.approval_reference.strip())
            and s.approved_at is not None
        ]
        result.append(
            {
                "approvedShareId": str(share.id),
                "shareName": share.share_name,
                "inventoryCount": int(inventory_count or 0),
                "aclCapturedCount": int(acl_captured or 0),
                "pilotFolderPrefix": settings.pilot_folder_prefix,
                "isConfiguredPilotShare": settings.approved_share_id is not None and share.id == settings.approved_share_id,
                "scopes": [_scope_dto(s) for s in share_scopes],
                "accessModel": "qka_managed" if qka_managed else "source_acl",
                "eligibleForGrants": bool(qka_managed),
            }
        )
    return result


def eligible_scopes_for_share(session: Session, share_id: uuid.UUID) -> dict:
    """Return scopes for Source Access rule editing.

    No corporate-approval warning is returned here — authorization is configured once
    under Synology Access Policy. Rules may be prepared even when QKA Managed is inactive;
    Effective Access stays Denied until the source is authorized.
    """
    scopes = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
        )
    ).scalars().all()
    managed = [
        s
        for s in scopes
        if s.trust_class == TrustClass.CORPORATE_DESIGNATED
        and s.service_read_verified
        and s.approval_reference.strip()
        and s.approved_at is not None
    ]
    return {
        "eligible": bool(managed),
        "accessModel": "qka_managed" if managed else "source_acl",
        "scopes": [_scope_dto(s) for s in (managed or scopes)],
        "reason": None,
        "authorizedForDelegation": bool(managed),
    }


def ensure_share_scope(
    session: Session,
    settings: Settings,
    actor: QkaUser,
    *,
    approved_share_id: uuid.UUID,
    folder_prefix: str | None = None,
) -> SourceScope:
    """Resolve or create an active scope for employee grants (planning or QKA Managed)."""
    share = session.get(SynologyApprovedShare, approved_share_id)
    if share is None or not share.is_active:
        raise ValueError("Approved share was not found.")
    # Empty prefix = entire approved share. Do not fall back to the extraction pilot folder.
    if folder_prefix is None:
        prefix = ""
    else:
        prefix = normalize_prefix(folder_prefix)

    managed = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == approved_share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
            SourceScope.trust_class == TrustClass.CORPORATE_DESIGNATED,
            SourceScope.folder_prefix_normalized == prefix,
            SourceScope.service_read_verified.is_(True),
        )
    ).scalar_one_or_none()
    if managed is not None and managed.approval_reference.strip() and managed.approved_at is not None:
        return managed

    existing = session.execute(
        select(SourceScope).where(
            SourceScope.approved_share_id == approved_share_id,
            SourceScope.status == ScopeStatus.ACTIVE,
            SourceScope.folder_prefix_normalized == prefix,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    now = _now()
    row = SourceScope(
        id=uuid.uuid4(),
        approved_share_id=approved_share_id,
        folder_prefix_normalized=prefix,
        trust_class=TrustClass.PERSONAL,
        status=ScopeStatus.ACTIVE,
        display_name=prefix or share.share_name,
        source_owner_name="",
        source_owner_email="",
        approval_reference="",
        approval_notes="",
        approved_at=None,
        approved_by_user_id=None,
        service_read_verified=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        action="ensure_planning_scope",
        actor_id=actor.id,
        scope_id=row.id,
        detail=f"prefix={prefix}; planning scope for employee rules",
        share_name=share.share_name,
    )
    return row


def folder_tree(session: Session, settings: Settings, share_id: uuid.UUID, root_prefix: str | None = None) -> list[dict]:
    """Lazy one-level roots from PostgreSQL. Empty prefix = entire share (not the extraction pilot folder)."""
    from qka_api.services.inventory_hierarchy import folder_tree_roots

    _ = settings
    prefix = "" if root_prefix is None else normalize_prefix(root_prefix)
    return folder_tree_roots(session, share_id, prefix)


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
    now = _now()
    if existing is not None:
        # Upgrade a planning / incomplete scope into authorized QKA Managed (same row keeps grants).
        if (
            existing.trust_class == TrustClass.CORPORATE_DESIGNATED
            and existing.service_read_verified
            and existing.approval_reference.strip()
            and existing.approved_at is not None
        ):
            raise ValueError("An active QKA Managed scope already exists for that folder.")
        existing.trust_class = TrustClass.CORPORATE_DESIGNATED
        existing.display_name = (display_name or prefix).strip()[:512]
        existing.source_owner_name = source_owner_name.strip()[:256]
        existing.source_owner_email = owner_email
        existing.approval_reference = approval_reference.strip()[:512]
        existing.approval_notes = (approval_notes or "").strip()[:2000]
        existing.approved_at = now
        existing.approved_by_user_id = actor.id
        existing.service_read_verified = True
        existing.updated_at = now
        session.flush()
        _audit(
            session,
            action="activate_qka_managed_scope",
            actor_id=actor.id,
            scope_id=existing.id,
            detail=f"prefix={prefix}; ref={existing.approval_reference}; owner={owner_email}",
        )
        return existing

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
    settings: Settings,
    *,
    scope_id: uuid.UUID | None = None,
    approved_share_id: uuid.UUID | None = None,
    qka_user_id: uuid.UUID | None = None,
    allowlist_id: uuid.UUID | None = None,
    capability_view: bool = True,
    capability_download: bool = False,
    capability_read: bool | None = None,
    capability_search: bool | None = None,
    capability_preview: bool | None = None,
    reason: str,
    path_mode: str = "all",
    include_subfolders: bool = True,
    selected_paths: list[str] | None = None,
    file_types: list[str] | None = None,
) -> SourceGrant:
    # Legacy R/S/P → View (any true). Download never inferred from legacy.
    if capability_read is not None or capability_search is not None or capability_preview is not None:
        capability_view = bool(
            capability_view
            or capability_read
            or capability_search
            or capability_preview
        )

    if scope_id is not None:
        scope = session.get(SourceScope, scope_id)
        if scope is None or scope.status != ScopeStatus.ACTIVE:
            raise ValueError("Active source scope is required.")
    elif approved_share_id is not None:
        scope = ensure_share_scope(session, settings, actor, approved_share_id=approved_share_id)
    else:
        raise ValueError("scopeId or approvedShareId is required.")

    target_user_id: uuid.UUID | None = None
    allowed: AllowedEmail | None = None
    employee_email = ""

    if qka_user_id is not None:
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
        target_user_id = qka_user_id
        employee_email = _primary_email_for_user(session, qka_user_id)
    elif allowlist_id is not None:
        allowed = session.get(AllowedEmail, allowlist_id)
        if allowed is None or not allowed.enabled:
            raise ValueError("Enabled allowlist entry is required for pending grants.")
        by_email = _google_users_by_verified_email(session)
        linked = by_email.get(allowed.email_normalized) or []
        if linked:
            target_user_id = linked[0].id
        employee_email = allowed.email_normalized
    else:
        raise ValueError("qkaUserId or allowlistId is required.")

    if not (capability_view or capability_download):
        raise ValueError("At least one permission (View Documents or Download Originals) is required.")
    # Download is never auto-enabled from View alone.
    capability_download = bool(capability_download)

    mode = PathMode.ALL_IN_SCOPE if (path_mode or "all").lower() != "selected" else PathMode.SELECTED
    paths = [normalize_prefix(p) for p in (selected_paths or []) if normalize_prefix(p)]
    scope_prefix = normalize_prefix(scope.folder_prefix_normalized)
    for path in paths:
        if not under_prefix(path, scope_prefix):
            raise ValueError(f"Selected path is outside the scope: {path}")
    if mode == PathMode.SELECTED and not paths:
        raise ValueError("Select at least one folder or file for specific-folder access.")
    allowed_types = set(FILE_TYPE_EXTENSIONS.keys()) | {"all"}
    types = [t.lower().strip() for t in (file_types or []) if t and t.lower().strip()]
    if any(t not in allowed_types for t in types):
        raise ValueError("One or more file types are invalid.")
    if "all" in types:
        types = []

    now = _now()
    share_name = _resolve_share_name(session, scope)
    assert allowed is not None
    scope_id = scope.id

    existing: SourceGrant | None = None
    if target_user_id is not None:
        existing = session.execute(
            select(SourceGrant).where(
                SourceGrant.scope_id == scope_id,
                SourceGrant.qka_user_id == target_user_id,
                SourceGrant.status == GrantStatus.ACTIVE,
            )
        ).scalar_one_or_none()
    if existing is None:
        existing = session.execute(
            select(SourceGrant).where(
                SourceGrant.scope_id == scope_id,
                SourceGrant.allowed_email_id == allowed.id,
                SourceGrant.status == GrantStatus.ACTIVE,
            )
        ).scalar_one_or_none()

    def _apply(row: SourceGrant) -> None:
        row.capability_view = bool(capability_view)
        row.capability_download = bool(capability_download)
        # Dual-write legacy mirrors from View only (never invent Download).
        row.capability_read = bool(capability_view)
        row.capability_search = bool(capability_view)
        row.capability_preview = bool(capability_view)
        row.path_mode = int(mode)
        row.include_subfolders = bool(include_subfolders)
        row.selected_paths_json = json.dumps(paths)
        row.file_types_json = json.dumps(types)
        row.reason = (reason or "").strip()[:1024]
        row.allowed_email_id = allowed.id
        if target_user_id is not None:
            row.qka_user_id = target_user_id
        row.updated_at = now
        row.updated_by_user_id = actor.id

    if existing is not None:
        before = _grant_snapshot(existing)
        _apply(existing)
        session.flush()
        after = _grant_snapshot(existing)
        detail = existing.reason or "grant updated"
        if existing.qka_user_id is None:
            detail = f"{detail}; pending_activation".strip("; ")
        authorized = (
            scope.trust_class == TrustClass.CORPORATE_DESIGNATED
            and scope.service_read_verified
            and bool(scope.approval_reference.strip())
        )
        if not authorized:
            detail = f"{detail}; effective_denied_until_source_authorized"
        _audit(
            session,
            action="Updated",
            actor_id=actor.id,
            target_user_id=existing.qka_user_id,
            scope_id=scope_id,
            grant_id=existing.id,
            detail=detail,
            employee_email=employee_email,
            share_name=share_name,
            before=before,
            after=after,
        )
        return existing

    row = SourceGrant(
        id=uuid.uuid4(),
        scope_id=scope_id,
        qka_user_id=target_user_id,
        allowed_email_id=allowed.id,
        capability_read=bool(capability_view),
        capability_search=bool(capability_view),
        capability_preview=bool(capability_view),
        capability_view=bool(capability_view),
        capability_download=bool(capability_download),
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
    detail = row.reason or "grant created"
    if row.qka_user_id is None:
        detail = f"{detail}; pending_activation".strip("; ")
    authorized = (
        scope.trust_class == TrustClass.CORPORATE_DESIGNATED
        and scope.service_read_verified
        and bool(scope.approval_reference.strip())
    )
    if not authorized:
        detail = f"{detail}; effective_denied_until_source_authorized"
    _audit(
        session,
        action="Created",
        actor_id=actor.id,
        target_user_id=target_user_id,
        scope_id=scope_id,
        grant_id=row.id,
        detail=detail,
        employee_email=employee_email,
        share_name=share_name,
        before={},
        after=after,
    )
    return row


def set_employee_chat_access(
    session: Session,
    actor: QkaUser,
    allowlist_id: uuid.UUID,
    *,
    local_chat_enabled: bool,
    mcp_enabled: bool,
) -> dict:
    from qka_shared.chat_access import set_chat_access

    row = set_chat_access(
        session,
        actor.id,
        allowlist_id,
        local_chat_enabled=local_chat_enabled,
        mcp_enabled=mcp_enabled,
    )
    allow = session.get(AllowedEmail, allowlist_id)
    return {
        "allowlistId": str(allowlist_id),
        "email": allow.email_normalized if allow else "",
        "localChatEnabled": bool(row.local_chat_enabled),
        "mcpEnabled": bool(row.mcp_enabled),
    }


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
    employee_email = ""
    if row.qka_user_id is not None:
        employee_email = _primary_email_for_user(session, row.qka_user_id)
    elif row.allowed_email_id is not None:
        allow = session.get(AllowedEmail, row.allowed_email_id)
        employee_email = allow.email_normalized if allow else ""
    _audit(
        session,
        action=action,
        actor_id=actor.id,
        target_user_id=row.qka_user_id,
        scope_id=row.scope_id,
        grant_id=row.id,
        detail=row.reason,
        employee_email=employee_email,
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
            eff = evaluate_document_access(session, user, inventory, content, capability="view")
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
                eff = evaluate_document_access(session, user, inventory, content, capability="view")
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
