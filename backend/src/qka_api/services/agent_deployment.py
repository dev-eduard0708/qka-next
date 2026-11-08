"""Workstation agent deployment jobs + readiness gates (HTTPS / GPO / enrollment)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
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

_PREREQ_TTL_SEC = 45.0
_PREREQ_CACHE: dict[str, tuple[float, dict]] = {}
_PREREQ_LOCK = Lock()


def invalidate_deployment_readiness_cache() -> None:
    """Clear display-cache for readiness; deploy submit always uses force_refresh."""
    with _PREREQ_LOCK:
        _PREREQ_CACHE.clear()
    gpo_engine.invalidate_ad_readiness_caches()


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


def deployment_prerequisites(settings: Settings, *, force_refresh: bool = False) -> dict:
    """Readiness snapshot for UI. Cached briefly; pass force_refresh=True for deploy submit."""
    cache_key = "|".join(
        [
            (settings.agent_public_base_url or "").strip(),
            (getattr(settings, "agent_msi_unc", None) or "").strip(),
            (getattr(settings, "agent_token_drop_unc", None) or "").strip(),
            (getattr(settings, "agent_token_provisioning", None) or "").strip().lower(),
        ]
    )
    now = time.monotonic()
    if not force_refresh:
        with _PREREQ_LOCK:
            hit = _PREREQ_CACHE.get(cache_key)
            if hit and (now - hit[0]) < _PREREQ_TTL_SEC:
                cached = dict(hit[1])
                cached["cached"] = True
                return cached

    base = (settings.agent_public_base_url or "").strip()
    parsed = urlparse(base)
    https_ok = (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and "localhost" not in parsed.netloc.lower()
        and "127.0.0.1" not in parsed.netloc
    )
    msi_unc = (getattr(settings, "agent_msi_unc", None) or "").strip()
    drop_unc = (getattr(settings, "agent_token_drop_unc", None) or "").strip()
    gpo = gpo_engine.probe_gpo_engine(msi_unc=msi_unc, force_refresh=force_refresh)
    # Preferred: unc_acl_drop — server stages per-SID *.tok with computer-only ACL; agent claims+deletes.
    # Fallback: hkml_out_of_band — Provision-MachineEnrollmentToken.ps1 (no paste into GPO/SYSVOL).
    provisioning_mode = (getattr(settings, "agent_token_provisioning", None) or "").strip().lower()
    unc_drop_mode = provisioning_mode in {"unc_acl_drop", "token_drop", "unc_drop"}
    hkml_mode = provisioning_mode in {"hkml_out_of_band", "hkml", "registry_out_of_band"}
    drop_ok = False
    if unc_drop_mode and drop_unc:
        try:
            from pathlib import Path

            drop_ok = Path(drop_unc).is_dir()
        except Exception:
            drop_ok = False
    machine_proof_ready = bool(
        gpo.msi_unc_reachable
        and gpo.ad_gpo_linked
        and ((unc_drop_mode and drop_ok) or hkml_mode)
    )
    machine_proof = {
        "hmacImplemented": True,
        "tokenEmbeddedInMsi": False,
        "kerberosMachineAuth": False,
        "securePerComputerProvisioning": machine_proof_ready,
        "provisioningMode": provisioning_mode or "unset",
        "tokenDropUnc": drop_unc if unc_drop_mode else "",
        "provisioningScript": (
            "scripts/gpo-agent-deploy/New-QkaAgentTokenDropShare.ps1"
            if unc_drop_mode
            else "scripts/gpo-agent-deploy/Provision-MachineEnrollmentToken.ps1"
        ),
        "replayProtection": "one_time_token_consume",
        "revocation": "desktop_device_revoke_rotates_device_key",
        "status": "ready" if machine_proof_ready else "blocked",
        "detail": (
            "machineProof=HMAC-SHA256(token, computerSid). Preferred delivery: per-computer ACL "
            "token-drop UNC (agent claims+deletes). Never put tokens in MSI, GPO Preferences, "
            "SYSVOL, PowerShell history, or logs. Not Kerberos machine authentication."
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
        # Use structured hard_reasons — never sentence-split gpo.reason (that turned
        # "On a GPMC host run…" into a false GPMC-required blocker). Skip NOTE: lines.
        hard = list(getattr(gpo, "hard_reasons", ()) or ())
        if not hard:
            # Backward-compatible fallback for older probe mocks in tests.
            for part in (gpo.reason or "").split(". "):
                text = part.strip().rstrip(".")
                if not text or text.startswith("NOTE:"):
                    continue
                lower = text.lower()
                # Local GPMC is never a hard blocker on the API host.
                if lower.startswith("on a gpmc host"):
                    continue
                if "group policy management" in lower or "rsat gpmc" in lower or "gpmc" in lower:
                    continue
                hard.append(text if text.endswith(".") else text + ".")
        for line in hard:
            text = line.strip()
            if not text or text.startswith("NOTE:"):
                continue
            lower = text.lower()
            if "group policy management" in lower or "rsat gpmc" in lower:
                continue
            if not text.endswith("."):
                text += "."
            if text not in blockers:
                blockers.append(text)
    if not unc_drop_mode and not hkml_mode:
        blockers.append(
            "Set QKA_AGENT_TOKEN_PROVISIONING=unc_acl_drop (preferred) with QKA_AGENT_TOKEN_DROP_UNC, "
            "or hkml_out_of_band fallback. Never place tokens in SYSVOL/MSI."
        )
    elif unc_drop_mode and not drop_ok:
        blockers.append(
            "Token-drop UNC missing/unreachable — run New-QkaAgentTokenDropShare.ps1 and set "
            "QKA_AGENT_TOKEN_DROP_UNC."
        )
    elif not machine_proof_ready:
        blockers.append(
            "Secure per-computer token provisioning is not ready "
            "(need reachable MSI UNC + linked pilot GPO + unc_acl_drop or hkml mode)."
        )
    # tlsTrust: server-side HTTPS health already validated in ops; clients still need CER trust.
    tls_trust = "server_ok_pilot_cer_required" if https_ok else "blocked"
    deploy_enabled = bool(https_ok and gpo.available and machine_proof_ready and not blockers)
    payload = {
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
        "approvedPilotComputer": "WSIT02",
        "pilotAuthorization": (
            "Each Deploy target must be a computer member of QKA-Agent-Pilot-Computers "
            "(matched by AD objectSid). Name-only matching is not accepted."
        ),
        "machineIdentityEnrollmentReady": machine_proof_ready,
        "machineProofSecurity": machine_proof,
        "manualEnrollmentCodeFallback": True,
        "blockers": blockers,
        "confirmationPhrase": CONFIRM_PHRASE,
        "maxBulk": 50,
        "realPcDeployAllowed": deploy_enabled,
        "tokenDropUnc": drop_unc,
        "note": (
            "Deploy Agent enables only for selected computers that are members of "
            "QKA-Agent-Pilot-Computers (SID-verified), after HTTPS, MSI UNC, linked "
            "pilot-scoped GPO, and unc_acl_drop (or hkml fallback) are ready. "
            "Approved pilot computer: WSIT02. Confirmation phrase required. "
            "Install success = agent registration + heartbeat."
        ),
        "cached": False,
    }
    with _PREREQ_LOCK:
        _PREREQ_CACHE[cache_key] = (time.monotonic(), dict(payload))
    return payload


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
    # Fail-closed: always revalidate readiness + pilot SIDs at submit time (ignore display cache).
    prereq = deployment_prerequisites(settings, force_refresh=True)
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

    gpo = gpo_engine.probe_gpo_engine(
        msi_unc=(getattr(settings, "agent_msi_unc", None) or "").strip(),
        force_refresh=True,
    )
    # Authorize every target against live AD pilot-group membership (objectSid), before any job/token.
    pilot_sids = gpo_engine.list_pilot_group_member_sids(gpo.target_group, force_refresh=True)
    authorized: list[AdWorkstation] = []
    for wid in ids:
        ws = session.get(AdWorkstation, wid)
        if ws is None or not ws.is_present:
            raise LookupError(f"Workstation {wid} was not found or is not present.")
        sid = (ws.object_sid or "").strip()
        if not sid:
            raise ValueError(f"{ws.computer_name}: AD objectSid is required for machine-identity enrollment.")
        if not gpo_engine.computer_sid_in_pilot_group(
            sid, target_group=gpo.target_group, member_sids=pilot_sids
        ):
            raise ValueError(
                f"{ws.computer_name}: not authorized for pilot deployment — AD objectSid is not a "
                f"computer member of '{gpo.target_group}'. Add only the approved pilot computer "
                f"(WSIT02) via Add-QkaPilotComputer.ps1; do not deploy other workstations."
            )
        authorized.append(ws)

    jobs: list[dict] = []
    tokens_issued = 0
    computer_names: list[str] = []
    for ws in authorized:
        wid = ws.id
        # Employee assignment is optional at deploy/install time (assign after registration).

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
        # Prefer automatic UNC ACL drop — do not return plaintext token to the UI when staged.
        staged_path = ""
        provisioning_mode = (getattr(settings, "agent_token_provisioning", None) or "").strip().lower()
        drop_unc = (getattr(settings, "agent_token_drop_unc", None) or "").strip()
        if provisioning_mode in {"unc_acl_drop", "token_drop", "unc_drop"} and drop_unc:
            from qka_api.services import token_drop

            try:
                staged_path = token_drop.stage_token_for_computer(
                    drop_unc=drop_unc,
                    computer_sid=sid,
                    computer_name=ws.computer_name,
                    token=plain_token,
                )
                dto["tokenStaged"] = True
                dto["tokenDropPath"] = staged_path  # path only — not the secret
                dto["machineEnrollmentToken"] = None
            except Exception as exc:  # noqa: BLE001
                # Fail the job rather than falling back to exposing the token in the browser.
                job.status = DEPLOY_FAILED
                job.last_error = f"Token drop staging failed: {exc}"[:2000]
                job.updated_at = _now()
                dto = job_dto(job, ws)
                dto["tokenStaged"] = False
                dto["machineEnrollmentToken"] = None
                jobs.append(dto)
                computer_names.append(ws.computer_name)
                continue
        else:
            # Manual HKLM fallback — shown once in UI; never log.
            dto["tokenStaged"] = False
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

    mode = (getattr(settings, "agent_token_provisioning", None) or "").strip().lower()
    if mode in {"unc_acl_drop", "token_drop", "unc_drop"}:
        token_info = {
            "mode": "unc_acl_drop",
            "script": "scripts/gpo-agent-deploy/New-QkaAgentTokenDropShare.ps1",
            "instruction": (
                "One-time tokens were staged to the token-drop UNC with per-computer ACL. "
                "After GPO installs the MSI, the agent claims and deletes its own *.tok automatically. "
                "No employee paste. Do not copy tokens into GPO/SYSVOL/MSI/logs."
            ),
        }
    else:
        token_info = {
            "mode": "hkml_out_of_band",
            "script": "scripts/gpo-agent-deploy/Provision-MachineEnrollmentToken.ps1",
            "instruction": (
                "Fallback: run Provision-MachineEnrollmentToken.ps1 on the pilot PC with the one-time "
                "token (stdin/secure string preferred). Do not paste into GPO/SYSVOL/MSI or log history."
            ),
        }

    return {
        "ok": True,
        "deployEnabled": True,
        "prerequisites": prereq,
        "jobs": jobs,
        "tokensIssued": tokens_issued,
        "gpoManifest": manifest,
        "tokenProvisioning": token_info,
        "realPcDeployExecuted": False,
        "note": (
            "Jobs scheduled for selected computers only. GPO installs MSI from UNC; "
            "enrollment uses per-computer ACL token-drop (automatic) or HKLM fallback; "
            "agent registration/heartbeat proves install. "
            "QKA does not execute unrestricted remote PowerShell or use Domain Admin credentials."
        ),
    }


def cancel_job(session: Session, settings: Settings | None = None, *, actor: QkaUser, job_id: uuid.UUID) -> dict:
    from qka_shared.config import get_settings

    settings = settings or get_settings()
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
    # Remove staged token so GPO startup auth-gate will not install after cancel.
    drop_unc = (getattr(settings, "agent_token_drop_unc", None) or "").strip()
    sid = (ws.object_sid if ws else "") or ""
    if drop_unc and sid:
        try:
            from qka_api.services import token_drop

            token_drop.remove_token_for_computer(drop_unc=drop_unc, computer_sid=sid)
        except Exception:
            pass
    return job_dto(job, ws)


def retry_job(session: Session, settings: Settings, *, actor: QkaUser, job_id: uuid.UUID) -> dict:
    job = session.get(AgentDeploymentJob, job_id)
    if job is None:
        raise LookupError("Deployment job was not found.")
    if job.status not in {DEPLOY_FAILED, DEPLOY_WAITING_COMPUTER, DEPLOY_CANCELLED}:
        raise ValueError("Only failed, waiting, or cancelled jobs can be retried.")
    ws = session.get(AdWorkstation, job.workstation_id)
    if ws is None:
        raise LookupError("Workstation for that deployment job was not found.")
    sid = (ws.object_sid or "").strip()
    if not gpo_engine.computer_sid_in_pilot_group(
        sid,
        target_group=job.gpo_target_group or gpo_engine.DEFAULT_TARGET_GROUP,
        member_sids=gpo_engine.list_pilot_group_member_sids(
            job.gpo_target_group or gpo_engine.DEFAULT_TARGET_GROUP,
            force_refresh=True,
        ),
    ):
        raise ValueError(
            f"{ws.computer_name}: not authorized for pilot deployment — AD objectSid is not a "
            f"computer member of '{job.gpo_target_group or gpo_engine.DEFAULT_TARGET_GROUP}'."
        )
    prereq = deployment_prerequisites(settings, force_refresh=True)
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.updated_at = _now()
    job.cancelled_at = None
    dto = job_dto(job, ws)
    if not prereq["deployEnabled"]:
        job.status = DEPLOY_WAITING_COMPUTER
        job.last_error = "; ".join(prereq["blockers"])[:2000]
        job.engine = "blocked"
        session.flush()
        return job_dto(job, ws)

    # Mint a fresh enrollment token and re-stage (plaintext from a failed stage is gone).
    now = _now()
    plain_token = secrets.token_urlsafe(32)
    guid = (ws.object_guid or "").strip()
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
    session.flush()

    provisioning_mode = (getattr(settings, "agent_token_provisioning", None) or "").strip().lower()
    drop_unc = (getattr(settings, "agent_token_drop_unc", None) or "").strip()
    if provisioning_mode in {"unc_acl_drop", "token_drop", "unc_drop"} and drop_unc:
        from qka_api.services import token_drop

        try:
            # Replace any prior drop file for this SID.
            token_drop.remove_token_for_computer(drop_unc=drop_unc, computer_sid=sid)
            staged_path = token_drop.stage_token_for_computer(
                drop_unc=drop_unc,
                computer_sid=sid,
                computer_name=ws.computer_name,
                token=plain_token,
            )
            job.status = DEPLOY_SCHEDULED
            job.scheduled_at = now
            job.last_error = ""
            job.engine = "gpo"
            session.flush()
            dto = job_dto(job, ws)
            dto["tokenStaged"] = True
            dto["tokenDropPath"] = staged_path
            dto["machineEnrollmentToken"] = None
            return dto
        except Exception as exc:  # noqa: BLE001
            job.status = DEPLOY_FAILED
            job.last_error = f"Token drop staging failed: {exc}"[:2000]
            job.engine = "gpo"
            session.flush()
            dto = job_dto(job, ws)
            dto["tokenStaged"] = False
            dto["machineEnrollmentToken"] = None
            return dto

    job.status = DEPLOY_SCHEDULED
    job.scheduled_at = now
    job.last_error = ""
    job.engine = "gpo"
    session.flush()
    dto = job_dto(job, ws)
    dto["tokenStaged"] = False
    dto["machineEnrollmentToken"] = plain_token
    return dto


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
