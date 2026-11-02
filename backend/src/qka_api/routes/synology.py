from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services import synology as svc
from qka_api.smb import SmbGateway, default_gateway
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings
from qka_shared.models import QkaUser

router = APIRouter(prefix="/api/v1/admin/synology")


class SaveConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    connectionName: str | None = None
    sharedFolderPath: str | None = None
    configuredSharedFolderPath: str | None = None
    hostName: str | None = None
    authMode: int = 1
    serviceAccountUserName: str | None = None
    username: str | None = None
    serviceAccountSecret: str | None = Field(default=None)
    password: str | None = Field(default=None)


class StartSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectionId: uuid.UUID | None = None


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


def get_gateway(request: Request) -> SmbGateway:
    return getattr(request.app.state, "smb_gateway", None) or default_gateway()


def _guard_writes(settings: Settings) -> None:
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")


@router.get("/connections")
def list_connections(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_connections(session)


@router.get("/connection")
def get_connection(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.get_connection(session, connectionId)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/connections/{connection_id}")
def get_connection_by_id(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.get_connection(session, connection_id)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/connections")
def create_connection(
    body: SaveConnectionRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        return svc.create_connection(session, settings, actor.id, body.model_dump())
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/connections/{connection_id}")
@router.post("/connections/{connection_id}")
def update_connection(
    connection_id: uuid.UUID,
    body: SaveConnectionRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        return svc.save_connection(session, settings, actor.id, body.model_dump(), connection_id)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/connection")
@router.post("/connection")
def save_connection(
    body: SaveConnectionRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        return svc.save_connection(session, settings, actor.id, body.model_dump())
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/test")
def test_connection_by_id(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    gateway: SmbGateway = Depends(get_gateway),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        return svc.test_connection(session, settings, gateway, actor.id, connection_id)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connection/test")
def test_connection(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
    gateway: SmbGateway = Depends(get_gateway),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        return svc.test_connection(session, settings, gateway, actor.id, connectionId)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/remove")
def remove_connection(
    connection_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        svc.delete_connection(session, connection_id)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/shares")
def list_shares(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_shares(session, connectionId)


@router.get("/sync/monitor")
def get_monitor(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.get_monitor(session, connectionId)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/sync/stats")
def get_stats(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    share_id = None
    if connectionId is not None:
        connection = svc.get_connection(session, connectionId)
        raw = connection.get("primaryApprovedShareId")
        share_id = uuid.UUID(raw) if raw else None
    return svc.stats_dto(session, share_id)


@router.post("/sync/start")
def start_sync(
    request: Request,
    body: StartSyncRequest | None = None,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    selected = (body.connectionId if body else None) or connectionId
    try:
        return svc.start_sync(session, actor.id, selected)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except svc.SynologyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/sync/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    job = svc.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync job was not found.")
    return job


@router.get("/sync/history")
def list_history(
    request: Request,
    take: int = Query(default=50, ge=1, le=100),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_history(session, take)


@router.post("/sync/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    try:
        svc.cancel_job(session, job_id)
    except svc.SynologyValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/inventory")
def list_inventory(
    request: Request,
    search: str | None = None,
    fileType: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_inventory(session, search, fileType, page, pageSize, connectionId)
