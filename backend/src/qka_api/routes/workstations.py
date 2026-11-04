"""Super Admin workstation inventory APIs (AD computers + reachability)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.ldap_client import default_ldap_gateway
from qka_api.services import workstations as svc
from qka_api.services.network_probe import default_network_probe
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings

router = APIRouter(prefix="/api/v1/admin/workstations", tags=["workstations-admin"])


class SyncBody(BaseModel):
    connectionId: uuid.UUID | None = None
    ouDns: list[str] = Field(default_factory=list)
    includeServers: bool = False
    persistOuScope: bool = True


class OuScopeBody(BaseModel):
    connectionId: uuid.UUID | None = None
    items: list[dict] = Field(default_factory=list)


class AssociateBody(BaseModel):
    qkaUserId: uuid.UUID | None = None


class PingBody(BaseModel):
    timeoutMs: int = Field(default=2000, ge=200, le=10000)


class BulkPingBody(BaseModel):
    workstationIds: list[uuid.UUID] = Field(default_factory=list)
    timeoutMs: int = Field(default=2000, ge=200, le=10000)
    concurrency: int = Field(default=8, ge=1, le=8)


def _require_super_admin(request: Request, settings: Settings, session: Session, *, mutating: bool):
    if mutating:
        require_csrf(request)
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None or not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin required.")
    return user


def _ldap(request: Request):
    return getattr(request.app.state, "ldap_gateway", None) or default_ldap_gateway()


def _probe(request: Request):
    return getattr(request.app.state, "network_probe", None) or default_network_probe()


@router.get("")
def list_workstations(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    search: str = Query(default=""),
    ou: str = Query(default=""),
    accountStatus: str = Query(default=""),
    reachability: str = Query(default=""),
    agentStatus: str = Query(default=""),
    assigned: str = Query(default=""),
    includeMissing: bool = Query(default=False),
    includeServers: bool = Query(default=False),
    sort: str = Query(default="computerName"),
    sortDir: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=25, ge=1, le=200),
):
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_workstations(
        session,
        search=search,
        ou=ou,
        account_status=accountStatus,
        reachability=reachability,
        agent_status=agentStatus,
        assigned=assigned,
        include_missing=includeMissing,
        include_servers=includeServers,
        sort=sort,
        sort_dir=sortDir,
        page=page,
        page_size=pageSize,
    )


@router.get("/sync-ou-options")
def sync_ou_options(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    connectionId: uuid.UUID | None = Query(default=None),
    search: str = Query(default=""),
):
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_sync_ou_options(
            session, settings, _ldap(request), connection_id=connectionId, search=search
        )
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/ou-scope")
def save_ou_scope(
    body: OuScopeBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.set_sync_ou_scope(
            session, connection_id=body.connectionId, selections=body.items
        )
        session.commit()
        return result
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sync")
def sync_workstations(
    body: SyncBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    admin = _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.sync_workstations(
            session,
            settings,
            _ldap(request),
            connection_id=body.connectionId,
            ou_dns=body.ouDns,
            include_servers=body.includeServers,
            persist_ou_scope=body.persistOuScope,
            actor_user_id=admin.id,
        )
        session.commit()
        return result
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ping-bulk")
def ping_bulk(
    body: BulkPingBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.probe_workstations_bulk(
            session,
            body.workstationIds,
            _probe(request),
            timeout_ms=body.timeoutMs,
            concurrency=body.concurrency,
        )
        session.commit()
        return result
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{workstation_id}/ping")
def ping_workstation(
    workstation_id: uuid.UUID,
    body: PingBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.probe_workstation(
            session, workstation_id, _probe(request), timeout_ms=body.timeoutMs
        )
        session.commit()
        return result
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{workstation_id}/employee")
def associate_employee(
    workstation_id: uuid.UUID,
    body: AssociateBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        result = svc.associate_employee(session, workstation_id, qka_user_id=body.qkaUserId)
        session.commit()
        return result
    except svc.WorkstationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
