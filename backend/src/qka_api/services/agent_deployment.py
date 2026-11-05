"""Workstation agent deployment jobs + readiness gates (HTTPS / GPO / enrollment)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.services import gpo_engine
from qka_shared.config import Settings
from qka_shared.models import (
    DEPLOY_APPROVED,
    DEPLOY_CANCELLED,
    DEPLOY_FAILED,
    DEPLOY_INSTALLING,
    DEPLOY_INSTALLED_REGISTERED,
    DEPLOY_REQUESTED,
    DEPLOY_SCHEDULED,
    DEPLOY_WAITING_COMPUTER,
    AdWorkstation,
    AgentDeploymentJob,
    DesktopDevice,
    MachineEnrollmentToken,
    QkaUser,
)

CONFIRM_PHRASE = "DEPLOY SELECTED"

_ACTIVE_JOB_STATUSES = {
    DEPLOY_REQUESTED,
    DEPLOY_APPROVED,
    DEPLOY_SCHEDULED,
    DEPLOY_WAITING_COMPUTER,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def machine_proof_digest(*, token: str, computer_sid: str, computer_guid: str = "") -> str:
    """HMAC-SHA256(token, sid|guid) — possession of token + claimed machine SID."""
    msg = f"{(computer_sid or '').strip()}|{(computer_guid or '').strip()}".encode("utf-8")
    return hmac.new((token or "").encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _status_label(status: int) -> str:
    return {
        DEPLOY_REQUESTED: "requested",
        DEPLOY_APPROVED: "approved",
        DEPLOY_SCHEDULED: "scheduled",
        DEPLOY_WAITING_COMPUTER: "waiting_for_computer",
        DEPLOY_INSTALLING: "installing",
        DEPLOY_INSTALLED_REGISTERED: "installed_agent_registered",
        DEPLOY_FAILED: "failed",
        DEPLOY_CANCELLED: "cancelled",
    }.get(status, "unknown")


def deployment_prerequisites(settings: Settings) -> dict:
    base = (settings.agent_public_base_url or "").strip()
    parsed = urlparse(base)
    https_ok = parsed.scheme == "https" and bool(parsed.netloc) and "localhost" not in parsed.netloc.lower() and "127.0.0.1" not in parsed.netloc
    gpo = gpo_engine.probe_gpo_engine()
    blockers: list[str] = []
    if not https_ok:
        blockers.append(
            "Internal HTTPS agent endpoint is not configured "
            "(set QKA_AGENT_PUBLIC_BASE_URL to a trusted https:// hostname — not localhost)."
        )
    if not gpo.msi_present:
        blockers.append(f"MSI missing at {gpo.msi_path}.")
    if not gpo.available:
        blockers.append(gpo.reason)
    # TLS trust cannot be asserted from this process without an org cert + probe target.
    tls_trust = "unknown"
    if not https_ok:
        tls_trust = "blocked"
    return {
        "deployEnabled": False if blockers else bool(https_ok and gpo.available),
        "httpsConfigured": https_ok,
        "httpsEndpoint": base if https_ok else "",
        "tlsTrust": tls_trust,
        "msiPresent": gpo.msi_present,
        "msiPath": gpo.msi_path,
        "gpoEngineAvailable": gpo.available,
        "gpoReason": gpo.reason,
        "gpoTargetGroup": gpo.target_group,
        "machineIdentityEnrollmentReady": True,
        "manualEnrollmentCodeFallback": True,
        "blockers": blockers,
        "confirmationPhrase": CONFIRM_PHRASE,
        "maxBulk": 50,
        "realPcDeployAllowed": False,
        "note": (
            "Deploy Agent stays disabled until HTTPS and the delegated GPO engine are ready. "
            "Ping/GPO assignment alone never counts as installation success."
        ),
    }


def job_dto(job: AgentDeploymentJob, ws: AdWorkstation | None = None) -> dict:
    return {
        "id": str(job.id),
        "workstationId": str(job.workstation_id),
        "computerName": ws.computer_name if ws else "",
        "assignedQkaUserId": str(job.assigned_qka_user_id),
        "status": _status_label(job.status),
        "statusCode": job.status,
        "attemptCount": job.attempt_count,
        "lastError": job.last_error or "",
        "msiVersion": job.msi_version,
        "engine": job.engine,
        "enrollmentMode": job.enrollment_mode,
        "gpoTargetGroup": job.gpo_target_group,
        "desktopDeviceId": str(job.desktop_device_id) if job.desktop_device_id else None,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "updatedAt": job.updated_at.isoformat() if job.updated_at else None,
        "approvedAt": job.approved_at.isoformat() if job.approved_at else None,
        "scheduledAt": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "installedAt": job.installed_at.isoformat() if job.installed_at else None,
        "cancelledAt": job.cancelled_at.isoformat() if job.cancelled_at else None,
    }


def latest_job_for_workstation(session: Session, workstation_id: uuid.UUID) -> AgentDeploymentJob | None:
    return session.execute(
        select(AgentDeploymentJob)
        .where(AgentDeploymentJob.workstation_id == workstation_id)
        .order_by(AgentDeploymentJob.created_at.desc())
    ).scalars().first()


def request_deployments(
    session: Session,
    settings: Settings,
    *,
    actor: QkaUser,
    workstation_ids: list[uuid.UUID],
    confirmation: str,
    approve: bool = True,
) -> dict:
    prereq = deployment_prerequisites(settings)
    if (confirmation or "").strip() != CONFIRM_PHRASE:
        raise ValueError(f'Confirmation phrase must be exactly "{CONFIRM_PHRASE}".')
    if not approve:
        raise ValueError("Super Admin approval is required for agent deployment.")
    if not prereq["deployEnabled"]:
        blockers = "; ".join(prereq["blockers"]) or "Deployment prerequisites are not met."
        raise ValueError(f"Deployment blocked: {blockers}")
    ids = list(dict.fromkeys(workstation_ids))
    if not ids:
        raise ValueError("Select at least one workstation.")
    if len(ids) > int(prereq["maxBulk"]):
        raise ValueError(f"Select at most {prereq['maxBulk']} workstations per batch.")

    gpo = gpo_engine.probe_gpo_engine()
    jobs: list[dict] = []
    tokens_issued = 0
    computer_names: list[str] = []
    for wid in ids:
        ws = session.get(AdWorkstation, wid)
        if ws is None or not ws.is_present:
            raise LookupError(f"Workstation {wid} was not found or is not present.")
        if not ws.assigned_qka_user_id:
            raise ValueError(f"{ws.computer_name}: assign an AD-linked employee before deploy.")
        if not (ws.object_sid or "").strip():
            raise ValueError(f"{ws.computer_name}: AD objectSid is required for machine-identity enrollment.")

        open_job = session.execute(
            select(AgentDeploymentJob).where(
                AgentDeploymentJob.workstation_id == wid,
                AgentDeploymentJob.status.in_(list(_ACTIVE_JOB_STATUSES)),
            )
        ).scalars().first()
        if open_job is not None:
            raise ValueError(f"{ws.computer_name}: an active deployment job already exists ({open_job.id}).")

        now = _now()
        job = AgentDeploymentJob(
            id=uuid.uuid4(),
            workstation_id=ws.id,
            assigned_qka_user_id=ws.assigned_qka_user_id,
            requested_by_user_id=actor.id,
            approved_by_user_id=actor.id,
            status=DEPLOY_SCHEDULED,
            attempt_count=1,
            last_error="",
            msi_version=settings.agent_min_version or "0.1.0",
            engine="gpo",
            enrollment_mode="machine_identity",
            gpo_target_group=gpo.target_group,
            confirmation_phrase=CONFIRM_PHRASE,
            created_at=now,
            updated_at=now,
            approved_at=now,
            scheduled_at=now,
        )
        session.add(job)
        session.flush()

        plain_token = secrets.token_urlsafe(32)
        sid = (ws.object_sid or "").strip()
        guid = (ws.object_guid or "").strip()
        # Proof binds token ↔ local computer SID (GUID matched separately when presented).
        proof = machine_proof_digest(token=plain_token, computer_sid=sid, computer_guid="")
        session.add(
            MachineEnrollmentToken(
                id=uuid.uuid4(),
                job_id=job.id,
                workstation_id=ws.id,
                qka_user_id=ws.assigned_qka_user_id,
                ad_computer_sid=sid,
                ad_computer_guid=guid,
                token_hash=_hash(plain_token),
                proof_hash=_hash(proof),
                expires_at=now + timedelta(hours=max(1, int(settings.agent_enrollment_hours or 24))),
                created_at=now,
            )
        )
        tokens_issued += 1
        session.flush()
        dto = job_dto(job, ws)
        # Plaintext token only at creation for delegated GPO/registry delivery — never in MSI.
        dto["machineEnrollmentToken"] = plain_token
        computer_names.append(ws.computer_name)
        jobs.append(dto)

    manifest = gpo_engine.build_gpo_package_manifest(
        computer_names=computer_names,
        server_base_url=prereq["httpsEndpoint"],
        msi_path=gpo.msi_path,
        target_group=gpo.target_group,
    )

    return {
        "ok": True,
        "deployEnabled": True,
        "prerequisites": prereq,
        "jobs": jobs,
        "tokensIssued": tokens_issued,
        "gpoManifest": manifest,
        "realPcDeployExecuted": False,
        "note": (
            "Jobs scheduled for selected computers only. Domain Ops applies the dedicated "
            "GPO to the approved computer group; agent registration/heartbeat proves install. "
            "QKA does not execute unrestricted remote PowerShell."
        ),
    }


def cancel_job(session: Session, *, actor: QkaUser, job_id: uuid.UUID) -> dict:
    job = session.get(AgentDeploymentJob, job_id)
    if job is None:
        raise LookupError("Deployment job was not found.")
    if job.status in {DEPLOY_INSTALLED_REGISTERED, DEPLOY_CANCELLED}:
        raise ValueError("That job can no longer be cancelled.")
    job.status = DEPLOY_CANCELLED
    job.cancelled_at = _now()
    job.updated_at = _now()
    job.last_error = f"Cancelled by {actor.id}"
    session.flush()
    ws = session.get(AdWorkstation, job.workstation_id)
    return job_dto(job, ws)


def retry_job(session: Session, settings: Settings, *, actor: QkaUser, job_id: uuid.UUID) -> dict:
    job = session.get(AgentDeploymentJob, job_id)
    if job is None:
        raise LookupError("Deployment job was not found.")
    if job.status not in {DEPLOY_FAILED, DEPLOY_WAITING_COMPUTER, DEPLOY_CANCELLED}:
        raise ValueError("Only failed, waiting, or cancelled jobs can be retried.")
    prereq = deployment_prerequisites(settings)
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.updated_at = _now()
    job.cancelled_at = None
    if prereq["deployEnabled"]:
        job.status = DEPLOY_SCHEDULED
        job.scheduled_at = _now()
        job.last_error = ""
        job.engine = "gpo"
    else:
        job.status = DEPLOY_WAITING_COMPUTER
        job.last_error = "; ".join(prereq["blockers"])[:2000]
        job.engine = "blocked"
    session.flush()
    ws = session.get(AdWorkstation, job.workstation_id)
    return job_dto(job, ws)


def mark_job_registered(session: Session, *, workstation_id: uuid.UUID, device: DesktopDevice) -> None:
    """When agent enrolls with machine identity, promote open job to registered."""
    job = session.execute(
        select(AgentDeploymentJob)
        .where(
            AgentDeploymentJob.workstation_id == workstation_id,
            AgentDeploymentJob.status.in_(
                [
                    DEPLOY_APPROVED,
                    DEPLOY_SCHEDULED,
                    DEPLOY_WAITING_COMPUTER,
                    DEPLOY_INSTALLING,
                ]
            ),
        )
        .order_by(AgentDeploymentJob.created_at.desc())
    ).scalars().first()
    if job is None:
        return
    job.status = DEPLOY_INSTALLED_REGISTERED
    job.desktop_device_id = device.id
    job.installed_at = _now()
    job.updated_at = _now()
    job.last_error = ""
    session.flush()


def consume_machine_enrollment_token(
    session: Session,
    *,
    token: str,
    ad_computer_sid: str,
    ad_computer_guid: str = "",
    machine_proof: str = "",
) -> tuple[MachineEnrollmentToken, AdWorkstation]:
    sid = (ad_computer_sid or "").strip()
    if not sid:
        raise ValueError("AD computer SID is required for machine-identity enrollment.")
    proof = (machine_proof or "").strip().lower()
    if not proof:
        raise PermissionError(
            "Machine enrollment proof is required. "
            "SID/GUID claims alone are not accepted as device identity."
        )
    row = session.execute(
        select(MachineEnrollmentToken).where(MachineEnrollmentToken.token_hash == _hash(token.strip()))
    ).scalar_one_or_none()
    if row is None:
        raise PermissionError("Machine enrollment token is invalid.")
    if row.consumed_at is not None:
        raise PermissionError("Machine enrollment token was already used.")
    if row.expires_at < _now():
        raise PermissionError("Machine enrollment token has expired.")
    if row.ad_computer_sid.strip() != sid:
        raise PermissionError("Machine SID does not match the enrollment token.")
    guid = (ad_computer_guid or "").strip()
    if row.ad_computer_guid and guid and row.ad_computer_guid.lower() != guid.lower():
        raise PermissionError("Machine GUID does not match the enrollment token.")
    expected = machine_proof_digest(token=token.strip(), computer_sid=sid, computer_guid="")
    if not hmac.compare_digest(proof, expected):
        raise PermissionError("Machine enrollment proof is invalid.")
    if row.proof_hash and not hmac.compare_digest(_hash(proof), row.proof_hash):
        raise PermissionError("Machine enrollment proof is invalid.")
    ws = session.get(AdWorkstation, row.workstation_id)
    if ws is None:
        raise LookupError("Workstation inventory row was not found.")
    if (ws.object_sid or "").strip() != sid:
        raise PermissionError("Workstation inventory SID mismatch.")
    return row, ws
