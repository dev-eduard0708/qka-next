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

from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, load_active_user
from qka_shared.config import Settings
from qka_shared.models import (
    DEVICE_ACTIVE,
    DEVICE_ERROR,
    DEVICE_PAUSED,
    DEVICE_PENDING,
    DEVICE_REVOKED,
    INDEX_ERROR,
    INDEX_IDLE,
    INDEX_PAUSED,
    INDEX_RUNNING,
    DesktopDevice,
    DesktopDeviceFolder,
    DesktopEnrollmentCode,
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
    user = load_active_user(session, user_id)
    if user is None:
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


def create_enrollment_code(
    session: Session,
    settings: Settings,
    *,
    actor_id: uuid.UUID,
    qka_user_id: uuid.UUID,
    label: str = "",
) -> dict:
    require_ad_linked_user(session, qka_user_id)
    hours = max(1, int(settings.agent_enrollment_hours or 24))
    plain = secrets.token_urlsafe(24)
    row = DesktopEnrollmentCode(
        id=uuid.uuid4(),
        qka_user_id=qka_user_id,
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
    return {
        "id": str(row.id),
        "enrollmentCode": plain,
        "expiresAt": row.expires_at.isoformat(),
        "qkaUserId": str(qka_user_id),
        "serverBaseUrl": base,
        "installHint": (
            "Install QKA-Agent.msi silently, then set server URL + enrollment code "
            "via registry or first-run config. Do not embed the code in the MSI."
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
    computer_name: str,
    device_fingerprint: str,
    domain_joined_name: str = "",
    agent_version: str = "",
) -> dict:
    """Enroll via machine-bound token (managed deploy) or one-time enrollment code (manual fallback).

    Hostname/MAC/IP/email alone are never accepted as proof of device identity for
    the managed path — AD computer SID (and GUID when present) must match inventory.
    """
    from qka_api.services import agent_deployment as deploy_svc

    code = (enrollment_code or "").strip()
    machine_token = (machine_enrollment_token or "").strip()
    sid = (ad_computer_sid or "").strip()
    guid = (ad_computer_guid or "").strip()
    fp = (device_fingerprint or "").strip()
    if not fp:
        raise ValueError("Device fingerprint is required.")

    qka_user_id: uuid.UUID
    workstation_id: uuid.UUID | None = None
    machine_row = None
    code_row = None

    if machine_token:
        if not sid:
            raise ValueError("AD computer SID is required for machine-identity enrollment.")
        machine_row, ws = deploy_svc.consume_machine_enrollment_token(
            session,
            token=machine_token,
            ad_computer_sid=sid,
            ad_computer_guid=guid,
        )
        if not ws.assigned_qka_user_id or ws.assigned_qka_user_id != machine_row.qka_user_id:
            raise PermissionError("Workstation employee assignment does not match enrollment token.")
        require_ad_linked_user(session, machine_row.qka_user_id)
        qka_user_id = machine_row.qka_user_id
        workstation_id = ws.id
    elif code:
        # Manual fallback — still requires Super Admin–issued one-time code.
        code_row = session.execute(
            select(DesktopEnrollmentCode).where(DesktopEnrollmentCode.code_hash == _hash_secret(code))
        ).scalar_one_or_none()
        if code_row is None:
            raise PermissionError("Enrollment code is invalid.")
        if code_row.consumed_at is not None:
            raise PermissionError("Enrollment code was already used.")
        if code_row.expires_at < _now():
            raise PermissionError("Enrollment code has expired.")
        require_ad_linked_user(session, code_row.qka_user_id)
        qka_user_id = code_row.qka_user_id
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
        if existing.qka_user_id != qka_user_id:
            raise PermissionError("This computer is already enrolled to another employee.")
        existing.device_key_hash = _hash_secret(device_key)
        existing.computer_name = (computer_name or "").strip()[:256]
        existing.domain_joined_name = (domain_joined_name or "").strip()[:256]
        existing.agent_version = (agent_version or "").strip()[:64]
        existing.ad_computer_sid = sid[:256] if sid else existing.ad_computer_sid
        existing.ad_computer_guid = guid[:64] if guid else existing.ad_computer_guid
        if workstation_id:
            existing.workstation_id = workstation_id
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
            from qka_shared.models import AdWorkstation

            ws_row = session.get(AdWorkstation, workstation_id)
            if ws_row is not None:
                ws_row.desktop_device_id = device.id
    if code_row is not None:
        code_row.consumed_at = now
    session.flush()
    return {
        "deviceId": str(device.id),
        "deviceKey": device_key,
        "qkaUserId": str(device.qka_user_id),
        "serverBaseUrl": settings.agent_public_base,
        "supportedExtensions": list(AGENT_SUPPORTED_EXTS),
        "defaultExcludes": list(DEFAULT_EXCLUDES),
        "contentUploadEnabled": False,
        "enrollmentMode": "machine_identity" if machine_row is not None else "enrollment_code",
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
    return {"ok": True, "serverTime": _now().isoformat(), "status": _status_label(device.status)}


def get_policy(session: Session, device: DesktopDevice) -> dict:
    folders = session.execute(
        select(DesktopDeviceFolder).where(
            DesktopDeviceFolder.device_id == device.id,
            DesktopDeviceFolder.enabled.is_(True),
        )
    ).scalars().all()
    command = "run"
    if device.status == DEVICE_PAUSED or device.indexing_state == INDEX_PAUSED:
        command = "pause"
    elif device.status == DEVICE_REVOKED:
        command = "stop"
    return {
        "deviceId": str(device.id),
        "command": command,
        "approvedFolders": [
            {
                "id": str(f.id),
                "path": f.path_normalized,
                "includeSubfolders": bool(f.include_subfolders),
            }
            for f in folders
        ],
        "supportedExtensions": list(AGENT_SUPPORTED_EXTS),
        "defaultExcludes": list(DEFAULT_EXCLUDES),
        "contentUploadEnabled": False,
        "throttleWhenBusy": True,
        "localIndexOnly": True,
        "requireReadOnlyVerified": True,
    }


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


def device_dto(session: Session, device: DesktopDevice) -> dict:
    user = session.get(QkaUser, device.qka_user_id)
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
    return {
        "id": str(device.id),
        "qkaUserId": str(device.qka_user_id),
        "employeeName": user.display_name if user else "",
        "computerName": device.computer_name,
        "domainJoinedName": device.domain_joined_name,
        "deviceFingerprint": device.device_fingerprint,
        "agentVersion": device.agent_version,
        "status": _status_label(device.status),
        "online": _online(device),
        "agentStatus": "connected" if _online(device) and device.status != DEVICE_REVOKED else "offline",
        "indexingState": _index_label(device.indexing_state),
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
        "gpoDeployment": {
            "supported": True,
            "status": "installer_ready",
            "note": (
                "MSI is prepared for silent msiexec /qn and AD GPO Software Installation. "
                "Remote GPO push from this UI is a separate integration — not claimed here."
            ),
        },
    }
