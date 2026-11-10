"""Explicit per-device Desktop Agent upgrades (preserve identity/index/ownership)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.services import token_drop
from qka_shared.config import Settings
from qka_shared.models import (
    UPGRADE_CANCELLED,
    UPGRADE_COMPLETED,
    UPGRADE_FAILED,
    UPGRADE_SCHEDULED,
    UPGRADE_UPGRADING,
    UPGRADE_VERIFYING,
    AdWorkstation,
    DesktopAgentUpgradeJob,
    DesktopDevice,
    DEVICE_REVOKED,
)

_ACTIVE = {UPGRADE_SCHEDULED, UPGRADE_UPGRADING, UPGRADE_VERIFYING}
# GPO task may authorize only while MSI work is still pending — not during heartbeat verify.
_AUTH_OK = {UPGRADE_SCHEDULED, UPGRADE_UPGRADING}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_secret(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _machine_proof_digest(token: str, sid: str) -> str:
    return hmac.new(
        (token or "").encode("utf-8"),
        f"{(sid or '').strip()}|".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _status_label(status: int) -> str:
    return {
        UPGRADE_SCHEDULED: "scheduled",
        UPGRADE_UPGRADING: "upgrading",
        UPGRADE_VERIFYING: "verifying",
        UPGRADE_COMPLETED: "completed",
        UPGRADE_FAILED: "failed",
        UPGRADE_CANCELLED: "cancelled",
    }.get(status, "unknown")


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in re_split(v or ""):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def re_split(v: str) -> list[str]:
    import re

    return [p for p in re.split(r"[^\d]+", (v or "").strip()) if p]


def version_less(a: str, b: str) -> bool:
    return _version_tuple(a) < _version_tuple(b)


def available_version(settings: Settings) -> str:
    return (settings.agent_available_version or settings.agent_min_version or "0.1.3").strip()


def job_dto(job: DesktopAgentUpgradeJob) -> dict:
    return {
        "id": str(job.id),
        "deviceId": str(job.device_id),
        "workstationId": str(job.workstation_id) if job.workstation_id else None,
        "status": _status_label(job.status),
        "fromVersion": job.from_version,
        "targetVersion": job.target_version,
        "msiVersion": job.msi_version,
        "lastError": job.last_error,
        "msiExitCode": job.msi_exit_code,
        "lastResult": job.last_result,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "scheduledAt": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "upgradingAt": job.upgrading_at.isoformat() if job.upgrading_at else None,
        "verifyingAt": job.verifying_at.isoformat() if job.verifying_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
        "failedAt": job.failed_at.isoformat() if job.failed_at else None,
        "expiresAt": job.expires_at.isoformat() if job.expires_at else None,
    }


def latest_job_for_device(session: Session, device_id: uuid.UUID) -> DesktopAgentUpgradeJob | None:
    return session.execute(
        select(DesktopAgentUpgradeJob)
        .where(DesktopAgentUpgradeJob.device_id == device_id)
        .order_by(DesktopAgentUpgradeJob.created_at.desc())
    ).scalars().first()


def schedule_upgrade(
    session: Session,
    settings: Settings,
    *,
    actor_id: uuid.UUID,
    device_id: uuid.UUID,
) -> dict:
    device = session.get(DesktopDevice, device_id)
    if device is None or device.status == DEVICE_REVOKED:
        raise LookupError("Device was not found.")
    target = available_version(settings)
    current = (device.agent_version or "").strip()
    if current and not version_less(current, target):
        raise ValueError(f"Device already at or above available version {target} (installed {current or 'unknown'}).")

    existing = session.execute(
        select(DesktopAgentUpgradeJob).where(
            DesktopAgentUpgradeJob.device_id == device_id,
            DesktopAgentUpgradeJob.status.in_(tuple(_ACTIVE)),
        )
    ).scalars().first()
    if existing is not None:
        raise ValueError("An upgrade is already in progress for this device.")

    sid = (device.ad_computer_sid or "").strip()
    if not sid and device.workstation_id:
        ws = session.get(AdWorkstation, device.workstation_id)
        if ws is not None:
            sid = (ws.object_sid or "").strip()
    if not sid:
        raise ValueError("Device has no AD computer SID for upgrade authorization drop.")

    drop_unc = (settings.agent_token_drop_unc or "").strip()
    if not drop_unc:
        raise ValueError("QKA_AGENT_TOKEN_DROP_UNC is not configured.")
    if not (settings.agent_msi_unc or "").strip():
        raise ValueError("QKA_AGENT_MSI_UNC is not configured.")

    now = _now()
    token = secrets.token_urlsafe(32)
    job = DesktopAgentUpgradeJob(
        id=uuid.uuid4(),
        device_id=device.id,
        workstation_id=device.workstation_id,
        requested_by_user_id=actor_id,
        status=UPGRADE_SCHEDULED,
        from_version=current,
        target_version=target,
        msi_version=target,
        token_hash=_hash_secret(token),
        proof_hash=_machine_proof_digest(token, sid),
        ad_computer_sid=sid,
        created_at=now,
        updated_at=now,
        scheduled_at=now,
        expires_at=now + timedelta(hours=6),
    )
    session.add(job)
    session.flush()
    try:
        staged = token_drop.stage_token_for_computer(
            drop_unc=drop_unc,
            computer_sid=sid,
            computer_name=device.computer_name or "device",
            token=token,
            suffix=".upg",
        )
    except Exception as exc:
        job.status = UPGRADE_FAILED
        job.last_error = f"Failed to stage upgrade token: {exc}"[:2000]
        job.failed_at = _now()
        job.updated_at = job.failed_at
        session.flush()
        raise RuntimeError(job.last_error) from exc

    job.last_result = f"upgrade_token_staged path_suffix=.upg"
    job.updated_at = _now()
    session.flush()
    dto = job_dto(job)
    dto["stagedPathHint"] = staged
    dto["note"] = (
        "Upgrade authorized for this computer only. Existing GPO task will apply MSI when it next runs. "
        "Device identity, employee assignment, approved folders and local index are preserved."
    )
    return dto


def cancel_upgrade(session: Session, settings: Settings, *, job_id: uuid.UUID) -> dict:
    job = session.get(DesktopAgentUpgradeJob, job_id)
    if job is None:
        raise LookupError("Upgrade job was not found.")
    if job.status not in _ACTIVE:
        raise ValueError("Upgrade job is no longer active.")
    if job.ad_computer_sid and (settings.agent_token_drop_unc or "").strip():
        token_drop.remove_token_for_computer(
            drop_unc=settings.agent_token_drop_unc,
            computer_sid=job.ad_computer_sid,
            suffix=".upg",
        )
    now = _now()
    job.status = UPGRADE_CANCELLED
    job.cancelled_at = now
    job.updated_at = now
    job.last_result = "cancelled"
    session.flush()
    return job_dto(job)


def upgrade_authorization(
    session: Session,
    *,
    token: str,
    ad_computer_sid: str,
    machine_proof: str,
) -> dict:
    tok = (token or "").strip()
    sid = (ad_computer_sid or "").strip()
    proof = (machine_proof or "").strip().lower()
    if not tok or not sid or not proof:
        return {"authorized": False, "reason": "missing_token_or_sid_or_proof"}
    expected = _machine_proof_digest(tok, sid)
    if not hmac.compare_digest(expected, proof):
        return {"authorized": False, "reason": "invalid_machine_proof"}
    th = _hash_secret(tok)
    job = session.execute(
        select(DesktopAgentUpgradeJob).where(
            DesktopAgentUpgradeJob.token_hash == th,
            DesktopAgentUpgradeJob.ad_computer_sid == sid,
            DesktopAgentUpgradeJob.status.in_(tuple(_AUTH_OK)),
        )
    ).scalars().first()
    if job is None:
        # Distinct reason when MSI finished and we are waiting on heartbeat version.
        verifying = session.execute(
            select(DesktopAgentUpgradeJob).where(
                DesktopAgentUpgradeJob.token_hash == th,
                DesktopAgentUpgradeJob.ad_computer_sid == sid,
                DesktopAgentUpgradeJob.status == UPGRADE_VERIFYING,
            )
        ).scalars().first()
        if verifying is not None:
            return {"authorized": False, "reason": "already_verifying", "jobId": str(verifying.id)}
        return {"authorized": False, "reason": "no_active_upgrade_job"}
    if job.expires_at and job.expires_at < _now():
        job.status = UPGRADE_FAILED
        job.last_error = "Upgrade authorization expired."
        job.failed_at = _now()
        job.updated_at = job.failed_at
        session.flush()
        return {"authorized": False, "reason": "expired", "jobId": str(job.id)}
    return {
        "authorized": True,
        "reason": "ok",
        "jobId": str(job.id),
        "targetVersion": job.target_version,
        "deviceId": str(job.device_id),
    }


def report_upgrade_progress(
    session: Session,
    *,
    token: str,
    ad_computer_sid: str,
    machine_proof: str,
    phase: str,
    msi_exit_code: int | None = None,
    detail: str = "",
) -> dict:
    auth = upgrade_authorization(
        session,
        token=token,
        ad_computer_sid=ad_computer_sid,
        machine_proof=machine_proof,
    )
    if not auth.get("authorized"):
        return {"ok": False, "reason": auth.get("reason") or "denied"}
    job = session.get(DesktopAgentUpgradeJob, uuid.UUID(auth["jobId"]))
    if job is None:
        return {"ok": False, "reason": "job_missing"}
    now = _now()
    phase_l = (phase or "").strip().lower()
    job.last_result = (detail or phase_l)[:256]
    if msi_exit_code is not None:
        job.msi_exit_code = int(msi_exit_code)
    if phase_l in {"started", "upgrading"}:
        job.status = UPGRADE_UPGRADING
        job.upgrading_at = job.upgrading_at or now
    elif phase_l in {"msi_finished", "verifying"}:
        if msi_exit_code not in (None, 0, 3010):
            job.status = UPGRADE_FAILED
            job.failed_at = now
            job.last_error = (detail or f"msiexec_exit={msi_exit_code}")[:2000]
        else:
            job.status = UPGRADE_VERIFYING
            job.verifying_at = now
            # Consume upgrade drop so the task does not re-run forever.
            # (Caller may also delete; best-effort here when settings available via env.)
    elif phase_l == "failed":
        job.status = UPGRADE_FAILED
        job.failed_at = now
        job.last_error = (detail or "upgrade_failed")[:2000]
    job.updated_at = now
    session.flush()
    return {"ok": True, "job": job_dto(job)}


def observe_heartbeat_for_upgrade(session: Session, device: DesktopDevice) -> None:
    """Mark verifying upgrades completed when heartbeat reports the target version."""
    job = session.execute(
        select(DesktopAgentUpgradeJob).where(
            DesktopAgentUpgradeJob.device_id == device.id,
            DesktopAgentUpgradeJob.status == UPGRADE_VERIFYING,
        )
    ).scalars().first()
    if job is None:
        return
    installed = (device.agent_version or "").strip()
    if installed and not version_less(installed, job.target_version):
        now = _now()
        job.status = UPGRADE_COMPLETED
        job.completed_at = now
        job.updated_at = now
        job.last_result = f"verified_version={installed}"
        session.flush()
