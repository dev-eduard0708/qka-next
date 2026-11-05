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


class DeployBody(BaseModel):
    workstationIds: list[uuid.UUID] = Field(default_factory=list)
    confirmation: str = ""
    approve: bool = True


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
    deployStatus: str = Query(default=""),
    includeMissing: bool = Query(default=False),
    includeServers: bool = Query(default=False),
    filterOuDns: list[str] | None = Query(
        default=None,
        description="Display-only OU DN filter (multi). Does not change AD sync scope.",
    ),
    includeChildren: bool = Query(default=True, description="Include nested OUs under filterOuDns."),
    scopeOu: str = Query(default="", description="Legacy single display OU DN."),
    scopeOnly: bool = Query(
        default=False,
        description="Legacy: OR-filter all selected sync OUs when no display filter is set.",
    ),
    sort: str = Query(default="computerName"),
    sortDir: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=25, ge=1, le=200),
):
    _require_super_admin(request, settings, session, mutating=False)
    return svc.list_workstations(
        session,
        settings=settings,
        search=search,
        ou=ou,
        scope_ou=scopeOu,
        filter_ou_dns=list(filterOuDns or []),
        include_children=includeChildren,
        account_status=accountStatus,
        reachability=reachability,
        agent_status=agentStatus,
        assigned=assigned,
        deploy_status=deployStatus,
        include_missing=includeMissing,
        include_servers=includeServers,
        scope_only=scopeOnly,
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
    parentDn: str = Query(default=""),
    search: str = Query(default=""),
):
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.list_sync_ou_options(
            session,
            settings,
            _ldap(request),
            connection_id=connectionId,
            parent_dn=parentDn,
            search=search,
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


@router.get("/deploy/prerequisites")
def deploy_prerequisites(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    _require_super_admin(request, settings, session, mutating=False)
    from qka_api.services import agent_deployment as deploy_svc

    return deploy_svc.deployment_prerequisites(settings)


@router.post("/deploy")
def request_deploy(
    body: DeployBody,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    """Create approved deployment jobs for selected workstations only (never entire OU)."""
    from qka_api.services import agent_deployment as deploy_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        result = deploy_svc.request_deployments(
            session,
            settings,
            actor=actor,
            workstation_ids=body.workstationIds,
            confirmation=body.confirmation,
            approve=body.approve,
        )
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deploy/{job_id}/cancel")
def cancel_deploy(
    job_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    from qka_api.services import agent_deployment as deploy_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        result = deploy_svc.cancel_job(session, actor=actor, job_id=job_id)
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deploy/{job_id}/retry")
def retry_deploy(
    job_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    from qka_api.services import agent_deployment as deploy_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        result = deploy_svc.retry_job(session, settings, actor=actor, job_id=job_id)
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
