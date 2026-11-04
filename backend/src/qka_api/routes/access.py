from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services import source_access_admin as source_admin
from qka_api.services import user_directory as directory
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings
from qka_shared.email_address import try_normalize
from qka_shared.models import AllowedEmail, QkaUser
from qka_shared.source_access import GrantStatus

router = APIRouter(prefix="/api/v1/access")


class AddEmailRequest(BaseModel):
    email: str
    displayName: str = ""


class SetEnabledRequest(BaseModel):
    enabled: bool


class CreateUserRequest(BaseModel):
    displayName: str
    email: str = ""
    identitySource: str = "google"  # google | active_directory
    objectGuid: str | None = None
    departmentOverride: str | None = None
    contactEmail: str | None = None
    linkExistingQkaUserId: uuid.UUID | None = None
    localChatEnabled: bool = False
    mcpEnabled: bool = False
    enabled: bool = True


class UpdateUserRequest(BaseModel):
    displayName: str | None = None
    enabled: bool | None = None
    localChatEnabled: bool | None = None
    mcpEnabled: bool | None = None


class RegisterCorporateScopeRequest(BaseModel):
    approvedShareId: uuid.UUID
    folderPrefix: str = ""
    displayName: str = ""
    sourceOwnerName: str
    sourceOwnerEmail: str
    approvalReference: str
    approvalNotes: str = ""
    serviceReadVerified: bool = False


class UpsertGrantRequest(BaseModel):
    scopeId: uuid.UUID | None = None
    approvedShareId: uuid.UUID | None = None
    qkaUserId: uuid.UUID | None = None
    allowlistId: uuid.UUID | None = None
    capabilityView: bool = True
    capabilityDownload: bool = False
    # Legacy aliases (any true → View). Download is never inferred.
    capabilityRead: bool | None = None
    capabilitySearch: bool | None = None
    capabilityPreview: bool | None = None
    reason: str = ""
    pathMode: str = "all"
    includeSubfolders: bool = True
    selectedPaths: list[str] = Field(default_factory=list)
    fileTypes: list[str] = Field(default_factory=list)


class SetGrantStatusRequest(BaseModel):
    status: str = Field(description="active | disabled | revoked")
    reason: str = ""


class SetChatAccessRequest(BaseModel):
    allowlistId: uuid.UUID
    localChatEnabled: bool = False
    mcpEnabled: bool = False


def _require_super_admin(
    request: Request,
    settings: Settings,
    session: Session,
    *,
    mutating: bool,
) -> QkaUser:
    if mutating:
        require_csrf(request)
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None or not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin access is required.")
    return user


