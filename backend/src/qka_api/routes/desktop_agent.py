"""Authenticated Windows Desktop Agent APIs + Super Admin management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services import desktop_agent as svc
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings

agent_router = APIRouter(prefix="/api/v1/agent/desktop", tags=["desktop-agent"])
admin_router = APIRouter(prefix="/api/v1/admin/desktop-devices", tags=["desktop-agent-admin"])


class InstallAuthorizationBody(BaseModel):
    token: str = ""
    adComputerSid: str = ""
    machineProof: str = ""


class InstallProgressBody(BaseModel):
    token: str = ""
    adComputerSid: str = ""
    machineProof: str = ""
    phase: str = ""
    msiExitCode: int | None = None
    detail: str = ""


class EnrollBody(BaseModel):
    enrollmentCode: str = ""
    machineEnrollmentToken: str = ""
    adComputerSid: str = ""
    adComputerGuid: str = ""
    machineProof: str = ""
    sidSource: str = ""
    computerName: str = ""
    deviceFingerprint: str
    domainJoinedName: str = ""
    agentVersion: str = ""


class FolderBrowseResultBody(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    error: str = ""


class FolderBrowseRequestBody(BaseModel):
    parentPath: str = ""


class InventoryBody(BaseModel):
    agentVersion: str = ""
    includeHardware: bool = True
    includeVolumes: bool = True
    hardware: dict = Field(default_factory=dict)
    disks: list[dict] = Field(default_factory=list)
    volumes: list[dict] = Field(default_factory=list)
    knownFolders: list[dict] = Field(default_factory=list)


class HeartbeatBody(BaseModel):
    agentVersion: str = ""
    computerName: str = ""
    health: str = "ok"
    lastError: str = ""


class ProgressBody(BaseModel):
    indexingState: str = "idle"
    readOnlyVerified: bool = False
    readOnlyDetail: str = ""
    filesTotal: int = 0
    filesIndexed: int = 0
    filesPending: int = 0
    filesFailed: int = 0
    filesByType: dict = Field(default_factory=dict)
    lastError: str = ""


class ErrorBody(BaseModel):
    message: str
    category: str = "agent"


class CreateEnrollmentBody(BaseModel):
    """Device-registration codes omit qkaUserId (install → register → assign)."""

    qkaUserId: uuid.UUID | None = None
    label: str = ""


class AssignEmployeeBody(BaseModel):
    qkaUserId: uuid.UUID | None = None


class FoldersBody(BaseModel):
    folders: list[dict] = Field(default_factory=list)


class CommandBody(BaseModel):
    command: str


def _require_super_admin(request: Request, settings: Settings, session: Session, *, mutating: bool):
    if mutating:
        require_csrf(request)
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None or not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin required.")
    return user


def _device_auth(
    session: Session = Depends(get_session),
    x_qka_device_id: str | None = Header(default=None, alias="X-QKA-Device-Id"),
    x_qka_device_key: str | None = Header(default=None, alias="X-QKA-Device-Key"),
    authorization: str | None = Header(default=None),
):
    device_id_raw = (x_qka_device_id or "").strip()
    device_key = (x_qka_device_key or "").strip()
    if authorization and authorization.lower().startswith("bearer "):
        # Bearer format: <deviceId>.<deviceKey>
        token = authorization[7:].strip()
        if "." in token and not device_id_raw:
            device_id_raw, device_key = token.split(".", 1)
    try:
        device_id = uuid.UUID(device_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Device authentication required.") from exc
    try:
        return svc.authenticate_device(session, device_id, device_key)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@agent_router.post("/install-authorization")
def install_authorization(body: InstallAuthorizationBody, session: Session = Depends(get_session)):
    """Pre-install gate for the GPO scheduled task. Does not consume the token."""
    from qka_api.services import agent_deployment as deploy_svc

    return deploy_svc.install_authorization(
        session,
        token=body.token,
        ad_computer_sid=body.adComputerSid,
        machine_proof=body.machineProof,
    )


@agent_router.post("/install-progress")
def install_progress(body: InstallProgressBody, session: Session = Depends(get_session)):
    """Windows Scheduled Task progress — authenticated by token + SID + machineProof."""
    from qka_api.services import agent_deployment as deploy_svc

    try:
        return deploy_svc.report_install_progress(
            session,
            token=body.token,
            ad_computer_sid=body.adComputerSid,
            machine_proof=body.machineProof,
            phase=body.phase,
            msi_exit_code=body.msiExitCode,
            detail=body.detail,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@agent_router.post("/upgrade-authorization")
def upgrade_authorization(body: InstallAuthorizationBody, session: Session = Depends(get_session)):
    from qka_api.services import agent_upgrade as upg_svc

    return upg_svc.upgrade_authorization(
        session,
        token=body.token,
        ad_computer_sid=body.adComputerSid,
        machine_proof=body.machineProof,
    )


@agent_router.post("/upgrade-progress")
def upgrade_progress(body: InstallProgressBody, session: Session = Depends(get_session)):
    from qka_api.services import agent_upgrade as upg_svc

    return upg_svc.report_upgrade_progress(
        session,
        token=body.token,
        ad_computer_sid=body.adComputerSid,
        machine_proof=body.machineProof,
        phase=body.phase,
        msi_exit_code=body.msiExitCode,
        detail=body.detail,
    )


@agent_router.post("/enroll")
def enroll(
    body: EnrollBody,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
):
    try:
        return svc.enroll_device(
            session,
            settings,
            enrollment_code=body.enrollmentCode,
            machine_enrollment_token=body.machineEnrollmentToken,
            ad_computer_sid=body.adComputerSid,
            ad_computer_guid=body.adComputerGuid,
            machine_proof=body.machineProof,
            sid_source=body.sidSource,
            computer_name=body.computerName,
            device_fingerprint=body.deviceFingerprint,
            domain_joined_name=body.domainJoinedName,
            agent_version=body.agentVersion,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@agent_router.post("/heartbeat")
def heartbeat(body: HeartbeatBody, device=Depends(_device_auth), session: Session = Depends(get_session)):
    return svc.heartbeat(
        session,
        device,
        agent_version=body.agentVersion,
        computer_name=body.computerName,
        health=body.health,
        last_error=body.lastError,
    )


@agent_router.get("/policy")
def policy(device=Depends(_device_auth), session: Session = Depends(get_session)):
    return svc.get_policy(session, device)


@agent_router.post("/progress")
def progress(body: ProgressBody, device=Depends(_device_auth), session: Session = Depends(get_session)):
    return svc.report_progress(
        session,
        device,
        indexing_state=body.indexingState,
        read_only_verified=body.readOnlyVerified,
        read_only_detail=body.readOnlyDetail,
        files_total=body.filesTotal,
        files_indexed=body.filesIndexed,
        files_pending=body.filesPending,
        files_failed=body.filesFailed,
        files_by_type=body.filesByType,
        last_error=body.lastError,
    )


@agent_router.post("/errors")
def errors(body: ErrorBody, device=Depends(_device_auth), session: Session = Depends(get_session)):
    return svc.report_error(session, device, message=body.message, category=body.category)


@agent_router.post("/folder-browse/{browse_id}/result")
def folder_browse_result(
    browse_id: uuid.UUID,
    body: FolderBrowseResultBody,
    device=Depends(_device_auth),
    session: Session = Depends(get_session),
):
    try:
        return svc.complete_folder_browse(
            session,
            device,
            browse_id=browse_id,
            nodes=body.nodes,
            error=body.error,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@agent_router.post("/inventory")
def post_inventory(
    body: InventoryBody,
    device=Depends(_device_auth),
    session: Session = Depends(get_session),
):
    from qka_api.services import desktop_inventory as inv_svc

    return inv_svc.upsert_inventory(
        session,
        device,
        payload={
            "agentVersion": body.agentVersion,
            "includeHardware": body.includeHardware,
            "includeVolumes": body.includeVolumes,
            "hardware": body.hardware,
            "disks": body.disks,
            "volumes": body.volumes,
            "knownFolders": body.knownFolders,
        },
    )


@admin_router.get("")
def list_devices(
    request: Request,
    qkaUserId: uuid.UUID | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=False)
    return {"items": svc.list_devices(session, qka_user_id=qkaUserId)}


@admin_router.get("/{device_id}")
def get_device(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=False)
    rows = svc.list_devices(session)
    match = next((r for r in rows if r["id"] == str(device_id)), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Device was not found.")
    return match


@admin_router.post("/enrollment-codes")
def create_code(
    body: CreateEnrollmentBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        return svc.create_enrollment_code(
            session,
            settings,
            actor_id=actor.id,
            qka_user_id=body.qkaUserId,
            label=body.label,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/{device_id}/assign-employee")
def assign_employee(
    device_id: uuid.UUID,
    body: AssignEmployeeBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        return svc.assign_device_employee(
            session,
            device_id=device_id,
            qka_user_id=body.qkaUserId,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.put("/{device_id}/folders")
def put_folders(
    device_id: uuid.UUID,
    body: FoldersBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        folders = svc.set_folders(session, device_id, body.folders)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"folders": folders}


@admin_router.post("/{device_id}/folder-browse")
def request_folder_browse(
    device_id: uuid.UUID,
    body: FolderBrowseRequestBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        return svc.request_folder_browse(
            session,
            actor_id=actor.id,
            device_id=device_id,
            parent_path=body.parentPath,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/{device_id}/folder-browse/{browse_id}")
def get_folder_browse(
    device_id: uuid.UUID,
    browse_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=False)
    try:
        return svc.get_folder_browse(session, device_id=device_id, browse_id=browse_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@admin_router.get("/{device_id}/inventory")
def get_inventory(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    from qka_api.services import desktop_inventory as inv_svc
    from qka_shared.models import DesktopDevice

    _require_super_admin(request, settings, session, mutating=False)
    device = session.get(DesktopDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device was not found.")
    inv = inv_svc.inventory_dto(session, device)
    if inv is None:
        return {"inventory": None, "note": "No inventory synced yet."}
    return {"inventory": inv}


@admin_router.post("/{device_id}/inventory/refresh")
def refresh_inventory(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    from qka_api.services import desktop_inventory as inv_svc

    _require_super_admin(request, settings, session, mutating=True)
    try:
        return inv_svc.request_inventory_refresh(session, device_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@admin_router.get("/{device_id}/browse-roots")
def browse_roots(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    from qka_api.services import desktop_inventory as inv_svc
    from qka_shared.models import DesktopDevice

    _require_super_admin(request, settings, session, mutating=False)
    device = session.get(DesktopDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device was not found.")
    roots = inv_svc.browse_roots_dto(session, device)
    dto = svc.device_dto(session, device)
    roots["online"] = bool(dto.get("online"))
    roots["folderBrowseAvailable"] = bool(dto.get("folderBrowseAvailable"))
    return roots


@admin_router.get("/{device_id}/folder-cache")
def get_folder_cache(
    device_id: uuid.UUID,
    request: Request,
    parentPath: str = "",
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    from qka_api.services import desktop_inventory as inv_svc
    from qka_shared.models import DesktopDevice

    _require_super_admin(request, settings, session, mutating=False)
    device = session.get(DesktopDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device was not found.")
    cached = inv_svc.get_folder_cache(session, device_id=device_id, parent_path=parentPath)
    return {"cache": cached, "online": bool(svc.device_dto(session, device).get("online"))}


@admin_router.post("/{device_id}/command")
def command(
    device_id: uuid.UUID,
    body: CommandBody,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        return svc.set_indexing_command(session, device_id, body.command)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/{device_id}/revoke")
def revoke(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    _require_super_admin(request, settings, session, mutating=True)
    try:
        return svc.revoke_device(session, device_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@admin_router.post("/{device_id}/upgrade")
def schedule_upgrade(
    device_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    """Explicit single-device agent upgrade. Never fleet-wide."""
    from qka_api.services import agent_upgrade as upg_svc

    actor = _require_super_admin(request, settings, session, mutating=True)
    try:
        return upg_svc.schedule_upgrade(
            session,
            settings,
            actor_id=actor.id,
            device_id=device_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@admin_router.post("/upgrades/{job_id}/cancel")
def cancel_upgrade(
    job_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
):
    from qka_api.services import agent_upgrade as upg_svc

    _require_super_admin(request, settings, session, mutating=True)
    try:
        return upg_svc.cancel_upgrade(session, settings, job_id=job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
