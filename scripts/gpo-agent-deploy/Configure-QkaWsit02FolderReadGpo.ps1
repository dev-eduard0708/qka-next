<#
.SYNOPSIS
  Create/link a per-workstation GPO that grants QkaDesktopAgent service SID Read/List on one employee's approved folders.

.DESCRIPTION
  - Default targets WSIT02 / Eduard (original pilot). Pass -ComputerName/-ProfileRoot/-GpoName for others (e.g. WSIT05).
  - Linked to WorkStations OU
  - GpoApply: that computer only (Authenticated Users → GpoRead)
  - GPP Scheduled Task (SYSTEM) runs Grant-QkaAgentProfileFolderRead.ps1 from SYSVOL with -ProfileRoot
  - Does NOT change agent service account, MSI, enrollment, or other computers
  - Does NOT grant NETWORK SERVICE broadly — uses NT SERVICE\QkaDesktopAgent SID

  DEFAULT = dry-run. Live create/link requires -IConfirmFolderAclGpo.
#>
[CmdletBinding()]
param(
  [string]$GpoName = 'QKA-WSIT02-Agent-Folder-Read',
  [string]$TargetOuDn = 'OU=WorkStations,OU=IT,OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa',
  [string]$ComputerName = 'WSIT02',
  [string]$ProfileRoot = 'C:\Users\eduard',
  [switch]$IConfirmFolderAclGpo
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmFolderAclGpo) {
  throw "Pass -IConfirmFolderAclGpo after reviewing scope ($ComputerName + $ProfileRoot\{Desktop,Documents,Downloads} only)."
}
if (-not (Get-Module -ListAvailable GroupPolicy)) {
  throw 'RSAT GroupPolicy module required.'
}
# GroupPolicy module aliases (Get/Set-GPPermissions) can collide in long-lived hosts.
Get-Alias -Name Get-GPPermissions,Set-GPPermissions -ErrorAction SilentlyContinue |
  ForEach-Object { Remove-Item -LiteralPath ("Alias:" + $_.Name) -Force -ErrorAction SilentlyContinue }
Import-Module GroupPolicy -Force -DisableNameChecking -ErrorAction Stop
Import-Module ActiveDirectory -Force -ErrorAction Stop

$ou = Get-ADOrganizationalUnit -Identity $TargetOuDn -ErrorAction Stop
$computer = Get-ADComputer -Identity $ComputerName -ErrorAction Stop
$pdc = (Get-ADDomain).PDCEmulator

$gpo = Get-GPO -Name $GpoName -ErrorAction SilentlyContinue
if (-not $gpo) {
  $gpo = New-GPO -Name $GpoName -Comment ("$ComputerName-only Read/List for NT SERVICE\QkaDesktopAgent on $ProfileRoot approved folders")
  Write-Host ("GPO_CREATED=" + $gpo.Id)
} else {
  Write-Host ("GPO_EXISTS=" + $gpo.Id)
}
$guidBare = $gpo.Id.ToString().Trim('{}')
$gpoDn = "CN={$guidBare},CN=Policies,CN=System,DC=qehc,DC=edu,DC=sa"
$sysvolRoot = "\\$pdc\SYSVOL\qehc.edu.sa\Policies\{$guidBare}"

# Link (idempotent)
$existing = (Get-GPInheritance -Target $ou.DistinguishedName).GpoLinks |
  Where-Object { $_.GpoId -eq $gpo.Id.ToString() -or $_.DisplayName -eq $gpo.DisplayName }
if (-not $existing) {
  New-GPLink -Guid $gpo.Id -Target $ou.DistinguishedName -LinkEnabled Yes | Out-Null
  Write-Host 'GPLink created.'
} else {
  Write-Host 'GPLink already present.'
}

# Security filtering: this computer only
try {
  Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
} catch {
  Write-Warning ("Authenticated Users permission adjust: " + $_.Exception.Message)
}
foreach ($broad in @('Domain Computers', 'Everyone', 'QKA-Agent-Pilot-Computers')) {
  try {
    Set-GPPermission -Guid $gpo.Id -TargetName $broad -TargetType Group -PermissionLevel None -Replace -ErrorAction SilentlyContinue | Out-Null
  } catch { }
}
Set-GPPermission -Guid $gpo.Id -TargetName $computer.Name -TargetType Computer -PermissionLevel GpoApply -Replace | Out-Null
$perms = Get-GPPermission -Guid $gpo.Id -All | Where-Object { $_.Permission -eq 'GpoApply' }
Write-Host 'GpoApply principals:'
$perms | ForEach-Object { Write-Host ("  APPLY=" + $_.Trustee.Name) }
$applyNames = @($perms | ForEach-Object { $_.Trustee.Name })
if ($applyNames -notcontains $computer.Name -and ($applyNames | Where-Object { $_ -like "*$ComputerName*" }).Count -eq 0) {
  throw "Computer $ComputerName does not have GpoApply — aborting."
}
if ($applyNames | Where-Object { $_ -match 'Authenticated Users|Domain Computers|Everyone' }) {
  throw "Broad principal still has GpoApply ($($applyNames -join ', '))."
}

