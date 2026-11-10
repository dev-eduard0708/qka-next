<#
.SYNOPSIS
  Link existing QKA Agent GPO, scope to pilot computer group, register Machine Startup script properly.

.DESCRIPTION
  Uses GPO GUID {73E8998A-A3B3-435F-9219-419C331BA2D4}. Does NOT recreate the GPO.
  Requires RSAT GroupPolicy on an authorized AD management host.

  - Links GPO to a pilot OU only (never domain root)
  - Security filtering: GpoApply ONLY for QKA-Agent-Pilot-Computers; Authenticated Users → GpoRead
  - Install trigger is a GPO Scheduled Task (SYSTEM, about every 2 minutes), not a
    startup script. No workstation reboot is required after the task is applied.
  - Removes this product's Machine Startup script entry and preserves unrelated scripts.
  - Startup/task script installs MSI from UNC with QKASERVERURL only — NO enrollment tokens in SYSVOL
  - Authorization gate: a per-SID *.tok is required AND the API must confirm the token is
    unexpired and the deployment job is not cancelled. Linking the GPO or pilot-group
    membership alone never installs.

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
$pdc = (Get-ADDomain).PDCEmulator
# Write the authoritative copy on the PDC. SYSVOL DFSR (not a manual copy) distributes it.
$sysvolRoot = "\\$pdc\SYSVOL\qehc.edu.sa\Policies\{$guidBare}"
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

