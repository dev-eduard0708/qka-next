"""Super Admin management of QKA-Agent-Pilot-Computers membership only.

Writes are hard-locked to DEFAULT_TARGET_GROUP. The frontend never supplies a group
name or DN. Uses the API host Windows identity (delegated AD rights on that group).
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.services import agent_deployment as deploy_svc
from qka_api.services import gpo_engine
from qka_api.services import workstations as ws_svc
from qka_shared.config import Settings, get_settings
from qka_shared.models import (
    AdAuditEvent,
    AdWorkstation,
    QkaUser,
)

Operation = Literal["add", "remove"]

class PilotMembershipError(ValueError):
    """Validation / policy failure for pilot membership changes."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    session: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    detail: str,
) -> None:
    safe = re.sub(r"(?i)(password|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", detail or "")[:2000]
    session.add(
        AdAuditEvent(
            id=uuid.uuid4(),
            connection_id=None,
            action=action,
            actor_user_id=actor_id,
            detail=safe,
            created_at=_now(),
        )
    )


def _run_membership_ps(
    *,
    operation: Operation,
    computer_sid: str,
    sam_account_name: str,
) -> dict[str, Any]:
    """Mutate ONLY DEFAULT_TARGET_GROUP. SID is the authoritative computer identity."""
    group = gpo_engine.DEFAULT_TARGET_GROUP.replace("'", "''")
    sid = (computer_sid or "").strip().replace("'", "''")
    sam = (sam_account_name or "").strip().replace("'", "''")
    if not sid or not re.match(r"^S-1-\d+(-\d+)+$", sid):
        return {"ok": False, "result": "invalid_sid", "detail": "Computer objectSid is missing or invalid."}
    op = "add" if operation == "add" else "remove"
    script = f"""
$ErrorActionPreference = 'Stop'
$out = [ordered]@{{
  ok = $false
  result = 'error'
  detail = ''
  computerName = ''
  samAccountName = ''
  distinguishedName = ''
  objectSid = ''
  enabled = $false
  memberAfter = $false
  groupName = ''
}}
try {{
  Import-Module ActiveDirectory -ErrorAction Stop
  $expectedGroup = '{group}'
  $grp = Get-ADGroup -Identity $expectedGroup -ErrorAction Stop
  if ($grp.Name -ne $expectedGroup) {{
    throw "Refusing to modify unexpected group '$($grp.Name)'."
  }}
  $out.groupName = [string]$grp.Name
  $pc = $null
  try {{
    $pc = Get-ADComputer -Identity '{sid}' -Properties Enabled,SamAccountName,DistinguishedName,DNSHostName,objectSid -ErrorAction Stop
  }} catch {{
    if ('{sam}') {{
      $pc = Get-ADComputer -Identity '{sam}' -Properties Enabled,SamAccountName,DistinguishedName,DNSHostName,objectSid -ErrorAction Stop
    }} else {{
      throw
    }}
  }}
  if (-not $pc) {{ throw 'Computer not found.' }}
  $out.computerName = [string]($pc.DNSHostName)
  if (-not $out.computerName) {{ $out.computerName = [string]$pc.Name }}
  $out.samAccountName = [string]$pc.SamAccountName
  $out.distinguishedName = [string]$pc.DistinguishedName
  $out.objectSid = [string]$pc.SID.Value
  $out.enabled = [bool]$pc.Enabled
  if ($out.objectSid -ne '{sid}') {{
    throw "Resolved computer SID '$($out.objectSid)' does not match requested SID '{sid}'."
  }}
  if (-not $pc.Enabled) {{
    $out.result = 'disabled'
    $out.detail = 'Computer account is disabled in Active Directory.'
    ($out | ConvertTo-Json -Compress)
    return
  }}
  $isMember = $false
  $members = @(Get-ADGroupMember -Identity $grp -ErrorAction SilentlyContinue)
  foreach ($m in $members) {{
    if ([string]$m.SID -eq $out.objectSid) {{ $isMember = $true; break }}
  }}
  if ('{op}' -eq 'add') {{
    if ($isMember) {{
      $out.ok = $true
      $out.result = 'already_member'
      $out.detail = 'Computer is already a member of the pilot group.'
      $out.memberAfter = $true
    }} else {{
      Add-ADGroupMember -Identity $grp -Members $pc -ErrorAction Stop
      $out.ok = $true
      $out.result = 'added'
      $out.detail = 'Computer added to pilot group.'
      $out.memberAfter = $true
    }}
  }} else {{
    if (-not $isMember) {{
      $out.ok = $true
      $out.result = 'not_member'
      $out.detail = 'Computer was not a member of the pilot group.'
      $out.memberAfter = $false
    }} else {{
      Remove-ADGroupMember -Identity $grp -Members $pc -Confirm:$false -ErrorAction Stop
      $out.ok = $true
      $out.result = 'removed'
      $out.detail = 'Computer removed from pilot group.'
      $out.memberAfter = $false
    }}
  }}
  # Re-verify membership against AD (fail closed if verify fails).
  $verify = $false
  $members2 = @(Get-ADGroupMember -Identity $grp -ErrorAction Stop)
  foreach ($m in $members2) {{
    if ([string]$m.SID -eq $out.objectSid) {{ $verify = $true; break }}
  }}
  $out.memberAfter = $verify
  if ('{op}' -eq 'add' -and -not $verify) {{
    $out.ok = $false
    $out.result = 'verify_failed'
    $out.detail = 'Add reported success but membership verification failed.'
  }}
  if ('{op}' -eq 'remove' -and $verify) {{
    $out.ok = $false
    $out.result = 'verify_failed'
    $out.detail = 'Remove reported success but computer is still a group member.'
  }}
}} catch {{
  $out.ok = $false
  $out.result = 'error'
  $out.detail = $_.Exception.Message
}}
($out | ConvertTo-Json -Compress)
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        raw = (completed.stdout or "").strip()
        if not raw:
            err = (completed.stderr or "").strip()[:500]
            return {
                "ok": False,
                "result": "error",
                "detail": err or "AD membership command returned no output.",
                "memberAfter": False,
            }
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"ok": False, "result": "error", "detail": "Unexpected AD response.", "memberAfter": False}
        return data
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "result": "error", "detail": str(exc)[:500], "memberAfter": False}


def _active_deploy_blocks_removal(session: Session, ws: AdWorkstation) -> str | None:
    from qka_api.services.agent_deployment import latest_job_for_workstation
    from qka_shared.models import (
        DEPLOY_APPROVED,
        DEPLOY_INSTALLING,
        DEPLOY_REQUESTED,
        DEPLOY_SCHEDULED,
        DEPLOY_WAITING_COMPUTER,
    )

    job = latest_job_for_workstation(session, ws.id)
    if job is None or job.cancelled_at is not None:
        return None
    if job.status in {
        DEPLOY_REQUESTED,
        DEPLOY_APPROVED,
        DEPLOY_SCHEDULED,
        DEPLOY_WAITING_COMPUTER,
        DEPLOY_INSTALLING,
    }:
        return (
            "Cannot remove from pilot group while an active deployment job exists. "
            "Cancel or complete the deployment first."
        )
    return None


def change_pilot_membership(
    session: Session,
    settings: Settings | None,
    *,
    actor: QkaUser,
    workstation_ids: list[uuid.UUID],
    operation: Operation,
) -> dict[str, Any]:
    """Add or remove workstations from the fixed pilot security group."""
    settings = settings or get_settings()
    if operation not in {"add", "remove"}:
        raise PilotMembershipError("operation must be add or remove.")
    ids = list(dict.fromkeys(workstation_ids or []))
    if not ids:
        raise PilotMembershipError("Select at least one workstation.")
    if len(ids) > 50:
        raise PilotMembershipError("Bulk pilot membership is limited to 50 workstations.")

    locked_group = gpo_engine.DEFAULT_TARGET_GROUP
    rows = list(
        session.execute(select(AdWorkstation).where(AdWorkstation.id.in_(ids))).scalars().all()
    )
    by_id = {r.id: r for r in rows}
    results: list[dict[str, Any]] = []
    any_ok = False

    for wid in ids:
        ws = by_id.get(wid)
        if ws is None:
            results.append(
                {
                    "workstationId": str(wid),
                    "ok": False,
                    "result": "workstation_not_in_inventory",
                    "detail": (
                        "Workstation ID was not found in the QKA AD inventory "
                        "(not an API route 404). Sync workstations and retry."
                    ),
                    "pilotGroupMember": False,
                    "pilotGroup": locked_group,
                }
            )
            continue

        base = {
            "workstationId": str(ws.id),
            "computerName": ws.computer_name,
            "dnsHostname": ws.dns_hostname,
            "objectSid": ws.object_sid,
            "samAccountName": (ws.computer_name or "").rstrip("$") + "$"
            if ws.computer_name
            else "",
            "pilotGroup": locked_group,
        }

        if not ws.is_present:
            results.append({**base, "ok": False, "result": "missing", "detail": "Computer is not present in AD inventory.", "pilotGroupMember": False})
            continue
        if not ws.account_enabled:
            results.append({**base, "ok": False, "result": "disabled", "detail": "Computer account is disabled.", "pilotGroupMember": False})
            continue
        if not (ws.object_sid or "").strip():
            results.append({**base, "ok": False, "result": "no_sid", "detail": "AD objectSid is required.", "pilotGroupMember": False})
            continue
        if ws.is_server_or_dc:
            results.append({**base, "ok": False, "result": "server_or_dc", "detail": "Servers/DCs cannot join the desktop agent pilot group.", "pilotGroupMember": False})
            continue

        if operation == "remove":
            block = _active_deploy_blocks_removal(session, ws)
            if block:
                # Still report current membership for UI honesty.
                in_pilot = gpo_engine.computer_sid_in_pilot_group(ws.object_sid or "")
                results.append(
                    {
                        **base,
                        "ok": False,
                        "result": "active_deployment",
                        "detail": block,
                        "pilotGroupMember": in_pilot,
                        "agentInstalled": bool(ws.desktop_device_id),
                        "warnAgentInstalled": bool(ws.desktop_device_id),
                    }
                )
                continue

        ad = _run_membership_ps(
            operation=operation,
            computer_sid=ws.object_sid or "",
            sam_account_name=base["samAccountName"],
        )
        member_after = bool(ad.get("memberAfter"))
        ok = bool(ad.get("ok"))
        result_code = str(ad.get("result") or "error")
        detail = str(ad.get("detail") or "")
        if ok:
            any_ok = True

        item = {
            **base,
            "ok": ok,
            "result": result_code,
            "detail": detail,
            "pilotGroupMember": member_after,
            "adComputerName": str(ad.get("computerName") or ws.computer_name or ""),
            "adSamAccountName": str(ad.get("samAccountName") or base["samAccountName"]),
            "adDistinguishedName": str(ad.get("distinguishedName") or ws.distinguished_name or ""),
            "agentInstalled": bool(ws.desktop_device_id),
            "gpoReadinessNote": (
                "AD membership updated. Workstation-side Group Policy application is not verified remotely. "
                "If the QKA-Agent-Install scheduled task is not present yet, run gpupdate /force on the computer "
                "or wait for the next policy cycle. QKA will not reboot the PC."
                if operation == "add" and member_after
                else ""
            ),
        }
        results.append(item)
        _audit(
            session,
            action=f"pilot_group_{operation}",
            actor_id=actor.id,
            detail=(
                f"op={operation} result={result_code} ok={ok} computer={ws.computer_name} "
                f"sid={ws.object_sid} group={locked_group} detail={detail[:400]}"
            ),
        )

    gpo_engine.invalidate_ad_readiness_caches()
    deploy_svc.invalidate_deployment_readiness_cache()
    # Force-refresh membership for DTO eligibility.
    member_sids = gpo_engine.list_pilot_group_member_sids(locked_group, force_refresh=True)
    prereq = deploy_svc.deployment_prerequisites(settings, force_refresh=True)

    workstations_out: list[dict[str, Any]] = []
    for wid in ids:
        ws = by_id.get(wid)
        if ws is None:
            continue
        workstations_out.append(
            ws_svc.workstation_dto(
                session,
                ws,
                settings,
                prereq=prereq,
                pilot_member_sids=member_sids,
            )
        )

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    return {
        "operation": operation,
        "pilotGroup": locked_group,
        "succeeded": succeeded,
        "failed": failed,
        "partial": failed > 0 and succeeded > 0,
        "allSucceeded": failed == 0 and succeeded > 0,
        "anySucceeded": any_ok,
        "results": results,
        "workstations": workstations_out,
        "prerequisites": prereq,
        "note": (
            "Adding a computer to the pilot group does not install the agent. "
            "Deploy Agent remains a separate Super Admin action after readiness checks pass."
            if operation == "add"
            else (
                "Removing pilot membership does not uninstall an existing agent or cancel history. "
                "Active deployments must be cancelled separately before removal."
            )
        ),
    }
