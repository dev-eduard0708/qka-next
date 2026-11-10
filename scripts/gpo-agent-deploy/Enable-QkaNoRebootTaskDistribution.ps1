<#
.SYNOPSIS
  Enable no-reboot centralized QKA agent task distribution (approval-gated).

.DESCRIPTION
  PROBLEM
    GpoApply is currently limited to QKA-Agent-Pilot-Computers. A computer added to
    that group does not receive the GPP Scheduled Task until its Kerberos PAC
    refreshes (often a reboot). QKA UI Deploy then stays Pending because
    QKA-Agent-Install never appears.

  PROPOSED ARCHITECTURE (default-deny install)
    1) GPO link stays on the managed WorkStations OU only (never domain root).
    2) GpoApply is granted to Domain Computers (already in every machine PAC) so
       QKA-Agent-Install is pre-provisioned by normal background policy refresh —
       no pilot-group PAC wait, no interactive login, no required reboot for task
       distribution.
    3) Install script (SYSTEM, PT2M) remains harmless without a SID-bound *.tok
       AND live /install-authorization success for that computer SID.
    4) Pilot group membership remains mandatory for QKA Super Admin Deploy /
       token staging (backend SID check). Membership alone never installs.
    5) MSI + enrollment tokens stay off SYSVOL (UNC MSI + ACL token-drop only).

  SECURITY SCOPE (what this script changes when confirmed)
    - Set-GPPermission on GPO {73E8998A-A3B3-435F-9219-419C331BA2D4} only:
        Domain Computers → GpoApply
        QKA-Agent-Pilot-Computers → GpoRead (no longer required for Apply)
        Authenticated Users → GpoRead (unchanged intent)
    - Does NOT change OU links, GPO settings XML, SYSVOL script, MSI ACLs,
      token-drop ACLs, or pilot group membership.
    - Does NOT reboot any workstation.
    - Does NOT deploy or stage tokens.

  RISKS
    - Every computer under the linked OU that successfully applies policy will
      create/update QKA-Agent-Install and run the bootstrap PowerShell every ~2
      minutes as SYSTEM. Without a valid token the script exits 0 (auth_gate_skip).
    - Broadening Apply increases the number of machines that execute the SYSVOL
      script; protect SYSVOL/GPO edit rights and keep the script fail-closed.
    - WSIT02 already has the task; this change must not remove its Apply path
      (Domain Computers covers WSIT02$).

  DEFAULT: dry-run only. Pass -IConfirmOuComputerGpoApply after explicit approval.
#>
[CmdletBinding()]
param(
  [string]$GpoGuid = '{73E8998A-A3B3-435F-9219-419C331BA2D4}',
  [string]$PilotGroup = 'QKA-Agent-Pilot-Computers',
  [string]$DistributionPrincipal = 'Domain Computers',
  [switch]$IConfirmOuComputerGpoApply
)

$ErrorActionPreference = 'Stop'

Write-Host '=== QKA NO-REBOOT TASK DISTRIBUTION — PROPOSAL ==='
Write-Host 'PROPOSED_ARCHITECTURE=OU-linked GPP Scheduled Task pre-provisioned via Domain Computers GpoApply; install gated by SID token + API authorization; pilot group retained for Deploy/token staging only'
Write-Host 'GPO_DISTRIBUTION_SCOPE=Existing link targets of QKA-Desktop-Agent-Deployment only (WorkStations OU path); never domain root'
Write-Host 'AUTHORIZATION_SECURITY=Default-deny Install-QkaAgentFromUnc.ps1; requires per-SID *.tok ACL + /install-authorization + machineProof; MSI/tokens not in SYSVOL'
Write-Host 'NO_REBOOT_WORKFLOW=Add to Pilot (UI) → Deploy (UI stages token) → existing computer policy refresh delivers/runs task → install-progress → register/heartbeat → Deployed'
Write-Host ''

if (-not (Get-Module -ListAvailable GroupPolicy)) {
  throw 'RSAT GroupPolicy module required on this management host.'
}
Import-Module GroupPolicy -ErrorAction Stop
Import-Module ActiveDirectory -ErrorAction Stop

