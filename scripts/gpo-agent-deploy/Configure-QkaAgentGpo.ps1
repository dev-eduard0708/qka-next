<#
.SYNOPSIS
  Link existing QKA Agent GPO and scope it to QKA-Agent-Pilot-Computers only.

.DESCRIPTION
  Uses existing GPO GUID {73E8998A-A3B3-435F-9219-419C331BA2D4}. Does NOT recreate the GPO.
  Requires RSAT GroupPolicy on an authorized AD management host (not Domain Admin in QKA app).

  - Links GPO to a pilot OU (user-supplied)
  - Security filtering: remove Authenticated Users Apply; add QKA-Agent-Pilot-Computers
  - Sets Software Installation package to MSI UNC (no enrollment secrets in GPO/SYSVOL)
  - Does NOT add computers to the pilot group (see Add-QkaPilotComputer.ps1)

.PARAMETER PilotOuDn
  OU DN where the GPO will be linked (pilot workstations OU only — never domain root).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$PilotOuDn,

  [Parameter(Mandatory)]
  [string]$MsiUnc,

  [string]$GpoGuid = '{73E8998A-A3B3-435F-9219-419C331BA2D4}',
  [string]$PilotGroup = 'QKA-Agent-Pilot-Computers',
  [string]$ServerUrl = 'https://qka-agent.qehc.edu.sa',
  [switch]$IConfirmGpoScope
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmGpoScope) {
  throw 'Pass -IConfirmGpoScope after reviewing PilotOuDn and MsiUnc. Refusing to change domain GPO without confirmation.'
}
if (-not (Get-Module -ListAvailable GroupPolicy)) {
  throw 'RSAT GroupPolicy module required on this management host (Install-WindowsFeature GPMC / RSAT-GPMC).'
}
Import-Module GroupPolicy -ErrorAction Stop
Import-Module ActiveDirectory -ErrorAction Stop

if (-not (Test-Path -LiteralPath $MsiUnc)) {
  throw "MSI UNC not reachable from this host: $MsiUnc"
}
$ou = Get-ADOrganizationalUnit -Identity $PilotOuDn -ErrorAction Stop
$gpo = Get-GPO -Guid $GpoGuid.Trim('{}') -ErrorAction Stop
$grp = Get-ADGroup -Identity $PilotGroup -ErrorAction Stop

Write-Host ("GPO=" + $gpo.DisplayName + " Id=" + $gpo.Id)
Write-Host ("OU=" + $ou.DistinguishedName)
Write-Host ("GROUP=" + $grp.DistinguishedName)

# Link (idempotent)
$existing = (Get-GPInheritance -Target $ou.DistinguishedName).GpoLinks |
  Where-Object { $_.GpoId -eq $gpo.Id.ToString() -or $_.DisplayName -eq $gpo.DisplayName }
if (-not $existing) {
  New-GPLink -Guid $gpo.Id -Target $ou.DistinguishedName -LinkEnabled Yes | Out-Null
  Write-Host 'GPLink created.'
} else {
  Write-Host 'GPLink already present.'
}

# Security filtering: Apply only to pilot computer group
# Remove Authenticated Users Apply if present
try {
  Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
} catch {
  Write-Warning ("Could not adjust Authenticated Users: " + $_.Exception.Message)
}
Set-GPPermission -Guid $gpo.Id -TargetName $PilotGroup -TargetType Group -PermissionLevel GpoApply -Replace | Out-Null
Write-Host "Security filtering Apply → $PilotGroup (only)."

# Software Installation via GP Preferences / package note:
# Full Software Installation COM packaging often needs GPMC UI or LGPO helpers.
# We write a documented Startup script alternative that installs from UNC silently —
# WITHOUT embedding enrollment tokens (tokens are HKLM out-of-band only).
$sysvol = "\\qehc.edu.sa\SysVol\qehc.edu.sa\Policies\$GpoGuid\Machine\Scripts\Startup"
New-Item -ItemType Directory -Force -Path $sysvol | Out-Null
$installPs1 = Join-Path $sysvol 'Install-QkaAgentFromUnc.ps1'
@'
# QKA Agent MSI bootstrap (no enrollment secrets). Deployed via GPO Startup for pilot group only.
param()
$ErrorActionPreference = "Stop"
$msi = "__MSI_UNC__"
$url = "__SERVER_URL__"
$log = "$env:ProgramData\QKA\DesktopAgent\logs\gpo-install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
if (Get-Service -Name "QkaDesktopAgent" -ErrorAction SilentlyContinue) {
  Add-Content $log "$(Get-Date -Format o) already_installed"
  exit 0
}
if (-not (Test-Path -LiteralPath $msi)) {
  Add-Content $log "$(Get-Date -Format o) msi_missing $msi"
  exit 1
}
$args = "/i `"$msi`" /qn /norestart QKASERVERURL=$url /L*v `"$env:ProgramData\QKA\DesktopAgent\logs\msiexec.log`""
Add-Content $log "$(Get-Date -Format o) starting msiexec"
$p = Start-Process -FilePath msiexec.exe -ArgumentList $args -Wait -PassThru
Add-Content $log "$(Get-Date -Format o) msiexec_exit=$($p.ExitCode)"
exit $p.ExitCode
'@.Replace('__MSI_UNC__', $MsiUnc).Replace('__SERVER_URL__', $ServerUrl) |
  Set-Content -Path $installPs1 -Encoding UTF8

# scripts.ini for Machine Startup
$scriptsIni = Join-Path (Split-Path $sysvol) 'scripts.ini'
@"
[Startup]
0CmdLine=Install-QkaAgentFromUnc.ps1
0Parameters=
"@ | Set-Content -Path $scriptsIni -Encoding ASCII

Write-Host ("Startup script written: " + $installPs1)
Write-Host 'NOTE: Enrollment tokens must NEVER be placed in this SYSVOL script.'
Write-Host 'Use Provision-MachineEnrollmentToken.ps1 (HKLM out-of-band) after Deploy in QKA UI.'
Write-Host 'Increment GPO version by editing in GPMC if clients do not refresh.'