def _to_dto(row: AllowedEmail) -> dict:
    return {
        "id": str(row.id),
        "email": row.email,
        "enabled": row.enabled,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/emails")
def list_emails(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    rows = session.execute(select(AllowedEmail).order_by(AllowedEmail.email_normalized)).scalars().all()
    return [_to_dto(row) for row in rows]


@router.post("/emails")
def add_email(
    body: AddEmailRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Legacy allowlist endpoint — prefers /users. Still creates a directory user."""
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    name = (body.displayName or "").strip() or (body.email.split("@", 1)[0] if "@" in body.email else body.email)
    try:
        created = directory.create_user(session, actor, display_name=name, email_raw=body.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.flush()
    return {
        "id": created["allowlistId"],
        "email": created["primaryEmail"],
        "enabled": created["allowlistEnabled"],
        "displayName": created["displayName"],
        "createdAt": created.get("createdAt"),
        "updatedAt": created.get("updatedAt"),
    }


@router.get("/users")
def list_directory_users(
    request: Request,
    search: str | None = None,
    accountStatus: str | None = None,
    identityStatus: str | None = None,
    chatgptMembership: str | None = None,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=25, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return directory.list_users(
        session,
        search=search,
        account_status=accountStatus,
        identity_status=identityStatus,
        chatgpt_membership=chatgptMembership,
        page=page,
        page_size=pageSize,
    )


@router.get("/users/membership-sync")
def membership_sync_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return directory.membership_sync_info()


@router.post("/users")
def create_directory_user(
    body: CreateUserRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    source = (body.identitySource or "google").strip().lower().replace("-", "_")
    if source in {"active_directory", "ad", "windows_integrated"}:
        from qka_api.services import active_directory as ad_svc

        if body.localChatEnabled or body.mcpEnabled:
            # Still allow explicit flags, but defaults remain off unless requested.
            pass
        try:
            enrolled = ad_svc.enroll_directory_user(
                session,
                actor.id,
                object_guid=body.objectGuid or "",
                department_override=body.departmentOverride,
                contact_email=body.contactEmail if body.contactEmail is not None else (body.email or None),
                link_existing_qka_user_id=body.linkExistingQkaUserId,
            )
        except ad_svc.AdValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ad_svc.AdConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        session.flush()
        # Map enrollment result into directory list shape for the UI.
        return {
            "allowlistId": None,
            "qkaUserId": enrolled["qkaUserId"],
            "displayName": enrolled["displayName"],
            "primaryEmail": enrolled.get("contactEmail") or "",
            "contactEmail": enrolled.get("contactEmail"),
            "domainUsername": enrolled.get("domainUsername"),
            "department": enrolled.get("department") or "",
            "identitySource": "ad",
            "identityStatus": "ad_enrolled",
            "accountStatus": "active",
            "localChatEnabled": False,
            "mcpEnabled": False,
            "allowlisted": bool(enrolled.get("contactEmail")),
            "adOnlyLoginImplemented": False,
            "created": enrolled.get("created"),
            "linkedExisting": enrolled.get("linkedExisting"),
            "objectGuid": enrolled.get("objectGuid"),
            "objectSid": enrolled.get("objectSid"),
        }
    try:
        if not (body.email or "").strip():
            raise ValueError("Email is required for Google / email enrollment.")
        created = directory.create_user(
            session,
            actor,
            display_name=body.displayName,
            email_raw=body.email,
            local_chat_enabled=body.localChatEnabled,
            mcp_enabled=body.mcpEnabled,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.flush()
    return created


@router.post("/users/membership/import")
async def import_membership_csv(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
    markMissingAsNonmember: bool = Form(default=False),
    notes: str = Form(default=""),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV must be UTF-8.") from exc
    try:
        result = directory.import_chatgpt_membership_csv(
            session,
            actor,
            csv_text=text,
            file_name=file.filename or "",
            mark_missing_as_nonmember=markMissingAsNonmember,
            notes=notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return result


@router.post("/users/{allowlist_id}")
def update_directory_user(
    allowlist_id: uuid.UUID,
    body: UpdateUserRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    try:
        updated = directory.update_user(
            session,
            actor,
            allowlist_id,
            display_name=body.displayName,
            enabled=body.enabled,
            local_chat_enabled=body.localChatEnabled,
            mcp_enabled=body.mcpEnabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return updated


@router.post("/users/qka/{qka_user_id}")
def update_ad_directory_user(
    qka_user_id: uuid.UUID,
    body: UpdateUserRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Update AD-enrolled QkaUser (no AllowedEmail). Does not write to Active Directory."""
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    try:
        updated = directory.update_ad_only_user(
            session,
            actor,
            qka_user_id,
            display_name=body.displayName,
            enabled=body.enabled,
            local_chat_enabled=body.localChatEnabled,
            mcp_enabled=body.mcpEnabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return updated


@router.post("/emails/{email_id}/enabled")
def set_enabled(
    email_id: uuid.UUID,
    body: SetEnabledRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    row = session.get(AllowedEmail, email_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist entry was not found.")
    row.enabled = body.enabled
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by_user_id = actor.id
    session.flush()
    return _to_dto(row)


@router.post("/emails/{email_id}/remove")
def remove_email(
    email_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    row = session.get(AllowedEmail, email_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist entry was not found.")
    session.delete(row)
    session.flush()
    return {"ok": True}


@router.get("/source/employees")
def source_employees(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.list_employees(session)


@router.get("/source/sources")
def source_sources(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.list_sources(session, settings)


@router.get("/source/folder-tree")
def source_folder_tree(
    request: Request,
    approvedShareId: uuid.UUID = Query(...),
    rootPrefix: str | None = Query(default=None),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    # Empty or omitted prefix lists the entire approved share (not the extraction pilot folder).
    return source_admin.folder_tree(session, settings, approvedShareId, rootPrefix)


@router.get("/source/folder-children")
def source_folder_children(
    request: Request,
    approvedShareId: uuid.UUID = Query(...),
    parentPath: str = Query(default=""),
    search: str | None = Query(default=None),
    fileType: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=200, ge=1, le=500),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services.inventory_hierarchy import list_folder_children

    _require_super_admin(request, settings, session, mutating=False)
    return list_folder_children(
        session,
        approvedShareId,
        parentPath,
        search=search,
        file_type=fileType,
        page=page,
        page_size=pageSize,
    )


@router.get("/source/folder-search")
def source_folder_search(
    request: Request,
    approvedShareId: uuid.UUID = Query(...),
    q: str = Query(default="", max_length=200),
    parentPath: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services.inventory_hierarchy import search_folder_nodes

    _require_super_admin(request, settings, session, mutating=False)
    return search_folder_nodes(session, approvedShareId, q, parent_path=parentPath, page=page, page_size=pageSize)


@router.get("/source/scopes")
def source_scopes(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.list_scopes(session)


@router.get("/source/eligible-scopes")
def source_eligible_scopes(
    request: Request,
    approvedShareId: uuid.UUID = Query(...),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.eligible_scopes_for_share(session, approvedShareId)


@router.post("/source/scopes/corporate")
def register_corporate_scope(
    body: RegisterCorporateScopeRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    try:
        row = source_admin.create_corporate_scope(
            session,
            actor,
            approved_share_id=body.approvedShareId,
            folder_prefix=body.folderPrefix,
            display_name=body.displayName,
            source_owner_name=body.sourceOwnerName,
            source_owner_email=body.sourceOwnerEmail,
            approval_reference=body.approvalReference,
            approval_notes=body.approvalNotes,
            service_read_verified=body.serviceReadVerified,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return source_admin._scope_dto(row)


@router.post("/source/scopes/{scope_id}/revoke")
def revoke_source_scope(
    scope_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    try:
        row = source_admin.revoke_scope(session, actor, scope_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    session.flush()
    return source_admin._scope_dto(row)


@router.get("/source/grants")
def source_grants(
    request: Request,
    scopeId: uuid.UUID | None = None,
    qkaUserId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.list_grants(session, scope_id=scopeId, user_id=qkaUserId)


@router.post("/source/grants")
def upsert_source_grant(
    body: UpsertGrantRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    if body.qkaUserId is None and body.allowlistId is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="qkaUserId or allowlistId is required.")
    if body.scopeId is None and body.approvedShareId is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scopeId or approvedShareId is required.")
    try:
        row = source_admin.upsert_grant(
            session,
            actor,
            settings,
            scope_id=body.scopeId,
            approved_share_id=body.approvedShareId,
            qka_user_id=body.qkaUserId,
            allowlist_id=body.allowlistId,
            capability_view=body.capabilityView,
            capability_download=body.capabilityDownload,
            capability_read=body.capabilityRead,
            capability_search=body.capabilitySearch,
            capability_preview=body.capabilityPreview,
            reason=body.reason,
            path_mode=body.pathMode,
            include_subfolders=body.includeSubfolders,
            selected_paths=body.selectedPaths,
            file_types=body.fileTypes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return source_admin._grant_dto(row)


@router.post("/source/chat-access")
def set_source_chat_access(
    body: SetChatAccessRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    try:
        payload = source_admin.set_employee_chat_access(
            session,
            actor,
            body.allowlistId,
            local_chat_enabled=body.localChatEnabled,
            mcp_enabled=body.mcpEnabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    session.flush()
    return payload


@router.post("/source/grants/{grant_id}/status")
def set_source_grant_status(
    grant_id: uuid.UUID,
    body: SetGrantStatusRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    status_map = {"active": GrantStatus.ACTIVE, "disabled": GrantStatus.DISABLED, "revoked": GrantStatus.REVOKED}
    mapped = status_map.get((body.status or "").strip().lower())
    if mapped is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be active, disabled, or revoked.")
    try:
        row = source_admin.set_grant_status(session, actor, grant_id, mapped, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.flush()
    return source_admin._grant_dto(row)


@router.get("/source/effective/{user_id}")
def source_effective(
    user_id: uuid.UUID,
    request: Request,
    documentId: str | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    doc_uuid: uuid.UUID | None = None
    if documentId:
        try:
            doc_uuid = uuid.UUID(documentId) if "-" in documentId else uuid.UUID(hex=documentId)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid documentId.") from exc
    try:
        return source_admin.effective_for_user(session, settings, user_id, document_id=doc_uuid)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/source/audit")
def source_audit(
    request: Request,
    search: str | None = None,
    employeeEmail: str | None = None,
    shareName: str | None = None,
    action: str | None = None,
    page: int = 1,
    pageSize: int = 20,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return source_admin.list_audit(
        session,
        search=search,
        employee_email=employeeEmail,
        share_name=shareName,
        action=action,
        page=page,
        page_size=pageSize,
    )