$guidBare = $GpoGuid.Trim('{}')
$gpo = Get-GPO -Guid $guidBare -ErrorAction Stop
Write-Host ("GPO=" + $gpo.DisplayName + " Id=" + $gpo.Id)

$links = @()
Get-ADOrganizationalUnit -Filter * -Properties gPLink -ErrorAction SilentlyContinue |
  Where-Object { $_.gPLink -and $_.gPLink -like ("*" + $guidBare + "*") } |
  ForEach-Object { $links += $_.DistinguishedName; Write-Host ("LINK_TARGET=" + $_.DistinguishedName) }
if ($links.Count -eq 0) {
  throw 'GPO is not linked to any OU — aborting (refusing domain-wide invent).'
}
if ($links | Where-Object { $_ -match '(?i)^DC=' }) {
  throw 'Refusing domain-root link target.'
}

Write-Host '--- CURRENT GpoApply ---'
Get-GPPermission -Guid $gpo.Id -All |
  Where-Object { $_.Permission -eq 'GpoApply' } |
  ForEach-Object { Write-Host ("APPLY=" + $_.Trustee.Name) }

Write-Host ''
Write-Host 'ADMIN_APPROVAL_REQUIRED=YES — broadening GpoApply to Domain Computers on this GPO'
Write-Host 'RISK_SUMMARY=SYSTEM task every ~2m on OU computers; mitigated by SID token + API gate; no auto-install'
Write-Host 'WSIT02_IMPACT=None expected (already Online; Domain Computers includes WSIT02$)'
Write-Host 'OTHER_PC_DEPLOY=None — no tokens staged by this script'

if (-not $IConfirmOuComputerGpoApply) {
  Write-Host ''
  Write-Host 'Dry-run only. After written approval, re-run with:'
  Write-Host ("  powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -IConfirmOuComputerGpoApply")
  Write-Host 'IMPLEMENTATION_STATUS=PROPOSED_NOT_APPLIED'
  return
}

# --- Confirmed change (GPO ACL only) ---
$backup = Join-Path $env:TEMP ('qka-gpo-apply-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
Get-GPPermission -Guid $gpo.Id -All | Out-String | Set-Content -Path $backup -Encoding UTF8
Write-Host ("ACL_BACKUP=" + $backup)

Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
Set-GPPermission -Guid $gpo.Id -TargetName $DistributionPrincipal -TargetType Group -PermissionLevel GpoApply -Replace | Out-Null
# Pilot group no longer needs Apply for task distribution; keep Read so admins can still see filtering intent.
try {
  Set-GPPermission -Guid $gpo.Id -TargetName $PilotGroup -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
} catch {
  Write-Warning ("Pilot group permission adjust: " + $_.Exception.Message)
}
# Remove accidental Everyone Apply if present
try {
  Set-GPPermission -Guid $gpo.Id -TargetName 'Everyone' -TargetType Group -PermissionLevel None -Replace -ErrorAction SilentlyContinue | Out-Null
} catch { }

Write-Host '--- AFTER GpoApply ---'
$apply = @(Get-GPPermission -Guid $gpo.Id -All | Where-Object { $_.Permission -eq 'GpoApply' })
$apply | ForEach-Object { Write-Host ("APPLY=" + $_.Trustee.Name) }
$names = @($apply | ForEach-Object { $_.Trustee.Name })
if ($names -notcontains $DistributionPrincipal -and -not ($names | Where-Object { $_ -like "*$DistributionPrincipal*" })) {
  throw "Failed to grant GpoApply to $DistributionPrincipal"
}
if ($names | Where-Object { $_ -match 'Everyone' }) {
  throw 'Everyone still has GpoApply — fix manually.'
}

Write-Host 'IMPLEMENTATION_STATUS=GPO_APPLY_BROADENED_TO_DOMAIN_COMPUTERS'
Write-Host 'NEXT=Set QKA_AGENT_GPO_TASK_DISTRIBUTION=ou_computers in API .env and restart API'
Write-Host 'NEXT_CLIENT=Background gpupdate on managed PCs will create QKA-Agent-Install; no reboot required for PAC'
Write-Host 'WSIT04=After policy refresh, task should appear; existing staged token can authorize install without redeploy'
