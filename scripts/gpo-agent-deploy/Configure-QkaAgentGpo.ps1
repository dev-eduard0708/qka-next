<#
.SYNOPSIS
  Link existing QKA Agent GPO, scope to pilot computer group, register Machine Startup script properly.

.DESCRIPTION
  Uses GPO GUID {73E8998A-A3B3-435F-9219-419C331BA2D4}. Does NOT recreate the GPO.
  Requires RSAT GroupPolicy on an authorized AD management host.

  - Links GPO to a pilot OU only (never domain root)
  - Security filtering: GpoApply ONLY for QKA-Agent-Pilot-Computers; Authenticated Users → GpoRead
  - Registers Machine Startup via GPT.ini version bump + Scripts CSE extension names
    (does not rely on copying scripts.ini alone without CSE/version updates)
  - Startup installs MSI from UNC with QKASERVERURL only — NO enrollment tokens in SYSVOL

  Does NOT add computers to the pilot group. Does NOT run unless -IConfirmGpoScope.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$PilotOuDn,

  [Parameter(Mandatory)]
  [string]$MsiUnc,

  # Non-secret path so the agent can claim its ACL-bound *.tok after install.
  [string]$TokenDropUnc = '\\QEC-AI-MCP\QkaAgentTokens',

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
  throw 'RSAT GroupPolicy module required on this management host (RSAT-GPMC / GPMC).'
}
Import-Module GroupPolicy -ErrorAction Stop
Import-Module ActiveDirectory -ErrorAction Stop

if ($PilotOuDn -match '(?i)^DC=') {
  throw 'Refusing to link at domain root. Provide a dedicated pilot OU DN.'
}
if (-not (Test-Path -LiteralPath $MsiUnc)) {
  throw "MSI UNC not reachable from this host: $MsiUnc"
}

$ou = Get-ADOrganizationalUnit -Identity $PilotOuDn -ErrorAction Stop
$guidBare = $GpoGuid.Trim('{}')
$gpo = Get-GPO -Guid $guidBare -ErrorAction Stop
$grp = Get-ADGroup -Identity $PilotGroup -ErrorAction Stop
$gpoDn = "CN={$guidBare},CN=Policies,CN=System,DC=qehc,DC=edu,DC=sa"
$sysvolRoot = "\\qehc.edu.sa\SysVol\qehc.edu.sa\Policies\{$guidBare}"
$startupDir = Join-Path $sysvolRoot 'Machine\Scripts\Startup'
$scriptsDir = Join-Path $sysvolRoot 'Machine\Scripts'
$gptIni = Join-Path $sysvolRoot 'GPT.INI'

Write-Host ("GPO=" + $gpo.DisplayName + " Id=" + $gpo.Id)
Write-Host ("OU=" + $ou.DistinguishedName)
Write-Host ("GROUP=" + $grp.DistinguishedName)

# --- Link (idempotent) ---
$existing = (Get-GPInheritance -Target $ou.DistinguishedName).GpoLinks |
  Where-Object { $_.GpoId -eq $gpo.Id.ToString() -or $_.DisplayName -eq $gpo.DisplayName }
if (-not $existing) {
  New-GPLink -Guid $gpo.Id -Target $ou.DistinguishedName -LinkEnabled Yes | Out-Null
  Write-Host 'GPLink created.'
} else {
  Write-Host 'GPLink already present.'
}

# --- Security filtering: pilot group ONLY ---
try {
  Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
} catch {
  Write-Warning ("Authenticated Users permission adjust: " + $_.Exception.Message)
}
# Deny Apply from other broad principals if present (best-effort)
foreach ($broad in @('Domain Computers', 'Everyone')) {
  try {
    Set-GPPermission -Guid $gpo.Id -TargetName $broad -TargetType Group -PermissionLevel None -Replace -ErrorAction SilentlyContinue | Out-Null
  } catch { }
}
Set-GPPermission -Guid $gpo.Id -TargetName $PilotGroup -TargetType Group -PermissionLevel GpoApply -Replace | Out-Null
$perms = Get-GPPermission -Guid $gpo.Id -All | Where-Object { $_.Permission -eq 'GpoApply' }
Write-Host 'GpoApply principals:'
$perms | ForEach-Object { Write-Host ("  APPLY=" + $_.Trustee.Name) }
$applyNames = @($perms | ForEach-Object { $_.Trustee.Name })
if ($applyNames -notcontains $PilotGroup -and ($applyNames | Where-Object { $_ -like "*$PilotGroup*" }).Count -eq 0) {
  throw "Pilot group $PilotGroup does not have GpoApply — aborting."
}
if ($applyNames | Where-Object { $_ -match 'Authenticated Users|Domain Computers|Everyone' }) {
  throw "Broad principal still has GpoApply ($($applyNames -join ', ')). Fix filtering before continuing."
}
Write-Host "Security filtering OK — Apply only on $PilotGroup."

# --- Startup payload (no secrets) ---
New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
$ps1Name = 'Install-QkaAgentFromUnc.ps1'
$cmdName = 'Install-QkaAgentFromUnc.cmd'
$ps1Path = Join-Path $startupDir $ps1Name
$cmdPath = Join-Path $startupDir $cmdName

