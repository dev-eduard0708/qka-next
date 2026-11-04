"""Super Admin Active Directory integration API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.ldap_client import default_ldap_gateway
from qka_api.services import active_directory as svc
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings
from qka_shared.models import QkaUser

router = APIRouter(prefix="/api/v1/admin/active-directory")


class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectionName: str | None = None
    domainDnsName: str | None = None
    domainController: str | None = None
    # windows_integrated (recommended) | ldaps (optional). Prefer authMode; protocol kept for compatibility.
    authMode: str | None = None
    protocol: str | None = None
    port: int | None = None
    baseDn: str | None = None
    bindUsername: str | None = None
    bindPassword: str | None = None
    timeoutSeconds: int = 15
    verifyTls: bool | None = None


class OuSelectionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[dict] = Field(default_factory=list)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    qkaUserId: uuid.UUID | None = None


class EligibilityBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    directoryUserIds: list[uuid.UUID] = Field(default_factory=list)
    eligible: bool = False


class ArchiveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    directoryUserIds: list[uuid.UUID] = Field(default_factory=list)
    archived: bool = True


class EnrollBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    objectGuid: str
    departmentOverride: str | None = None
    contactEmail: str | None = None
    linkExistingQkaUserId: uuid.UUID | None = None


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


def _guard_writes(settings: Settings) -> None:
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")


def _gateway(request: Request):
    return getattr(request.app.state, "ldap_gateway", None) or default_ldap_gateway()


@router.get("/hub-status")
def hub_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.integrations_hub_status(session)


@router.get("/connections")
def list_connections(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_connections(session)


@router.get("/connections/{connection_id}")
def get_connection(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.connection_dto(svc.get_connection(session, connection_id))
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/connections/test")
def test_connection(
    body: ConnectionBody,
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.test_connection_payload(
            session,
            settings,
            _gateway(request),
            actor.id,
            body.model_dump(),
            connection_id=connectionId,
            persist_on_row=bool(connectionId),
        )
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections")
def create_connection(
    body: ConnectionBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        row = svc.save_connection(session, settings, _gateway(request), actor.id, body.model_dump())
        session.commit()
        return row
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/connections/{connection_id}")
def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        row = svc.save_connection(
            session, settings, _gateway(request), actor.id, body.model_dump(), connection_id=connection_id
        )
        session.commit()
        return row
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/disable")
def disable_connection(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        row = svc.disable_connection(session, actor.id, connection_id)
        session.commit()
        return row
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/enable")
def enable_connection(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        row = svc.enable_connection(session, actor.id, connection_id)
        session.commit()
        return row
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/remove")
def remove_connection(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        svc.delete_connection(session, actor.id, connection_id)
        session.commit()
        return {"ok": True}
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.AdConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/overview")
def get_overview(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.overview(session, connectionId)


@router.get("/connections/{connection_id}/ous")
def browse_ous(
    connection_id: uuid.UUID,
    request: Request,
    parentDn: str = "",
    search: str = "",
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_ou_tree(
            session, settings, _gateway(request), connection_id, parent_dn=parentDn, search=search
        )
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/ou-scope")
def get_ou_scope(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_selected_ous(session, connection_id)
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/connections/{connection_id}/ou-scope")
def put_ou_scope(
    connection_id: uuid.UUID,
    body: OuSelectionBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.set_ou_selection(session, actor.id, connection_id, body.items)
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/users")
def get_users(
    connection_id: uuid.UUID,
    request: Request,
    search: str = "",
    mappingStatus: int | None = None,
    accountStatus: str | None = None,
    eligibility: str | None = None,
    department: str | None = None,
    archived: bool = False,
    sortBy: str = "displayName",
    sortDesc: bool = False,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_users(
            session,
            connection_id,
            search=search,
            mapping_status=mappingStatus,
            account_status=accountStatus,
            eligibility=eligibility,
            department=department,
            archived=archived,
            sort_by=sortBy,
            sort_desc=sortDesc,
            page=page,
            page_size=pageSize,
        )
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/eligibility")
def set_eligibility(
    connection_id: uuid.UUID,
    body: EligibilityBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.set_eligibility(
            session,
            actor.id,
            connection_id,
            body.directoryUserIds,
            eligible=body.eligible,
        )
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/archive")
def set_archived(
    connection_id: uuid.UUID,
    body: ArchiveBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.set_archived(
            session,
            actor.id,
            connection_id,
            body.directoryUserIds,
            archived=body.archived,
        )
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/eligible-users")
def eligible_users_picker(
    request: Request,
    search: str = "",
    connectionId: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=50),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.search_eligible_directory_users(
        session, search=search, connection_id=connectionId, page=page, page_size=pageSize
    )


@router.post("/enroll")
def enroll_user(
    body: EnrollBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.enroll_directory_user(
            session,
            actor.id,
            object_guid=body.objectGuid,
            department_override=body.departmentOverride,
            contact_email=body.contactEmail,
            link_existing_qka_user_id=body.linkExistingQkaUserId,
        )
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.AdConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/groups")
def get_groups(
    connection_id: uuid.UUID,
    request: Request,
    search: str = "",
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_groups(session, connection_id, search=search, page=page, page_size=pageSize)
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/groups/{group_guid}/members")
def group_members(
    connection_id: uuid.UUID,
    group_guid: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.resolve_nested_members(session, connection_id, group_guid)


@router.post("/connections/{connection_id}/users/{user_id}/link")
def link_user(
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    body: LinkBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.approve_link(
            session, actor.id, connection_id, user_id, qka_user_id=body.qkaUserId
        )
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.AdConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/users/{user_id}/unlink")
def unlink_user(
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        result = svc.unlink_identity(session, actor.id, connection_id, user_id)
        session.commit()
        return result
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/sync")
def start_sync(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        job = svc.start_sync(session, settings, _gateway(request), actor.id, connection_id, trigger="manual")
        session.commit()
        return job
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.AdConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/resync")
def resync(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        job = svc.start_sync(session, settings, _gateway(request), actor.id, connection_id, trigger="resync")
        session.commit()
        return job
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.AdConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/sync/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        job = svc.cancel_sync(session, actor.id, job_id)
        session.commit()
        return job
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/sync/history")
def history(
    connection_id: uuid.UUID,
    request: Request,
    take: int = Query(50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.sync_history(session, connection_id, take=take)
    except svc.AdValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/audit")
def audit(
    request: Request,
    connectionId: uuid.UUID | None = None,
    take: int = Query(50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.audit_history(session, connectionId, take=take)
