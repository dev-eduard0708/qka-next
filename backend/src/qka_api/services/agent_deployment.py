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
    https_ok = (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and "localhost" not in parsed.netloc.lower()
        and "127.0.0.1" not in parsed.netloc
    )
    msi_unc = (getattr(settings, "agent_msi_unc", None) or "").strip()
    gpo = gpo_engine.probe_gpo_engine(msi_unc=msi_unc)
    # Secure mode: one-time token delivered by Provision-MachineEnrollmentToken.ps1 → HKLM
    # (never MSI / GPO Preferences / SYSVOL). HMAC proof ≠ Kerberos.
    provisioning_mode = (getattr(settings, "agent_token_provisioning", None) or "").strip().lower()
    hkml_mode = provisioning_mode in {"hkml_out_of_band", "hkml", "registry_out_of_band"}
    machine_proof_ready = bool(hkml_mode and gpo.msi_unc_reachable and gpo.ad_gpo_linked)
    machine_proof = {
        "hmacImplemented": True,
        "tokenEmbeddedInMsi": False,
        "kerberosMachineAuth": False,
        "securePerComputerProvisioning": machine_proof_ready,
        "provisioningMode": provisioning_mode or "unset",
        "provisioningScript": "scripts/gpo-agent-deploy/Provision-MachineEnrollmentToken.ps1",
        "replayProtection": "one_time_token_consume",
        "revocation": "desktop_device_revoke_rotates_device_key",
        "status": "ready" if machine_proof_ready else "blocked",
        "detail": (
            "machineProof=HMAC-SHA256(token, computerSid). Deliver the one-time token only via "
            "Provision-MachineEnrollmentToken.ps1 to HKLM on the selected PC. Never put tokens in "
            "MSI, GPO Preferences, or SYSVOL. Not Kerberos machine authentication."
        ),
    }
    blockers: list[str] = []
    if not https_ok:
        blockers.append(
            "Internal HTTPS agent endpoint is not configured "
            "(set QKA_AGENT_PUBLIC_BASE_URL only after https://qka-agent.qehc.edu.sa validates with a trusted cert)."
        )
    if not gpo.msi_present:
        blockers.append(f"MSI missing at {gpo.msi_path}.")
    if not gpo.available:
        # Surface hard GPO/UNC/membership blockers only (skip success text / NOTE: lines).
        for part in (gpo.reason or "").split(". "):
            text = part.strip().rstrip(".")
            if not text or text.startswith("NOTE:"):
                continue
            line = text if text.endswith(".") else text + "."
            if line not in blockers:
                blockers.append(line)
    if not hkml_mode:
        blockers.append(
            "Set QKA_AGENT_TOKEN_PROVISIONING=hkml_out_of_band and use "
            "Provision-MachineEnrollmentToken.ps1 for per-PC HKLM delivery (no SYSVOL secrets)."
        )
    elif not machine_proof_ready:
        blockers.append(
            "Secure per-computer token provisioning is not ready "
            "(need reachable MSI UNC + linked pilot GPO + hkml_out_of_band mode)."
        )
    # tlsTrust: server-side HTTPS health already validated in ops; clients still need CER trust.
    tls_trust = "server_ok_pilot_cer_required" if https_ok else "blocked"
    deploy_enabled = bool(https_ok and gpo.available and machine_proof_ready and not blockers)
    return {
        "deployEnabled": deploy_enabled,
        "httpsConfigured": https_ok,
        "httpsEndpoint": base if https_ok else "",
        "httpsPreferredHost": "qka-agent.qehc.edu.sa",
        "tlsTrust": tls_trust,
        "msiPresent": gpo.msi_present,
        "msiPath": gpo.msi_path,
        "msiUnc": gpo.msi_unc,
        "msiUncReachable": gpo.msi_unc_reachable,
        "gpoEngineAvailable": gpo.available,
        "gpoReason": gpo.reason,
        "gpoTargetGroup": gpo.target_group,
        "gpoAd": {
            "groupExists": gpo.ad_group_exists,
            "groupMemberCount": gpo.ad_group_member_count,
            "gpoExists": gpo.ad_gpo_exists,
            "gpoDisplayName": gpo.ad_gpo_display_name,
            "gpoGuid": gpo.ad_gpo_guid,
            "gpoLinked": gpo.ad_gpo_linked,
            "gpoLinkTargets": list(gpo.ad_gpo_link_targets),
            "gpmcPresent": gpo.gpmc_present,
        },
        "machineIdentityEnrollmentReady": machine_proof_ready,
        "machineProofSecurity": machine_proof,
        "manualEnrollmentCodeFallback": True,
        "blockers": blockers,
        "confirmationPhrase": CONFIRM_PHRASE,
        "maxBulk": 50,
        "realPcDeployAllowed": deploy_enabled,
        "note": (
            "Deploy Agent enables only for selected computers after HTTPS, MSI UNC, linked "
            "pilot-scoped GPO, pilot group membership, and hkml_out_of_band token mode are ready. "
            "Confirmation phrase required. Install success = agent registration + heartbeat."
        ),
    }


def job_dto(job: AgentDeploymentJob, ws: AdWorkstation | None = None) -> dict:
    return {
        "id": str(job.id),
        "workstationId": str(job.workstation_id),
        "computerName": ws.computer_name if ws else "",
        "assignedQkaUserId": str(job.assigned_qka_user_id) if job.assigned_qka_user_id else None,
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

    gpo = gpo_engine.probe_gpo_engine(msi_unc=(getattr(settings, "agent_msi_unc", None) or "").strip())
    jobs: list[dict] = []
    tokens_issued = 0
    computer_names: list[str] = []
    for wid in ids:
        ws = session.get(AdWorkstation, wid)
        if ws is None or not ws.is_present:
            raise LookupError(f"Workstation {wid} was not found or is not present.")
        # Employee assignment is optional at deploy/install time (assign after registration).
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
            assigned_qka_user_id=ws.assigned_qka_user_id,  # may be null
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
                qka_user_id=ws.assigned_qka_user_id,  # optional until post-install assign
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
        msi_unc=gpo.msi_unc,
    )

    return {
        "ok": True,
        "deployEnabled": True,
        "prerequisites": prereq,
        "jobs": jobs,
        "tokensIssued": tokens_issued,
        "gpoManifest": manifest,
        "tokenProvisioning": {
            "mode": "hkml_out_of_band",
            "script": "scripts/gpo-agent-deploy/Provision-MachineEnrollmentToken.ps1",
            "instruction": (
                "For each job, run Provision-MachineEnrollmentToken.ps1 -Token <machineEnrollmentToken> "
                "-IConfirmProvisionToThisComputer on that pilot PC (or Invoke-Command with your admin rights). "
                "Do not paste tokens into GPO/SYSVOL/MSI. Do not log the token."
            ),
        },
        "realPcDeployExecuted": False,
        "note": (
            "Jobs scheduled for selected computers only. GPO installs MSI from UNC; "
            "provision the one-time HKLM token out-of-band; agent registration/heartbeat proves install. "
            "QKA does not execute unrestricted remote PowerShell or use Domain Admin credentials."
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
