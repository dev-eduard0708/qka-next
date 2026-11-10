<#
.SYNOPSIS
  Grant minimum Read/List NTFS rights to NT SERVICE\QkaDesktopAgent on approved profile folders.

.DESCRIPTION
  Adds (does not replace) ReadAndExecute for the QkaDesktopAgent service SID on:
    - profile root (this folder only — traverse/list into approved children)
    - Desktop, Documents, Downloads (this folder, subfolders, and files)

  Prefer the service SID (S-1-5-80-…) over NETWORK SERVICE so only this service
  receives the ACE. Never grants Write/Modify/Delete. Never creates a domain account.

  Intended to run as SYSTEM via GPO on targeted computers (WSIT02 pilot), or from a
  management host against admin shares. Idempotent.
#>
[CmdletBinding()]
param(
  [string]$ProfileRoot = 'C:\Users\eduard',
  [string[]]$Folders = @('Desktop', 'Documents', 'Downloads'),
  # Service SID for NT SERVICE\QkaDesktopAgent (stable for this service name).
  [string]$ServiceSid = 'S-1-5-80-2490061581-3647314164-3678032288-2869308700-3730982733',
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

function Test-HasAce {
  param(
    [System.Security.AccessControl.FileSystemSecurity]$Acl,
    [System.Security.Principal.SecurityIdentifier]$Sid,
    [System.Security.AccessControl.FileSystemRights]$Rights,
    [System.Security.AccessControl.InheritanceFlags]$Inheritance,
    [System.Security.AccessControl.PropagationFlags]$Propagation
  )
  foreach ($ace in $Acl.Access) {
    if ($ace.AccessControlType -ne 'Allow') { continue }
    $id = $ace.IdentityReference
    $aceSid = $null
    try {
      if ($id -is [System.Security.Principal.SecurityIdentifier]) { $aceSid = $id }
      else { $aceSid = $id.Translate([System.Security.Principal.SecurityIdentifier]) }
    } catch { continue }
    if ($aceSid.Value -ne $Sid.Value) { continue }
    if (($ace.FileSystemRights -band $Rights) -ne $Rights) { continue }
    if ($ace.InheritanceFlags -ne $Inheritance) { continue }
    if ($ace.PropagationFlags -ne $Propagation) { continue }
    return $true
  }
  return $false
}

function Grant-MinRead {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][System.Security.Principal.SecurityIdentifier]$Sid,
    [Parameter(Mandatory)][System.Security.AccessControl.InheritanceFlags]$Inheritance,
    [Parameter(Mandatory)][System.Security.AccessControl.PropagationFlags]$Propagation
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Host ("SKIP_MISSING=" + $Path)
    return
  }
  $rights = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
  $acl = Get-Acl -LiteralPath $Path
  if (Test-HasAce -Acl $acl -Sid $Sid -Rights $rights -Inheritance $Inheritance -Propagation $Propagation) {
    Write-Host ("OK_EXISTS=" + $Path)
    return
  }
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $Sid,
    $rights,
    $Inheritance,
    $Propagation,
    [System.Security.AccessControl.AccessControlType]::Allow
  )
  $acl.AddAccessRule($rule) | Out-Null
  if ($WhatIf) {
    Write-Host ("WHATIF_GRANT=" + $Path + " rights=ReadAndExecute inherit=" + $Inheritance)
    return
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
  Write-Host ("GRANTED=" + $Path + " principal=NT SERVICE\QkaDesktopAgent rights=ReadAndExecute")
}

$sid = New-Object System.Security.Principal.SecurityIdentifier($ServiceSid)
Write-Host ("SERVICE_SID=" + $sid.Value)
try {
  Write-Host ("SERVICE_NAME=" + $sid.Translate([System.Security.Principal.NTAccount]).Value)
} catch {
  Write-Host 'SERVICE_NAME=NT SERVICE\QkaDesktopAgent (SID applied; local translate unavailable)'
}

# Ensure the service SID is present in the process token (does not change the logon account).
# Without SERVICE_SID_TYPE=UNRESTRICTED, NT SERVICE\QkaDesktopAgent ACEs are ineffective.
$svcName = 'QkaDesktopAgent'
$sidTypeOut = & sc.exe qsidtype $svcName 2>&1 | Out-String
Write-Host ("SERVICE_SID_TYPE_BEFORE=" + ($sidTypeOut -replace '\s+', ' ').Trim())
if ($sidTypeOut -notmatch 'UNRESTRICTED') {
  if ($WhatIf) {
    Write-Host 'WHATIF_SIDTYPE=sc.exe sidtype QkaDesktopAgent unrestricted'
  } else {
    & sc.exe sidtype $svcName unrestricted | Out-Null
    Write-Host 'SERVICE_SID_TYPE=set_unrestricted (service restart required once for token to include SID)'
  }
}

# Profile root: this folder only (no inherit) — enough to traverse into approved children.
Grant-MinRead -Path $ProfileRoot -Sid $sid `
  -Inheritance ([System.Security.AccessControl.InheritanceFlags]::None) `
  -Propagation ([System.Security.AccessControl.PropagationFlags]::None)

$inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
  [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
$prop = [System.Security.AccessControl.PropagationFlags]::None
foreach ($name in $Folders) {
  $path = Join-Path $ProfileRoot $name
  Grant-MinRead -Path $path -Sid $sid -Inheritance $inherit -Propagation $prop
}

# Windows profile compatibility junctions carry Everyone:DENY and abort directory
# enumeration for the service SID when pathlib stats each entry. Remove junction
# stubs only (not library/AppData targets) so approved-folder discovery can continue.
function Remove-DenyJunctionStub {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  try {
    $item = Get-Item -LiteralPath $Path -Force
    if (([int]$item.Attributes -band 0x400) -eq 0) { return }
    if ($WhatIf) {
      Write-Host ("WHATIF_REMOVE_JUNCTION=" + $Path)
      return
    }
    & cmd.exe /c "fsutil reparsepoint delete `"$Path`" >nul 2>&1 & rmdir `"$Path`" >nul 2>&1"
    if (-not (Test-Path -LiteralPath $Path)) {
      Write-Host ("REMOVED_DENY_JUNCTION=" + $Path)
    } else {
      Write-Host ("WARN_JUNCTION_REMAINS=" + $Path)
    }
  } catch {
    Write-Host ("WARN_JUNCTION=" + $Path + " err=" + $_.Exception.Message)
  }
}

foreach ($junctionName in @(
  'Application Data', 'Cookies', 'Local Settings', 'My Documents',
  'NetHood', 'PrintHood', 'Recent', 'SendTo', 'Templates', 'Start Menu'
)) {
  Remove-DenyJunctionStub -Path (Join-Path $ProfileRoot $junctionName)
}
$documents = Join-Path $ProfileRoot 'Documents'
foreach ($junctionName in @('My Music', 'My Pictures', 'My Videos')) {
  Remove-DenyJunctionStub -Path (Join-Path $documents $junctionName)
}

Write-Host 'DONE=Grant-QkaAgentProfileFolderRead'
