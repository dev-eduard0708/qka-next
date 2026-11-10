"""Windows Desktop Agent enrollment, policy, and telemetry (pilot)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, USER_ACTIVE
from qka_shared.config import Settings
from qka_shared.models import (
    DEVICE_ACTIVE,
    DEVICE_ERROR,
    DEVICE_PAUSED,
    DEVICE_PENDING,
    DEVICE_REVOKED,
    FOLDER_BROWSE_COMPLETED,
    FOLDER_BROWSE_EXPIRED,
    FOLDER_BROWSE_FAILED,
    FOLDER_BROWSE_PENDING,
    INDEX_ERROR,
    INDEX_IDLE,
    INDEX_PAUSED,
    INDEX_RUNNING,
    AdWorkstation,
    DesktopDevice,
    DesktopDeviceFolder,
    DesktopEnrollmentCode,
    DesktopFolderBrowse,
    ExternalIdentity,
    QkaUser,
)

AGENT_SUPPORTED_EXTS = ("pdf", "docx", "xlsx", "txt", "csv")
DEFAULT_EXCLUDES = (
    r"%USERPROFILE%\AppData",
    r"%LOCALAPPDATA%",
    r"%APPDATA%",
    r"%USERPROFILE%\AppData\Local\Google\Chrome",
    r"%USERPROFILE%\AppData\Local\Microsoft\Edge",
    r"%USERPROFILE%\AppData\Roaming\Mozilla",
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_win_path(path: str) -> str:
    text = (path or "").strip().replace("/", "\\")
    while "\\\\" in text[2:]:
        text = text[:2] + text[2:].replace("\\\\", "\\")
    # Preserve drive-root form C:\
    if len(text) == 2 and text[1] == ":":
        return text + "\\"
    if len(text) >= 3 and text[1] == ":" and text[2] == "\\" and text.rstrip("\\") == text[:2]:
        return text[:3]
    return text.rstrip("\\")


def _status_label(status: int) -> str:
    return {
        DEVICE_PENDING: "pending",
        DEVICE_ACTIVE: "active",
        DEVICE_PAUSED: "paused",
        DEVICE_REVOKED: "revoked",
        DEVICE_ERROR: "error",
    }.get(status, "unknown")


def _index_label(state: int) -> str:
    return {
        INDEX_IDLE: "idle",
        INDEX_RUNNING: "running",
        INDEX_PAUSED: "paused",
        INDEX_ERROR: "error",
    }.get(state, "unknown")


def _online(device: DesktopDevice, *, within_seconds: int = 180) -> bool:
    if device.last_heartbeat_at is None:
        return False
    return (_now() - device.last_heartbeat_at).total_seconds() <= within_seconds


def require_ad_linked_user(session: Session, user_id: uuid.UUID) -> QkaUser:
    """Resolve an AD-linked ownership candidate.

    Source ownership / assignment must not require LoginEnabled — a login-disabled
    employee can own a workstation source while the agent keeps running; that
    employee simply cannot sign in to QKA until login is enabled.
    """
    user = session.get(QkaUser, user_id)
    if user is None or int(user.status) != USER_ACTIVE:
        raise LookupError("QKA user was not found or is not active.")
    ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.qka_user_id == user_id,
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.account_status == ACCOUNT_ENABLED,
        )
    ).scalar_one_or_none()
    if ad is None:
        raise ValueError("Select an AD-linked eligible employee for desktop agent enrollment.")
    return user


def _optional_owner_id(session: Session, candidate: uuid.UUID | None) -> uuid.UUID | None:
    """Bind ownership when the candidate is AD-linked and active; never block enroll."""
    if candidate is None:
        return None
    try:
        return require_ad_linked_user(session, candidate).id
    except (LookupError, ValueError):
        return None


def sync_workstation_ownership(
    session: Session,
    *,
    workstation_id: uuid.UUID | None,
    qka_user_id: uuid.UUID | None,
) -> None:
    """Keep AdWorkstation.assigned_qka_user_id aligned with device source ownership."""
    if workstation_id is None:
        return
    ws = session.get(AdWorkstation, workstation_id)
    if ws is None:
        return
    ws.assigned_qka_user_id = qka_user_id
    ws.updated_at = _now()


def create_enrollment_code(
    session: Session,
    settings: Settings,
    *,
    actor_id: uuid.UUID,
    qka_user_id: uuid.UUID | None = None,
    label: str = "",
) -> dict:
    """Issue a one-time code. Prefer device-only (qka_user_id=None): install → register → assign.

    Employee-bound codes remain a manual fallback when explicitly requested.
    """
    bound_user_id: uuid.UUID | None = None
    if qka_user_id is not None:
        require_ad_linked_user(session, qka_user_id)
        bound_user_id = qka_user_id
    hours = max(1, int(settings.agent_enrollment_hours or 24))
    plain = secrets.token_urlsafe(24)
    row = DesktopEnrollmentCode(
        id=uuid.uuid4(),
        qka_user_id=bound_user_id,
        code_hash=_hash_secret(plain),
        label=(label or "").strip()[:256],
        expires_at=_now() + timedelta(hours=hours),
        consumed_at=None,
        created_by_user_id=actor_id,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    base = settings.agent_public_base
    mode = "device_registration" if bound_user_id is None else "employee_bound_fallback"
    return {
        "id": str(row.id),
        "enrollmentCode": plain,
        "expiresAt": row.expires_at.isoformat(),
        "qkaUserId": str(bound_user_id) if bound_user_id else None,
        "registrationMode": mode,
        "serverBaseUrl": base,
        "installHint": (
            "Install QKA-Agent.msi silently, then set server URL + this one-time code "
            "(env/config only — never embed in the MSI). Device registers unassigned; "
            "admins may browse/approve folders; assign an employee before document access."
            if bound_user_id is None
            else "Legacy employee-bound code. Prefer device registration codes for install-before-assign."
        ),
    }


def enroll_device(
    session: Session,
    settings: Settings,
    *,
    enrollment_code: str = "",
    machine_enrollment_token: str = "",
    ad_computer_sid: str = "",
    ad_computer_guid: str = "",
    machine_proof: str = "",
    sid_source: str = "",
    computer_name: str,
    device_fingerprint: str,
    domain_joined_name: str = "",
    agent_version: str = "",
) -> dict:
    """Enroll via machine-bound token (managed deploy) or one-time enrollment code (manual fallback).

    Hostname/MAC/IP/email alone are never accepted as proof of device identity for
    the managed path — AD computer SID + HMAC machineProof bound to the one-time token.
    """
    from qka_api.services import agent_deployment as deploy_svc

    code = (enrollment_code or "").strip()
    machine_token = (machine_enrollment_token or "").strip()
    sid = (ad_computer_sid or "").strip()
    guid = (ad_computer_guid or "").strip()
    fp = (device_fingerprint or "").strip()
    if not fp:
        raise ValueError("Device fingerprint is required.")

    qka_user_id: uuid.UUID | None = None
    workstation_id: uuid.UUID | None = None
    machine_row = None
    code_row = None
    enrollment_mode = "enrollment_code"

    if machine_token:
        if not sid:
            raise ValueError("AD computer SID is required for machine-identity enrollment.")
        if (sid_source or "").strip().lower() not in {"local_machine", "lookup_account_name"}:
            raise PermissionError(
                "Machine SID must be resolved from the local computer account "
                "(sidSource=local_machine). Client-supplied SID claims are rejected."
            )
        machine_row, ws = deploy_svc.consume_machine_enrollment_token(
            session,
            token=machine_token,
            ad_computer_sid=sid,
            ad_computer_guid=guid,
            machine_proof=machine_proof,
        )
        # Machine auth alone authorizes registration. Employee assignment is ownership only.
        if (
            machine_row.qka_user_id
            and ws.assigned_qka_user_id
            and ws.assigned_qka_user_id != machine_row.qka_user_id
        ):
            raise PermissionError("Workstation employee assignment does not match enrollment token.")
        owner_candidate = machine_row.qka_user_id or ws.assigned_qka_user_id
        qka_user_id = _optional_owner_id(session, owner_candidate)
        workstation_id = ws.id
        enrollment_mode = "machine_identity"
    elif code:
        code_row = session.execute(
            select(DesktopEnrollmentCode).where(DesktopEnrollmentCode.code_hash == _hash_secret(code))
        ).scalar_one_or_none()
        if code_row is None:
            raise PermissionError("Enrollment code is invalid.")
        if code_row.consumed_at is not None:
            raise PermissionError("Enrollment code was already used.")
        if code_row.expires_at < _now():
            raise PermissionError("Enrollment code has expired.")
        if code_row.qka_user_id is not None:
            # Manual employee-bound codes still require a real AD-linked owner (LoginEnabled optional).
            require_ad_linked_user(session, code_row.qka_user_id)
            qka_user_id = code_row.qka_user_id
            enrollment_mode = "enrollment_code"
        else:
            enrollment_mode = "device_registration"
    else:
        raise ValueError("Enrollment code or machine enrollment token is required.")

    # Prefer SID uniqueness for managed enroll; fingerprint for manual fallback.
    existing = None
    if sid:
        existing = session.execute(
            select(DesktopDevice).where(
                DesktopDevice.ad_computer_sid == sid,
                DesktopDevice.status != DEVICE_REVOKED,
            )
        ).scalar_one_or_none()
    if existing is None:
        existing = session.execute(
            select(DesktopDevice).where(
                DesktopDevice.device_fingerprint == fp,
                DesktopDevice.status != DEVICE_REVOKED,
            )
        ).scalar_one_or_none()

    device_key = secrets.token_urlsafe(32)
    now = _now()
    if existing is not None:
        if (
            existing.qka_user_id is not None
            and qka_user_id is not None
            and existing.qka_user_id != qka_user_id
        ):
            raise PermissionError("This computer is already enrolled to another employee.")
        existing.device_key_hash = _hash_secret(device_key)
        existing.computer_name = (computer_name or "").strip()[:256]
        existing.domain_joined_name = (domain_joined_name or "").strip()[:256]
        existing.agent_version = (agent_version or "").strip()[:64]
        existing.ad_computer_sid = sid[:256] if sid else existing.ad_computer_sid
        existing.ad_computer_guid = guid[:64] if guid else existing.ad_computer_guid
        if workstation_id:
            existing.workstation_id = workstation_id
        # Do not overwrite an existing assignment with null during re-enroll.
        if qka_user_id is not None and existing.qka_user_id is None:
            existing.qka_user_id = qka_user_id
        existing.status = DEVICE_ACTIVE
        existing.enrolled_at = now
        existing.updated_at = now
        existing.last_heartbeat_at = now
        device = existing
    else:
        device = DesktopDevice(
            id=uuid.uuid4(),
            qka_user_id=qka_user_id,
            device_key_hash=_hash_secret(device_key),
            computer_name=(computer_name or "").strip()[:256],
            device_fingerprint=fp[:256],
            ad_computer_sid=sid[:256],
            ad_computer_guid=guid[:64],
            workstation_id=workstation_id,
            domain_joined_name=(domain_joined_name or "").strip()[:256],
            agent_version=(agent_version or "").strip()[:64],
            status=DEVICE_ACTIVE,
            indexing_state=INDEX_IDLE,
            enrolled_at=now,
            created_at=now,
            updated_at=now,
            last_heartbeat_at=now,
        )
        session.add(device)

    if machine_row is not None:
        machine_row.consumed_at = now
        if workstation_id:
            deploy_svc.mark_job_registered(session, workstation_id=workstation_id, device=device)
            ws_row = session.get(AdWorkstation, workstation_id)
            if ws_row is not None:
                ws_row.desktop_device_id = device.id
                # Ownership association only — never a deploy gate.
                if device.qka_user_id is not None and ws_row.assigned_qka_user_id is None:
                    ws_row.assigned_qka_user_id = device.qka_user_id
                    ws_row.updated_at = now
    if code_row is not None:
        code_row.consumed_at = now
    session.flush()
    return {
        "deviceId": str(device.id),
        "deviceKey": device_key,
        "qkaUserId": str(device.qka_user_id) if device.qka_user_id else None,
        "employeeAssigned": bool(device.qka_user_id),
        "indexingEnabled": False,
        "serverBaseUrl": settings.agent_public_base,
        "supportedExtensions": list(AGENT_SUPPORTED_EXTS),
        "defaultExcludes": list(DEFAULT_EXCLUDES),
        "contentUploadEnabled": False,
        "enrollmentMode": enrollment_mode,
        "note": (
            "Device registered. Approve folders to index; assign an employee later for source ownership."
            if device.qka_user_id is None
            else "Device registered. Source ownership associated; LoginEnabled is not required for the agent."
        ),
    }


def authenticate_device(session: Session, device_id: uuid.UUID, device_key: str) -> DesktopDevice:
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise PermissionError("Device is not enrolled or has been revoked.")
    if not hmac.compare_digest(device.device_key_hash, _hash_secret(device_key or "")):
        raise PermissionError("Device credentials are invalid.")
    return device


def heartbeat(
    session: Session,
    device: DesktopDevice,
    *,
    agent_version: str = "",
    computer_name: str = "",
    health: str = "ok",
    last_error: str = "",
) -> dict:
    device.last_heartbeat_at = _now()
    device.updated_at = _now()
    if agent_version:
        device.agent_version = agent_version.strip()[:64]
    if computer_name:
        device.computer_name = computer_name.strip()[:256]
    if health == "error" and last_error:
        device.status = DEVICE_ERROR
        device.last_error = last_error.strip()[:2000]
    elif device.status == DEVICE_ERROR and health == "ok":
        device.status = DEVICE_ACTIVE
        device.last_error = ""
    elif device.status == DEVICE_PENDING:
        device.status = DEVICE_ACTIVE
    session.flush()
    try:
        from qka_api.services import agent_upgrade as upg_svc

        upg_svc.observe_heartbeat_for_upgrade(session, device)
    except Exception:
        pass
    return {"ok": True, "serverTime": _now().isoformat(), "status": _status_label(device.status)}


def _employee_ad_sid(session: Session, qka_user_id: uuid.UUID | None) -> str:
    if qka_user_id is None:
        return ""
    ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.qka_user_id == qka_user_id,
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
        )
    ).scalar_one_or_none()
    return (ad.stable_external_id or "").strip() if ad else ""


def get_policy(session: Session, device: DesktopDevice) -> dict:
    assigned = device.qka_user_id is not None
    folders = session.execute(
        select(DesktopDeviceFolder).where(
            DesktopDeviceFolder.device_id == device.id,
            DesktopDeviceFolder.enabled.is_(True),
        )
    ).scalars().all()
    # Indexing follows approved folders + admin start/pause — independent of employee assignment.
    indexing_on = bool(folders) and device.indexing_state == INDEX_RUNNING and device.status != DEVICE_PAUSED
    command = "run" if indexing_on else "pause"
    if device.status == DEVICE_REVOKED:
        command = "stop"
    now = _now()
    pending_browse = session.execute(
        select(DesktopFolderBrowse).where(
            DesktopFolderBrowse.device_id == device.id,
            DesktopFolderBrowse.status == FOLDER_BROWSE_PENDING,
            DesktopFolderBrowse.expires_at > now,
        )
    ).scalars().all()
    employee_sid = _employee_ad_sid(session, device.qka_user_id) if assigned else ""
    from qka_api.services import desktop_inventory as inv_svc

    inventory_hints = inv_svc.inventory_due(session, device)
    return {
        "deviceId": str(device.id),
        "command": command,
        "employeeAssigned": assigned,
        "indexingEnabled": indexing_on,
        "approvedFolders": [
            {
                "id": str(f.id),
                "path": f.path_normalized,
                "includeSubfolders": bool(f.include_subfolders),
            }
            for f in folders
        ],
        "pendingFolderBrowse": [
            {
                "id": str(b.id),
                "parentPath": b.parent_path or "",
                "employeeAdSid": b.employee_ad_sid,
                "maxEntries": int(b.max_entries or 200),
                "timeoutSeconds": 8,
            }
            for b in pending_browse
        ],
        "inventory": inventory_hints,
        "employeeAdSid": employee_sid,
        "supportedExtensions": list(AGENT_SUPPORTED_EXTS),
        "defaultExcludes": list(DEFAULT_EXCLUDES),
        "contentUploadEnabled": False,
        "throttleWhenBusy": True,
        "localIndexOnly": True,
        "requireReadOnlyVerified": True,
        "scannerMustNotBeSystemAdmin": True,
        "forbidSourceAclChanges": True,
        "sourceAccessRestricted": not assigned,
        "note": (
            ""
            if assigned
            else "Employee unassigned — agent may index approved folders; indexed content stays restricted until ownership is assigned."
        ),
    }


def assign_device_employee(
    session: Session,
    *,
    device_id: uuid.UUID,
    qka_user_id: uuid.UUID | None,
) -> dict:
    """Assign or clear source ownership. Does not gate agent deploy/indexing folders."""
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise LookupError("Device was not found.")
    if qka_user_id is None:
        # Revoke ownership access only — preserve approved folders and index history.
        device.qka_user_id = None
        device.read_only_detail = (
            "Employee unassigned — indexed content restricted; approved folders preserved."
        )
        device.updated_at = _now()
        sync_workstation_ownership(session, workstation_id=device.workstation_id, qka_user_id=None)
        session.flush()
        return device_dto(session, device)

    require_ad_linked_user(session, qka_user_id)
    sid = _employee_ad_sid(session, qka_user_id)
    if not sid:
        raise ValueError("Assigned employee has no linked AD SID.")
    device.qka_user_id = qka_user_id
    device.updated_at = _now()
    sync_workstation_ownership(session, workstation_id=device.workstation_id, qka_user_id=qka_user_id)
    session.flush()
    return device_dto(session, device)


def request_folder_browse(
    session: Session,
    *,
    actor_id: uuid.UUID,
    device_id: uuid.UUID,
    parent_path: str = "",
) -> dict:
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise LookupError("Device was not found.")
    if not _online(device):
        raise ValueError(
            "Agent is offline or not heartbeating. Folder browsing requires a connected Desktop Agent "
            "(outbound HTTPS poll). Install/enroll the agent and wait until status is Connected."
        )
    # Employee SID is optional profile hint; unassigned devices may still browse machine paths.
    employee_sid = _employee_ad_sid(session, device.qka_user_id)
    now = _now()
    row = DesktopFolderBrowse(
        id=uuid.uuid4(),
        device_id=device.id,
        requested_by_user_id=actor_id,
        employee_ad_sid=employee_sid,
        parent_path=_normalize_win_path(parent_path)[:2048],
        status=FOLDER_BROWSE_PENDING,
        result_json="[]",
        error="",
        max_entries=200,
        created_at=now,
        expires_at=now + timedelta(minutes=2),
        completed_at=None,
    )
    session.add(row)
    session.flush()
    return {
        "id": str(row.id),
        "deviceId": str(device.id),
        "status": "pending",
        "parentPath": row.parent_path,
        "employeeAdSid": employee_sid,
        "expiresAt": row.expires_at.isoformat(),
        "note": "Waiting for the connected agent to fulfill this browse over outbound HTTPS.",
    }


def get_folder_browse(session: Session, *, device_id: uuid.UUID, browse_id: uuid.UUID) -> dict:
    row = session.get(DesktopFolderBrowse, browse_id)
    if row is None or row.device_id != device_id:
        raise LookupError("Folder browse request was not found.")
    if row.status == FOLDER_BROWSE_PENDING and row.expires_at < _now():
        row.status = FOLDER_BROWSE_EXPIRED
        row.error = "Browse request expired before the agent responded."
        row.completed_at = _now()
        session.flush()
    try:
        nodes = json.loads(row.result_json or "[]")
    except json.JSONDecodeError:
        nodes = []
    status = {
        FOLDER_BROWSE_PENDING: "pending",
        FOLDER_BROWSE_COMPLETED: "completed",
        FOLDER_BROWSE_FAILED: "failed",
        FOLDER_BROWSE_EXPIRED: "expired",
    }.get(row.status, "unknown")
    return {
        "id": str(row.id),
        "deviceId": str(row.device_id),
        "status": status,
        "parentPath": row.parent_path,
        "nodes": nodes if isinstance(nodes, list) else [],
        "error": row.error or "",
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "completedAt": row.completed_at.isoformat() if row.completed_at else None,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
    }


def complete_folder_browse(
    session: Session,
    device: DesktopDevice,
    *,
    browse_id: uuid.UUID,
    nodes: list[dict],
    error: str = "",
) -> dict:
    row = session.get(DesktopFolderBrowse, browse_id)
    if row is None or row.device_id != device.id:
        raise LookupError("Folder browse request was not found.")
    if row.status != FOLDER_BROWSE_PENDING:
        raise ValueError("Folder browse request is no longer pending.")
    if row.expires_at < _now():
        row.status = FOLDER_BROWSE_EXPIRED
        row.error = "Browse request expired."
        row.completed_at = _now()
        session.flush()
        raise ValueError("Folder browse request expired.")
    # Accept directory metadata only — strip unexpected keys / reject file content fields.
    clean: list[dict] = []
    for item in nodes or []:
        if not isinstance(item, dict):
            continue
        path = _normalize_win_path(str(item.get("path") or ""))
        name = str(item.get("name") or "").strip()[:256]
        if not name:
            continue
        node = {
            "name": name,
            "path": path[:2048],
            "kind": str(item.get("kind") or "directory")[:32],
            "accessible": bool(item.get("accessible")),
            "hasChildren": bool(item.get("hasChildren")),
            "detail": str(item.get("detail") or "")[:512],
        }
        for key in ("capacityBytes", "freeBytes", "usedBytes"):
            if key in item:
                try:
                    node[key] = int(item.get(key) or 0)
                except (TypeError, ValueError):
                    node[key] = 0
        clean.append(node)
        if len(clean) >= int(row.max_entries or 200):
            break
    err = (error or "").strip()
    row.result_json = json.dumps(clean, ensure_ascii=False)
    row.error = err[:2000]
    row.status = FOLDER_BROWSE_FAILED if err and not clean else FOLDER_BROWSE_COMPLETED
    if err and clean:
        row.status = FOLDER_BROWSE_COMPLETED
    row.completed_at = _now()
    try:
        from qka_api.services import desktop_inventory as inv_svc

        inv_svc.upsert_folder_cache(
            session,
            device_id=device.id,
            parent_path=row.parent_path or "",
            nodes=clean,
            error=err,
        )
    except Exception:
        pass
    device.last_heartbeat_at = _now()
    device.updated_at = _now()
    session.flush()
    return {"ok": True, "id": str(row.id), "status": "completed" if row.status == FOLDER_BROWSE_COMPLETED else "failed"}


def report_progress(
    session: Session,
    device: DesktopDevice,
    *,
    indexing_state: str,
    read_only_verified: bool,
    read_only_detail: str = "",
    files_total: int = 0,
    files_indexed: int = 0,
    files_pending: int = 0,
    files_failed: int = 0,
    files_by_type: dict | None = None,
    last_error: str = "",
) -> dict:
    state_map = {
        "idle": INDEX_IDLE,
        "running": INDEX_RUNNING,
        "paused": INDEX_PAUSED,
        "error": INDEX_ERROR,
    }
    device.indexing_state = state_map.get((indexing_state or "").lower(), INDEX_IDLE)
    device.read_only_verified = bool(read_only_verified)
    device.read_only_detail = (read_only_detail or "").strip()[:1024]
    device.files_total = max(0, int(files_total))
    device.files_indexed = max(0, int(files_indexed))
    device.files_pending = max(0, int(files_pending))
    device.files_failed = max(0, int(files_failed))
    device.files_by_type_json = json.dumps(files_by_type or {}, ensure_ascii=False)[:4000]
    device.last_error = (last_error or "").strip()[:2000]
    device.last_index_at = _now()
    device.last_heartbeat_at = _now()
    device.updated_at = _now()
    if not read_only_verified and device.indexing_state == INDEX_RUNNING:
        device.indexing_state = INDEX_ERROR
        if not device.last_error:
            device.last_error = "Indexing blocked: read-only verification failed."
    session.flush()
    return {"ok": True}


def report_error(session: Session, device: DesktopDevice, *, message: str, category: str = "agent") -> dict:
    device.last_error = f"[{category}] {(message or '').strip()}"[:2000]
    device.status = DEVICE_ERROR
    device.indexing_state = INDEX_ERROR
    device.updated_at = _now()
    session.flush()
    return {"ok": True}


def set_folders(session: Session, device_id: uuid.UUID, paths: list[dict]) -> list[dict]:
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise LookupError("Device was not found.")
    existing = session.execute(
        select(DesktopDeviceFolder).where(DesktopDeviceFolder.device_id == device_id)
    ).scalars().all()
    for row in existing:
        session.delete(row)
    session.flush()
    now = _now()
    out: list[DesktopDeviceFolder] = []
    for item in paths:
        raw = item.get("path") if isinstance(item, dict) else str(item)
        path = _normalize_win_path(str(raw or ""))
        if not path:
            continue
        # Reject obvious traversal / UNC into agent-controlled write targets later on agent side.
        try:
            PureWindowsPath(path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Invalid folder path: {path}") from exc
        row = DesktopDeviceFolder(
            id=uuid.uuid4(),
            device_id=device_id,
            path_normalized=path[:2048],
            include_subfolders=bool(item.get("includeSubfolders", True)) if isinstance(item, dict) else True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        out.append(row)
    device.updated_at = now
    session.flush()
    return [
        {"id": str(r.id), "path": r.path_normalized, "includeSubfolders": r.include_subfolders, "enabled": r.enabled}
        for r in out
    ]


def set_indexing_command(session: Session, device_id: uuid.UUID, command: str) -> dict:
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise LookupError("Device was not found.")
    cmd = (command or "").strip().lower()
    if cmd in {"start", "resume"}:
        folders = session.execute(
            select(DesktopDeviceFolder).where(
                DesktopDeviceFolder.device_id == device_id,
                DesktopDeviceFolder.enabled.is_(True),
            )
        ).scalars().first()
        if folders is None:
            raise ValueError("Approve at least one folder before starting indexing.")
    if cmd == "start":
        device.status = DEVICE_ACTIVE
        device.indexing_state = INDEX_RUNNING
    elif cmd == "pause":
        device.status = DEVICE_PAUSED
        device.indexing_state = INDEX_PAUSED
    elif cmd == "resume":
        device.status = DEVICE_ACTIVE
        device.indexing_state = INDEX_RUNNING
    else:
        raise ValueError("command must be start, pause, or resume.")
    device.updated_at = _now()
    session.flush()
    return device_dto(session, device)


def revoke_device(session: Session, device_id: uuid.UUID) -> dict:
    device = session.get(DesktopDevice, device_id)
    if device is None:
        raise LookupError("Device was not found.")
    device.status = DEVICE_REVOKED
    device.indexing_state = INDEX_IDLE
    device.revoked_at = _now()
    device.updated_at = _now()
    # Rotate key hash so old credentials cannot authenticate.
    device.device_key_hash = _hash_secret(secrets.token_urlsafe(32))
    session.flush()
    return device_dto(session, device)


def list_devices(session: Session, *, qka_user_id: uuid.UUID | None = None) -> list[dict]:
    stmt = select(DesktopDevice).order_by(DesktopDevice.updated_at.desc())
    if qka_user_id is not None:
        stmt = stmt.where(DesktopDevice.qka_user_id == qka_user_id)
    rows = session.execute(stmt).scalars().all()
    return [device_dto(session, row) for row in rows]


def _workstation_hostname(session: Session, device: DesktopDevice) -> str:
    """Prefer the verified AD workstation computer name; never invent or use domain NetBIOS."""
    if device.workstation_id:
        ws = session.get(AdWorkstation, device.workstation_id)
        if ws is not None:
            name = (ws.computer_name or "").strip()
            if name:
                return name
    reported = (device.computer_name or "").strip()
    # Agent sometimes reports domain NetBIOS in domain_joined_name; computer_name is the host.
    if reported and reported.upper() not in {"QEHC", "WORKGROUP"}:
        return reported
    return ""


def _employee_email(session: Session, user: QkaUser | None) -> str:
    if user is None:
        return ""
    contact = (user.contact_email or "").strip()
    if contact:
        return contact
    ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.qka_user_id == user.id,
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.link_status == LINKED,
        )
    ).scalar_one_or_none()
    if ad is not None:
        return (ad.primary_email or ad.upn or "").strip()
    return ""


def device_dto(session: Session, device: DesktopDevice) -> dict:
    user = session.get(QkaUser, device.qka_user_id) if device.qka_user_id else None
    folders = session.execute(
        select(DesktopDeviceFolder).where(DesktopDeviceFolder.device_id == device.id)
    ).scalars().all()
    try:
        by_type = json.loads(device.files_by_type_json or "{}")
    except json.JSONDecodeError:
        by_type = {}
    total = max(0, int(device.files_total))
    indexed = max(0, int(device.files_indexed))
    pct = int(round((indexed / total) * 100)) if total else 0
    assigned = device.qka_user_id is not None
    online = _online(device) and device.status != DEVICE_REVOKED
    browse_ok = bool(online)
    browse_reason = ""
    if not online:
        browse_reason = "Agent offline or not installed — folder browsing requires a connected Desktop Agent."
    indexing_label = _index_label(device.indexing_state)
    hostname = _workstation_hostname(session, device)
    return {
        "id": str(device.id),
        "qkaUserId": str(device.qka_user_id) if device.qka_user_id else None,
        "employeeAssigned": assigned,
        "employeeName": user.display_name if user else "Unassigned",
        "employeeEmail": _employee_email(session, user),
        "computerName": hostname,
        "workstationHostname": hostname,
        "domainJoinedName": device.domain_joined_name,
        "workstationId": str(device.workstation_id) if device.workstation_id else None,
        "deviceFingerprint": device.device_fingerprint,
        "agentVersion": device.agent_version,
        **_upgrade_fields(session, device),
        "status": _status_label(device.status),
        "online": online,
        "agentStatus": "connected" if online else "offline",
        "indexingState": indexing_label,
        "indexingEnabled": bool(folders and device.indexing_state == INDEX_RUNNING),
        "sourceAccessRestricted": not assigned,
        "readOnlyVerified": bool(device.read_only_verified),
        "readOnlyDetail": device.read_only_detail,
        "lastError": device.last_error,
        "filesTotal": total,
        "filesIndexed": indexed,
        "filesPending": int(device.files_pending),
        "filesFailed": int(device.files_failed),
        "filesByType": by_type,
        "percentComplete": pct,
        "lastHeartbeatAt": device.last_heartbeat_at.isoformat() if device.last_heartbeat_at else None,
        "lastIndexAt": device.last_index_at.isoformat() if device.last_index_at else None,
        "enrolledAt": device.enrolled_at.isoformat() if device.enrolled_at else None,
        "revokedAt": device.revoked_at.isoformat() if device.revoked_at else None,
        "folders": [
            {
                "id": str(f.id),
                "path": f.path_normalized,
                "includeSubfolders": f.include_subfolders,
                "enabled": f.enabled,
            }
            for f in folders
        ],
        "employeeAdSid": _employee_ad_sid(session, device.qka_user_id),
        "folderBrowseAvailable": browse_ok,
        "folderBrowseBlockedReason": browse_reason,
        "inventorySummary": _inventory_summary(session, device),
        "gpoDeployment": {
            "supported": True,
            "status": "installer_ready",
            "note": (
                "MSI is prepared for silent msiexec /qn and AD GPO Software Installation. "
                "Remote GPO push from this UI is a separate integration — not claimed here."
            ),
        },
    }


def _upgrade_fields(session: Session, device: DesktopDevice) -> dict:
    empty = {
        "availableAgentVersion": "",
        "upgradeAvailable": False,
        "upgradeJob": None,
    }
    try:
        from qka_api.services import agent_upgrade as upg_svc
        from qka_shared.config import get_settings

        settings = get_settings()
        avail = upg_svc.available_version(settings)
        # Nested savepoint: a missing upgrade table must not abort the outer request txn.
        with session.begin_nested():
            job = upg_svc.latest_job_for_device(session, device.id)
        return {
            "availableAgentVersion": avail,
            "upgradeAvailable": upg_svc.version_less(device.agent_version or "", avail),
            "upgradeJob": upg_svc.job_dto(job) if job is not None else None,
        }
    except Exception:
        return empty


def _inventory_summary(session: Session, device: DesktopDevice) -> dict | None:
    try:
        from qka_api.services import desktop_inventory as inv_svc

        with session.begin_nested():
            inv = inv_svc.inventory_dto(session, device)
        if inv is None:
            return None
        return {
            "hostname": inv.get("hostname") or "",
            "osEdition": inv.get("osEdition") or "",
            "ramBytes": inv.get("ramBytes") or 0,
            "volumesSyncedAt": inv.get("volumesSyncedAt"),
            "hardwareSyncedAt": inv.get("hardwareSyncedAt"),
            "hardwareStale": bool(inv.get("hardwareStale")),
            "volumesStale": bool(inv.get("volumesStale")),
            "volumeCount": len(inv.get("volumes") or []),
        }
    except Exception:
        return None
