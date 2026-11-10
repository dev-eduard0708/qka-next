"""Least-privilege GPO deployment capability probe (no unrestricted remoting).

QKA never runs arbitrary PowerShell against domain PCs and never uses Domain Admin
credentials in-app. Managed deploy records jobs + machine-bound enrollment tokens;
Domain Ops applies the dedicated GPO on an authorized GPMC host. GPMC need not live
on the QKA API host once AD shows the GPO linked and pilot-scoped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

_PILOT_SID_TTL_SEC = 60.0
_PILOT_SID_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
_PILOT_SID_LOCK = Lock()
_GPO_PROBE_TTL_SEC = 45.0
_GPO_PROBE_CACHE: dict[str, tuple[float, "GpoEngineStatus"]] = {}
_GPO_PROBE_LOCK = Lock()


DEFAULT_MSI_PATH = Path(r"C:\QehcAI\QKA-Next\agent\dist\QKA-Agent.msi")
DEFAULT_TARGET_GROUP = "QKA-Agent-Pilot-Computers"
DEFAULT_GPO_NAME = "QKA-Desktop-Agent-Deployment"
DEFAULT_GPO_GUID = "{73E8998A-A3B3-435F-9219-419C331BA2D4}"


@dataclass(frozen=True)
class GpoEngineStatus:
    available: bool
    reason: str
    target_group: str
    msi_path: str
    msi_present: bool
    gpmc_present: bool
    msi_unc: str = ""
    msi_unc_reachable: bool = False
    ad_group_exists: bool = False
    ad_group_member_count: int | None = None
    ad_gpo_exists: bool = False
    ad_gpo_display_name: str = ""
    ad_gpo_guid: str = ""
    ad_gpo_linked: bool = False
    ad_gpo_link_targets: tuple[str, ...] = field(default_factory=tuple)
    # Hard failures only (never GPMC-missing notes or instructional fragments).
    hard_reasons: tuple[str, ...] = field(default_factory=tuple)


def _gpmc_tools_present() -> bool:
    """Detect RSAT Group Policy Management tools without writing domain state."""
    if shutil.which("Get-GPO"):
        return True
    for candidate in (
        Path(r"C:\Windows\System32\gpmc.msc"),
        Path(r"C:\Windows\SysWOW64\gpmc.msc"),
    ):
        if candidate.is_file():
            return True
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "if (Get-Command Get-GPO -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if "yes" in (completed.stdout or "").strip().lower():
            return True
    except Exception:
        pass
    return False


def _unc_reachable(unc: str) -> bool:
    path = (unc or "").strip()
    if not path:
        return False
    try:
        return Path(path).is_file()
    except Exception:
        return False


def invalidate_ad_readiness_caches() -> None:
    """Drop cached AD probes (call after admin changes GPO/pilot membership)."""
    with _PILOT_SID_LOCK:
        _PILOT_SID_CACHE.clear()
    with _GPO_PROBE_LOCK:
        _GPO_PROBE_CACHE.clear()


def list_pilot_group_member_sids(
    target_group: str = DEFAULT_TARGET_GROUP,
    *,
    force_refresh: bool = False,
) -> frozenset[str]:
    """Return objectSid values for computer accounts in the pilot security group.

    Used to authorize Deploy Agent targets. Empty set means no computer is eligible.
    Read-only — never adds/removes group members. Cached briefly for inventory UI.
    """
    group_key = (target_group or DEFAULT_TARGET_GROUP).strip() or DEFAULT_TARGET_GROUP
    now = time.monotonic()
    if not force_refresh:
        with _PILOT_SID_LOCK:
            hit = _PILOT_SID_CACHE.get(group_key)
            if hit and (now - hit[0]) < _PILOT_SID_TTL_SEC:
                return hit[1]

    group = group_key.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$sids = @()
try {{
  Import-Module ActiveDirectory -ErrorAction Stop
  $g = Get-ADGroup -Identity '{group}' -ErrorAction Stop
  $members = @(Get-ADGroupMember -Identity $g -Recursive -ErrorAction SilentlyContinue)
  foreach ($m in $members) {{
    if ($m.objectClass -ne 'computer') {{ continue }}
    try {{
      $c = Get-ADComputer -Identity $m.SID -Properties objectSid -ErrorAction Stop
      if ($c.SID) {{ $sids += [string]$c.SID.Value }}
    }} catch {{
      try {{
        $c = Get-ADComputer -Identity $m.SamAccountName -Properties objectSid -ErrorAction Stop
        if ($c.SID) {{ $sids += [string]$c.SID.Value }}
      }} catch {{}}
    }}
  }}
}} catch {{}}
(@{{ sids = $sids }} | ConvertTo-Json -Compress)
"""
    result: frozenset[str] = frozenset()
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        raw = (completed.stdout or "").strip()
        if raw:
            data = json.loads(raw)
            sids = data.get("sids") or []
            if isinstance(sids, str):
                sids = [sids]
            result = frozenset(str(s).strip() for s in sids if str(s).strip())
    except Exception:
        result = frozenset()
    with _PILOT_SID_LOCK:
        _PILOT_SID_CACHE[group_key] = (time.monotonic(), result)
    return result