# --- Backup existing SYSVOL startup artifacts before rewrite ---
$backupRoot = Join-Path $env:TEMP ("qka-gpo-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
if (Test-Path -LiteralPath $sysvolRoot) {
  Copy-Item -LiteralPath $sysvolRoot -Destination (Join-Path $backupRoot 'Policies') -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host ("GPO_BACKUP=" + $backupRoot)
}

# --- Install payload (no secrets). Scheduled task calls this; startup does not. ---
New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
$ps1Name = 'Install-QkaAgentFromUnc.ps1'
$ps1Path = Join-Path $startupDir $ps1Name
$scriptSource = Join-Path $PSScriptRoot 'Install-QkaAgentFromUnc.template.ps1'
if (-not (Test-Path -LiteralPath $scriptSource)) {
  throw "Missing install template: $scriptSource"
}
$clientScriptUnc = "\\qehc.edu.sa\SYSVOL\qehc.edu.sa\Policies\{$guidBare}\Machine\Scripts\Startup\$ps1Name"
$rendered = Get-Content -LiteralPath $scriptSource -Raw
$rendered = $rendered.Replace('@@MSI_UNC@@', $MsiUnc).Replace('@@SERVER_URL@@', $ServerUrl).Replace('@@TOKEN_DROP@@', $TokenDropUnc)
Set-Content -LiteralPath $ps1Path -Value $rendered -Encoding UTF8
Write-Host ("Install script written: " + $ps1Path)

# Remove this product's Machine Startup entry. Keep every unrelated startup script.
$scriptsIni = Join-Path $scriptsDir 'scripts.ini'
$startupEntries = New-Object System.Collections.Generic.List[object]
$otherSections = New-Object System.Collections.Generic.List[string]
if (Test-Path -LiteralPath $scriptsIni) {
  $rawIni = @(Get-Content -LiteralPath $scriptsIni)
  $section = ''
  $pendingCmd = $null
  $bucket = New-Object System.Collections.Generic.List[string]
  foreach ($line in $rawIni) {
    if ($line -match '^\s*\[(.+)\]\s*$') {
      if ($section -and $section -ne 'Startup') {
        [void]$otherSections.Add("[$section]")
        foreach ($keptLine in $bucket) { [void]$otherSections.Add($keptLine) }
      }
      $section = $Matches[1].Trim()
      $bucket = New-Object System.Collections.Generic.List[string]
      $pendingCmd = $null
      continue
    }
    if ($section -eq 'Startup') {
      if ($line -match '^\s*\d+CmdLine=(.+)\s*$') { $pendingCmd = $Matches[1].Trim() }
      elseif ($line -match '^\s*\d+Parameters=(.*)\s*$' -and $null -ne $pendingCmd) {
        if ($pendingCmd -notmatch 'Install-QkaAgentFromUnc') {
          $startupEntries.Add([pscustomobject]@{ Cmd = $pendingCmd; Params = $Matches[1] })
        }
        $pendingCmd = $null
      }
    } else {
      [void]$bucket.Add($line)
    }
  }
  if ($section -and $section -ne 'Startup') {
    [void]$otherSections.Add("[$section]")
    foreach ($keptLine in $bucket) { [void]$otherSections.Add($keptLine) }
  }
}
$iniLines = New-Object System.Collections.Generic.List[string]
if ($startupEntries.Count -gt 0) {
  [void]$iniLines.Add('[Startup]')
  for ($i = 0; $i -lt $startupEntries.Count; $i++) {
    [void]$iniLines.Add(('{0}CmdLine={1}' -f $i, $startupEntries[$i].Cmd))
    [void]$iniLines.Add(('{0}Parameters={1}' -f $i, $startupEntries[$i].Params))
  }
}
foreach ($line in $otherSections) { [void]$iniLines.Add($line) }
if ($iniLines.Count -eq 0) { [void]$iniLines.Add('; no machine startup scripts') }
$iniLines | Set-Content -Path $scriptsIni -Encoding ASCII
# Drop the old CMD wrapper so nothing at startup launches the installer.
$oldCmd = Join-Path $startupDir 'Install-QkaAgentFromUnc.cmd'
if (Test-Path -LiteralPath $oldCmd) { Remove-Item -LiteralPath $oldCmd -Force }
Write-Host ("Startup entries preserved (excluding QKA): " + $startupEntries.Count)

# --- GPO Scheduled Task (SYSTEM, every 2 minutes). Not a startup script. ---
$taskDir = Join-Path $sysvolRoot 'Machine\Preferences\ScheduledTasks'
New-Item -ItemType Directory -Force -Path $taskDir | Out-Null
$taskTemplate = Join-Path $PSScriptRoot 'ScheduledTasks.template.xml'
if (-not (Test-Path -LiteralPath $taskTemplate)) { throw "Missing scheduled task template: $taskTemplate" }
$taskUidPath = Join-Path $taskDir 'qka-task-uid.txt'
if (Test-Path -LiteralPath $taskUidPath) {
  $taskUid = (Get-Content -LiteralPath $taskUidPath -Raw).Trim()
} else {
  $taskUid = [guid]::NewGuid().ToString()
  Set-Content -LiteralPath $taskUidPath -Value $taskUid -Encoding ASCII
}
$changed = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
$taskXml = (Get-Content -LiteralPath $taskTemplate -Raw).Replace('@@CHANGED@@', $changed).Replace('@@UID@@', $taskUid).Replace('@@SCRIPT_UNC@@', $clientScriptUnc)
$taskXmlPath = Join-Path $taskDir 'ScheduledTasks.xml'
Set-Content -LiteralPath $taskXmlPath -Value $taskXml -Encoding UTF8
Write-Host ("Scheduled task XML: " + $taskXmlPath)

# Scheduled Tasks CSE must be first in the [{CSE}{tool}] pair (GPSVC walks leading GUIDs).
# Mirror working GPP Services order; tool-first [{CAB54552}{AADCED64}] is never invoked.
$schedExt = '[{00000000-0000-0000-0000-000000000000}{CAB54552-DE0E-4C25-A188-0381EF229E1A}][{AADCED64-746C-4633-A97C-D61349046527}{CAB54552-DE0E-4C25-A188-0381EF229E1A}]'
$gpoAd = Get-ADObject -Identity $gpoDn -Properties gPCMachineExtensionNames, versionNumber, displayName
$ext = [string]($gpoAd.gPCMachineExtensionNames)
$hasCseFirst = $ext -match '\[\{AADCED64-746C-4633-A97C-D61349046527\}\{CAB54552-DE0E-4C25-A188-0381EF229E1A\}\]'
if (-not $hasCseFirst) {
  # Drop any prior tool-first / incomplete Scheduled Tasks pairs, then append CSE-first form.
  $cleaned = [regex]::Replace($ext, '\[\{(?:00000000-0000-0000-0000-000000000000|CAB54552-DE0E-4C25-A188-0381EF229E1A|AADCED64-746C-4633-A97C-D61349046527)\}\{(?:00000000-0000-0000-0000-000000000000|CAB54552-DE0E-4C25-A188-0381EF229E1A|AADCED64-746C-4633-A97C-D61349046527)\}\]', '')
  $newExt = if ([string]::IsNullOrWhiteSpace($cleaned)) { $schedExt } else { ($cleaned.TrimEnd() + $schedExt) }
  Set-ADObject -Identity $gpoDn -Replace @{ gPCMachineExtensionNames = $newExt }
  Write-Host 'gPCMachineExtensionNames updated with CSE-first Scheduled Tasks extension pairs.'
} else {
  Write-Host 'Scheduled Tasks CSE (CSE-first) already present on gPCMachineExtensionNames.'
}

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
try {
  Set-ADObject -Identity $gpoDn -Replace @{ versionNumber = [int]$newVer }
} catch {
  Write-Warning ("Could not set AD versionNumber (clients still use GPT.INI): " + $_.Exception.Message)
}

Write-Host ("GPT.INI Version=" + $newVer + " (computer=" + $compVer + " user=" + $userVer + ")")
Write-Host 'INSTALL_TRIGGER=scheduled_task_2min_system'
Write-Host 'STARTUP_SCRIPT=removed'
Write-Host 'Tokens must NEVER be placed in SYSVOL — the task calls the API install-authorization gate.'