# Publish grant script into GPO SYSVOL (no secrets)
$scriptsDir = Join-Path $sysvolRoot 'Machine\Scripts\Startup'
New-Item -ItemType Directory -Force -Path $scriptsDir | Out-Null
$src = Join-Path $PSScriptRoot 'Grant-QkaAgentProfileFolderRead.ps1'
if (-not (Test-Path -LiteralPath $src)) { throw "Missing grant script: $src" }
$dstName = 'Grant-QkaAgentProfileFolderRead.ps1'
$dst = Join-Path $scriptsDir $dstName
Copy-Item -LiteralPath $src -Destination $dst -Force
# Ensure ProfileRoot default matches this pilot (script already defaults to C:\Users\eduard)
$clientScriptUnc = "\\qehc.edu.sa\SYSVOL\qehc.edu.sa\Policies\{$guidBare}\Machine\Scripts\Startup\$dstName"
Write-Host ("GRANT_SCRIPT=" + $dst)
Write-Host ("GRANT_SCRIPT_UNC=" + $clientScriptUnc)
Write-Host ("PROFILE_ROOT=" + $ProfileRoot)

# GPP Scheduled Task with computer filter
$taskDir = Join-Path $sysvolRoot 'Machine\Preferences\ScheduledTasks'
New-Item -ItemType Directory -Force -Path $taskDir | Out-Null
$taskTemplate = Join-Path $PSScriptRoot 'ScheduledTasks.FolderRead.template.xml'
if (-not (Test-Path -LiteralPath $taskTemplate)) { throw "Missing template: $taskTemplate" }
$taskUidPath = Join-Path $taskDir 'qka-folder-read-uid.txt'
if (Test-Path -LiteralPath $taskUidPath) {
  $taskUid = (Get-Content -LiteralPath $taskUidPath -Raw).Trim()
} else {
  $taskUid = [guid]::NewGuid().ToString()
  Set-Content -LiteralPath $taskUidPath -Value $taskUid -Encoding ASCII
}
$changed = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
$computerDns = ($computer.DNSHostName)
if (-not $computerDns) { $computerDns = ($ComputerName.ToLower() + '.qehc.edu.sa') }
$taskXml = (Get-Content -LiteralPath $taskTemplate -Raw).
  Replace('@@CHANGED@@', $changed).
  Replace('@@UID@@', $taskUid).
  Replace('@@SCRIPT_UNC@@', $clientScriptUnc).
  Replace('@@PROFILE_ROOT@@', $ProfileRoot).
  Replace('@@COMPUTER@@', $ComputerName).
  Replace('@@COMPUTER_DNS@@', $computerDns.ToLower())
$taskXmlPath = Join-Path $taskDir 'ScheduledTasks.xml'
Set-Content -LiteralPath $taskXmlPath -Value $taskXml -Encoding UTF8
Write-Host ("ScheduledTasks=" + $taskXmlPath)

# Register Scheduled Tasks CSE (CSE-first pair)
$schedExt = '[{00000000-0000-0000-0000-000000000000}{CAB54552-DE0E-4C25-A188-0381EF229E1A}][{AADCED64-746C-4633-A97C-D61349046527}{CAB54552-DE0E-4C25-A188-0381EF229E1A}]'
$gpoAd = $null
for ($i = 0; $i -lt 12; $i++) {
  try {
    $gpoAd = Get-ADObject -Identity $gpoDn -Properties gPCMachineExtensionNames, versionNumber, displayName -Server $pdc -ErrorAction Stop
    break
  } catch {
    Start-Sleep -Seconds 2
  }
}
if (-not $gpoAd) {
  throw "GPO AD object not found yet: $gpoDn (check replication on $pdc)"
}
$ext = [string]($gpoAd.gPCMachineExtensionNames)
$hasCseFirst = $ext -match '\[\{AADCED64-746C-4633-A97C-D61349046527\}\{CAB54552-DE0E-4C25-A188-0381EF229E1A\}\]'
if (-not $hasCseFirst) {
  $cleaned = [regex]::Replace($ext, '\[\{(?:00000000-0000-0000-0000-000000000000|CAB54552-DE0E-4C25-A188-0381EF229E1A|AADCED64-746C-4633-A97C-D61349046527)\}\{(?:00000000-0000-0000-0000-000000000000|CAB54552-DE0E-4C25-A188-0381EF229E1A|AADCED64-746C-4633-A97C-D61349046527)\}\]', '')
  $newExt = if ([string]::IsNullOrWhiteSpace($cleaned)) { $schedExt } else { ($cleaned.TrimEnd() + $schedExt) }
  Set-ADObject -Identity $gpoDn -Replace @{ gPCMachineExtensionNames = $newExt }
  Write-Host 'gPCMachineExtensionNames updated (Scheduled Tasks CSE-first).'
} else {
  Write-Host 'Scheduled Tasks CSE already present.'
}

$gptIni = Join-Path $sysvolRoot 'GPT.INI'
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
  Write-Warning ("Could not set AD versionNumber: " + $_.Exception.Message)
}

Write-Host ("GPT.INI Version=" + $newVer)
Write-Host "SCOPE=computer=$ComputerName folders=$ProfileRoot\{Desktop,Documents,Downloads}"
Write-Host 'PRINCIPAL=NT SERVICE\QkaDesktopAgent (service SID; not broad NETWORK SERVICE)'
Write-Host 'RIGHTS=ReadAndExecute (List/Read/Traverse only)'
Write-Host 'DONE=Configure-QkaWsit02FolderReadGpo'