def computer_sid_in_pilot_group(
    computer_sid: str,
    *,
    target_group: str = DEFAULT_TARGET_GROUP,
    member_sids: frozenset[str] | None = None,
) -> bool:
    """True when the workstation's AD objectSid is a computer member of the pilot group."""
    sid = (computer_sid or "").strip()
    if not sid:
        return False
    members = member_sids if member_sids is not None else list_pilot_group_member_sids(target_group)
    return sid in members


def _probe_ad_objects(*, target_group: str, gpo_name: str) -> dict:
    """Read-only AD inventory for pilot group / dedicated GPO (no creates)."""
    script = f"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{{
  groupExists = $false
  groupMembers = -1
  gpoExists = $false
  gpoDisplayName = ''
  gpoGuid = ''
  gpoLinked = $false
  gpoLinks = @()
}}
try {{
  Import-Module ActiveDirectory -ErrorAction Stop
  $g = Get-ADGroup -Identity '{target_group}' -ErrorAction SilentlyContinue
  if ($g) {{
    $result.groupExists = $true
    $result.groupMembers = @(Get-ADGroupMember -Identity $g -ErrorAction SilentlyContinue).Count
  }}
  $gpo = $null
  try {{
    $gpo = Get-ADObject -Identity 'CN={DEFAULT_GPO_GUID},CN=Policies,CN=System,DC=qehc,DC=edu,DC=sa' `
      -Properties displayName -ErrorAction Stop
  }} catch {{
    $gpo = Get-ADObject -LDAPFilter '(&(objectClass=groupPolicyContainer)(displayName={gpo_name}*))' `
      -SearchBase 'CN=Policies,CN=System,DC=qehc,DC=edu,DC=sa' `
      -Properties displayName -ErrorAction SilentlyContinue | Select-Object -First 1
  }}
  if ($gpo) {{
    $result.gpoExists = $true
    $result.gpoDisplayName = [string]$gpo.displayName
    $result.gpoGuid = [string]$gpo.Name
    $needle = $gpo.Name
    $ous = Get-ADOrganizationalUnit -Filter * -Properties gPLink -ErrorAction SilentlyContinue |
      Where-Object {{ $_.gPLink -and $_.gPLink -like ("*" + $needle + "*") }}
    $links = @($ous | ForEach-Object {{ $_.DistinguishedName }})
    $result.gpoLinks = $links
    $result.gpoLinked = ($links.Count -gt 0)
  }}
}} catch {{}}
$result | ConvertTo-Json -Compress
"""
    out = {
        "groupExists": False,
        "groupMembers": None,
        "gpoExists": False,
        "gpoDisplayName": "",
        "gpoGuid": "",
        "gpoLinked": False,
        "gpoLinks": [],
    }
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        raw = (completed.stdout or "").strip()
        if not raw:
            return out
        data = json.loads(raw)
        out["groupExists"] = bool(data.get("groupExists"))
        members = data.get("groupMembers")
        out["groupMembers"] = int(members) if members is not None and int(members) >= 0 else None
        out["gpoExists"] = bool(data.get("gpoExists"))
        out["gpoDisplayName"] = str(data.get("gpoDisplayName") or "")
        out["gpoGuid"] = str(data.get("gpoGuid") or "")
        out["gpoLinked"] = bool(data.get("gpoLinked"))
        links = data.get("gpoLinks") or []
        out["gpoLinks"] = [str(x) for x in links if x]
    except Exception:
        return out
    return out


def probe_gpo_engine(
    *,
    msi_path: Path | None = None,
    target_group: str = DEFAULT_TARGET_GROUP,
    gpo_name: str = DEFAULT_GPO_NAME,
    msi_unc: str = "",
    force_refresh: bool = False,
) -> GpoEngineStatus:
    path = msi_path or DEFAULT_MSI_PATH
    unc = (msi_unc or "").strip()
    cache_key = f"{path}|{target_group}|{gpo_name}|{unc}"
    now = time.monotonic()
    if not force_refresh:
        with _GPO_PROBE_LOCK:
            hit = _GPO_PROBE_CACHE.get(cache_key)
            if hit and (now - hit[0]) < _GPO_PROBE_TTL_SEC:
                return hit[1]

    msi_present = path.is_file()
    gpmc = _gpmc_tools_present()
    unc_ok = _unc_reachable(unc) if unc else False
    ad = _probe_ad_objects(target_group=target_group, gpo_name=gpo_name)

    hard: list[str] = []
    notes: list[str] = []
    if not msi_present:
        hard.append("QKA-Agent.msi is missing on the build host.")
    if unc and not unc_ok:
        hard.append(f"MSI UNC is not reachable: {unc}")
    if not unc:
        hard.append(
            "Computer-readable MSI UNC is not configured (set QKA_AGENT_MSI_UNC after New-QkaAgentMsiShare.ps1)."
        )
    if not ad["groupExists"]:
        hard.append(f"AD security group '{target_group}' was not found.")
    elif ad["groupMembers"] == 0:
        hard.append(
            f"AD group '{target_group}' exists but has zero computer members — "
            "add pilot computers from Workstations (Add to Deployment Pilot) or "
            "Add-QkaPilotComputer.ps1 -IConfirmAddPilotComputer."
        )
    if not ad["gpoExists"]:
        hard.append(f"Dedicated GPO '{gpo_name}' was not found in AD.")
    elif not ad["gpoLinked"]:
        # Keep as one hard reason — do not embed "On a GPMC host…" here (that was split into a
        # false GPMC-required blocker by the prerequisites sentence splitter).
        hard.append(
            f"GPO '{ad['gpoDisplayName'] or gpo_name}' exists but is not linked to any OU "
            f"(run scripts/gpo-agent-deploy/Configure-QkaAgentGpo.ps1 on an authorized AD "
            f"management host; Apply only for '{target_group}')."
        )
    if not gpmc:
        # Informational only — local GPMC is NOT required on the QKA API host.
        notes.append(
            "NOTE: GPMC/Get-GPO is not on this QKA API host — configure/link the GPO from an "
            "authorized AD management host (scripts/gpo-agent-deploy)."
        )

    # Available when MSI + UNC + AD group + linked GPO + ≥1 pilot member.
    # Local GPMC is NOT required on the API host (ops may use another management PC).
    available = bool(
        msi_present
        and unc_ok
        and ad["groupExists"]
        and ad["gpoExists"]
        and ad["gpoLinked"]
        and (ad["groupMembers"] or 0) > 0
        and not hard
    )

    if not hard and not notes:
        reason = (
            "GPO AD objects, MSI UNC, and pilot membership look ready. Deploy still requires "
            "trusted HTTPS, Super Admin confirmation, and secure per-computer token provisioning."
        )
    else:
        reason = " ".join([*hard, *notes])

    status = GpoEngineStatus(
        available=available,
        reason=reason,
        target_group=target_group,
        msi_path=str(path),
        msi_present=msi_present,
        gpmc_present=gpmc,
        msi_unc=unc,
        msi_unc_reachable=unc_ok,
        ad_group_exists=bool(ad["groupExists"]),
        ad_group_member_count=ad["groupMembers"],
        ad_gpo_exists=bool(ad["gpoExists"]),
        ad_gpo_display_name=str(ad["gpoDisplayName"] or ""),
        ad_gpo_guid=str(ad["gpoGuid"] or ""),
        ad_gpo_linked=bool(ad["gpoLinked"]),
        ad_gpo_link_targets=tuple(ad["gpoLinks"] or ()),
        hard_reasons=tuple(hard),
    )
    with _GPO_PROBE_LOCK:
        _GPO_PROBE_CACHE[cache_key] = (time.monotonic(), status)
    return status


def build_gpo_package_manifest(
    *,
    computer_names: list[str],
    server_base_url: str,
    msi_path: str,
    target_group: str,
    msi_unc: str = "",
) -> dict:
    """Describe the intended GPO package (no domain writes from QKA)."""
    names = [n.strip() for n in computer_names if (n or "").strip()]
    if not names:
        raise ValueError("Select at least one computer for deployment.")
    if len(names) > 50:
        raise ValueError("Bulk deployment is limited to 50 computers per approval batch.")
    package_path = (msi_unc or "").strip() or msi_path
    return {
        "packageType": "msi_unc_startup_or_software_installation",
        "msiPath": msi_path,
        "msiUnc": package_path,
        "silentArgs": f'/qn QKASERVERURL={server_base_url}',
        "targetGroup": target_group,
        "computers": names,
        "scopeRule": "computer_group_only",
        "forbidden": ["entire_ou", "all_domain", "unrestricted_psexec", "unrestricted_winrm", "token_in_sysvol"],
        "tokenProvisioning": {
            "mode": "hkml_out_of_band",
            "script": "scripts/gpo-agent-deploy/Provision-MachineEnrollmentToken.ps1",
            "registryValue": r"HKLM\SOFTWARE\QKA\DesktopAgent\MachineEnrollmentToken",
            "forbidden": ["embed_in_msi", "shared_domain_secret", "gpo_registry_preference_token", "log_plaintext_token"],
            "note": (
                "Deliver machineEnrollmentToken only via Provision-MachineEnrollmentToken.ps1 "
                "to the selected pilot PC HKLM. HMAC machineProof proves token possession for the "
                "bound computer SID — it is not Kerberos machine authentication."
            ),
        },
        "note": (
            "QKA records the job and one-time machine-bound token. Domain Ops applies the "
            "dedicated GPO to the approved computer group; agent registration/heartbeat proves install."
        ),
    }
