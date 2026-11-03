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


class McpIndexingConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation: str
    sourceId: uuid.UUID | None = None
    documentIds: list[str] = Field(default_factory=list)


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


class ExtractMissingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectionId: uuid.UUID | None = None
    folderPrefix: str | None = None
    forceOutsidePilot: bool = False


class RetryFailedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectionId: uuid.UUID | None = None


class ReindexSelectedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    inventoryIds: list[uuid.UUID] = Field(default_factory=list)
    forceOutsidePilot: bool = True


class ReindexFolderRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connectionId: uuid.UUID | None = None
    folderPrefix: str
    forceOutsidePilot: bool = True


def _share_id_for_connection(session: Session, connection_id: uuid.UUID | None) -> uuid.UUID | None:
    if connection_id is None:
        return None
    connection = svc.get_connection(session, connection_id)
    raw = connection.get("primaryApprovedShareId")
    return uuid.UUID(raw) if raw else None


@router.get("/extraction/status")
def extraction_status(
    request: Request,
    connectionId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from sqlalchemy import select

    from qka_api.services import extraction as extraction_svc
    from qka_shared.models import SynologyExtractionBatch

    _require_super_admin(request, settings, session, mutating=False)
    share_id = _share_id_for_connection(session, connectionId)
    batches = session.execute(
        select(SynologyExtractionBatch).order_by(SynologyExtractionBatch.created_at.desc()).limit(10)
    ).scalars().all()
    return {
        "stats": extraction_svc.content_indexing_stats(session, share_id=share_id),
        "dependencies": extraction_svc.dependency_health(settings),
        "recentBatches": [extraction_svc.serialize_batch(row) for row in batches],
    }


@router.post("/extraction/extract-missing")
def extract_missing(
    request: Request,
    body: ExtractMissingRequest | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services import extraction as extraction_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    payload = body or ExtractMissingRequest()
    share_id = _share_id_for_connection(session, payload.connectionId)
    batch = extraction_svc.start_extract_missing(
        session,
        settings,
        actor_user_id=actor.id,
        share_id=share_id,
        folder_prefix=payload.folderPrefix,
        force_outside_pilot=payload.forceOutsidePilot,
    )
    session.commit()
    return extraction_svc.serialize_batch(batch)


@router.post("/extraction/retry-failed")
def retry_failed(
    request: Request,
    body: RetryFailedRequest | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services import extraction as extraction_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    payload = body or RetryFailedRequest()
    share_id = _share_id_for_connection(session, payload.connectionId)
    batch = extraction_svc.start_retry_failed(
        session,
        settings,
        actor_user_id=actor.id,
        share_id=share_id,
    )
    session.commit()
    return extraction_svc.serialize_batch(batch)


@router.post("/extraction/reindex-selected")
def reindex_selected(
    request: Request,
    body: ReindexSelectedRequest,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services import extraction as extraction_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    if not body.inventoryIds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="inventoryIds is required.")
    batch = extraction_svc.start_reindex_selected(
        session,
        settings,
        actor_user_id=actor.id,
        inventory_ids=body.inventoryIds,
        force_outside_pilot=body.forceOutsidePilot,
    )
    session.commit()
    return extraction_svc.serialize_batch(batch)


@router.post("/extraction/reindex-folder")
def reindex_folder(
    request: Request,
    body: ReindexFolderRequest,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    from qka_api.services import extraction as extraction_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    share_id = _share_id_for_connection(session, body.connectionId)
    if share_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="connectionId with an approved share is required.")
    try:
        batch = extraction_svc.start_reindex_folder(
            session,
            settings,
            actor_user_id=actor.id,
            share_id=share_id,
            folder_prefix=body.folderPrefix,
            force_outside_pilot=body.forceOutsidePilot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.commit()
    return extraction_svc.serialize_batch(batch)


@router.post("/mcp-indexing-confirmations")
def create_mcp_indexing_confirmation(
    request: Request,
    body: McpIndexingConfirmRequest,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Super Admin UI: mint a short-lived confirmation for MCP start_indexing."""
    from qka_api.services import mcp_capabilities as mcp_caps

    actor = _require_super_admin(request, settings, session, mutating=True)
    _guard_writes(settings)
    result = mcp_caps.create_indexing_confirmation(
        session,
        actor_id=actor.id,
        operation=body.operation,
        source_id=str(body.sourceId) if body.sourceId else None,
        document_ids=list(body.documentIds or []),
    )
    if result.get("denied"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("error") or "Denied.")
    session.commit()
    return result