$ps1 = @"
# QKA Agent MSI bootstrap — NO enrollment secrets. Pilot-group GPO only.
`$ErrorActionPreference = 'Stop'
`$msi = '$MsiUnc'
`$url = '$ServerUrl'
`$tokenDrop = '$TokenDropUnc'
`$logDir = Join-Path `$env:ProgramData 'QKA\DesktopAgent\logs'
New-Item -ItemType Directory -Force -Path `$logDir | Out-Null
`$log = Join-Path `$logDir 'gpo-install.log'
function Write-Log([string]`$m) { Add-Content -Path `$log -Value ("{0} {1}" -f (Get-Date -Format o), `$m) }
# Non-secret path only (token files are ACL-bound per computer on that share).
`$reg = 'HKLM:\SOFTWARE\QKA\DesktopAgent'
New-Item -Path `$reg -Force | Out-Null
New-ItemProperty -Path `$reg -Name 'TokenDropUnc' -Value `$tokenDrop -PropertyType String -Force | Out-Null
if (Get-Service -Name 'QkaDesktopAgent' -ErrorAction SilentlyContinue) { Write-Log 'already_installed'; exit 0 }
if (-not (Test-Path -LiteralPath `$msi)) { Write-Log ("msi_missing " + `$msi); exit 1 }
`$msiLog = Join-Path `$logDir 'msiexec.log'
`$arg = "/i ``"`$msi``" /qn /norestart QKASERVERURL=`$url /L*v ``"`$msiLog``""
Write-Log 'starting_msiexec'
`$p = Start-Process -FilePath msiexec.exe -ArgumentList `$arg -Wait -PassThru
Write-Log ("msiexec_exit=" + `$p.ExitCode)
exit `$p.ExitCode
"@
Set-Content -Path $ps1Path -Value $ps1 -Encoding UTF8

# CMD wrapper so classic Scripts CSE registers reliably (not PowerShell-only CSE).
@(
  '@echo off',
  'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-QkaAgentFromUnc.ps1"',
  'exit /b %ERRORLEVEL%'
) | Set-Content -Path $cmdPath -Encoding ASCII

# scripts.ini for Machine Startup (CMD entry)
$scriptsIni = Join-Path $scriptsDir 'scripts.ini'
@"
[Startup]
0CmdLine=$cmdName
0Parameters=
"@ | Set-Content -Path $scriptsIni -Encoding ASCII

# --- Register Scripts CSE on GPO object + bump computer GPT version ---
# Scripts CSE pair: tool {42B5FAAE-6536-11D2-AE5A-0000F87571E3} CSE {40B6664F-4972-11D1-A7CA-0000F87571E3}
$scriptsExt = '[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]'
$gpoAd = Get-ADObject -Identity $gpoDn -Properties gPCMachineExtensionNames, versionNumber, displayName
$ext = [string]($gpoAd.gPCMachineExtensionNames)
if ($ext -notmatch '40B6664F-4972-11D1-A7CA-0000F87571E3') {
  $newExt = if ([string]::IsNullOrWhiteSpace($ext)) { $scriptsExt } else { ($ext.TrimEnd() + $scriptsExt) }
  Set-ADObject -Identity $gpoDn -Replace @{ gPCMachineExtensionNames = $newExt }
  Write-Host 'gPCMachineExtensionNames updated to include Scripts CSE.'
} else {
  Write-Host 'Scripts CSE already present on gPCMachineExtensionNames.'
}

# GPT.INI: Version = (user << 16) + computer — increment computer (low 16 bits)
$userVer = 0
$compVer = 0
if (Test-Path -LiteralPath $gptIni) {
  $raw = Get-Content -LiteralPath $gptIni -Raw
  if ($raw -match '(?im)^Version\s*=\s*(\d+)') {
    $ver = [int64]$Matches[1]
    $userVer = [int](($ver -shr 16) -band 0xFFFF)
    $compVer = [int]($ver -band 0xFFFF)
  }
}
$compVer = ($compVer + 1) -band 0xFFFF
if ($compVer -eq 0) { $compVer = 1 }
$newVer = ($userVer -shl 16) + $compVer
@"
[General]
Version=$newVer
displayName=$($gpo.DisplayName)
"@ | Set-Content -Path $gptIni -Encoding ASCII

# Mirror version into AD versionNumber (best-effort)
try {
  Set-ADObject -Identity $gpoDn -Replace @{ versionNumber = [int]$newVer }
} catch {
  Write-Warning ("Could not set AD versionNumber (clients still use GPT.INI): " + $_.Exception.Message)
}

Write-Host ("Startup registered: " + $cmdPath)
Write-Host ("GPT.INI Version=" + $newVer + " (computer=" + $compVer + " user=" + $userVer + ")")
Write-Host 'VERIFY on client after link+membership: gpresult /r  and  Event Log → GroupPolicy'
Write-Host 'Tokens must NEVER be placed in this SYSVOL startup script — use UNC ACL token-drop auto-enrollment.'
