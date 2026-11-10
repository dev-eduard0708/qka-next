<#
.SYNOPSIS
  Grant least-privilege Write Members on QKA-Agent-Pilot-Computers only.

.DESCRIPTION
  Backs up the group's AD security descriptor (SDDL), then adds a single Allow ACE:
  WriteProperty on the "member" attribute for one identity.

  Does NOT:
  - Change group membership
  - Grant Domain Admin / OU-wide rights
  - Modify unrelated groups, users, GPOs, or OUs

  Run elevated as an identity that can WriteDacl on this group (e.g. Domain Admin).
  Do not run without explicit administrator approval (-IConfirmGrantWriteMembers).

.EXAMPLE
  # Dry prep / review only (default):
  .\Grant-QkaPilotGroupWriteMembers.ps1 -Identity 'QEHC\eduard'

  # After approval (elevated):
  .\Grant-QkaPilotGroupWriteMembers.ps1 -Identity 'QEHC\eduard' -IConfirmGrantWriteMembers
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$Identity,

  [string]$PilotGroup = 'QKA-Agent-Pilot-Computers',

  [string]$BackupDirectory = 'C:\QehcAI\QKA-Next\scripts\gpo-agent-deploy\acl-backups',

  [switch]$IConfirmGrantWriteMembers
)

$ErrorActionPreference = 'Stop'

# Active Directory schema GUID for attribute "member"
$MemberAttributeGuid = [guid]'bf9679c0-0de6-11d0-a285-00aa003049e2'

Import-Module ActiveDirectory -ErrorAction Stop

$grp = Get-ADGroup -Identity $PilotGroup -ErrorAction Stop
if ($grp.Name -ne 'QKA-Agent-Pilot-Computers' -and $PilotGroup -ne 'QKA-Agent-Pilot-Computers') {
  throw "Refusing to modify unexpected group '$($grp.Name)'."
}
if ($grp.Name -ne 'QKA-Agent-Pilot-Computers') {
  throw "Hard-locked target is QKA-Agent-Pilot-Computers; resolved '$($grp.Name)'."
}

$dn = $grp.DistinguishedName
$adPath = "AD:$dn"
$acl = Get-Acl -Path $adPath
$membersBefore = @(Get-ADGroupMember -Identity $grp | ForEach-Object { $_.SID.Value })

Write-Host "GROUP_NAME=$($grp.Name)"
Write-Host "GROUP_DN=$dn"
Write-Host "IDENTITY=$Identity"
Write-Host "MEMBER_COUNT_BEFORE=$($membersBefore.Count)"
Write-Host "MEMBER_SIDS_BEFORE=$($membersBefore -join ',')"

# Resolve trustee SID (fail closed).
$nt = New-Object System.Security.Principal.NTAccount($Identity)
try {
  $trusteeSid = $nt.Translate([System.Security.Principal.SecurityIdentifier])
} catch {
  throw "Cannot resolve identity '$Identity' to a SID."
}
Write-Host "TRUSTEE_SID=$($trusteeSid.Value)"

# Detect existing Allow WriteProperty(member) for this trustee.
$wp = [System.DirectoryServices.ActiveDirectoryRights]::WriteProperty
$existing = @(
  $acl.Access | Where-Object {
    try {
      $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier])
    } catch {
      return $false
    }
    $sid.Value -eq $trusteeSid.Value -and
    $_.AccessControlType -eq 'Allow' -and
    $_.ObjectType -eq $MemberAttributeGuid -and
    (([int]$_.ActiveDirectoryRights -band [int]$wp) -ne 0)
  }
)
if ($existing.Count -gt 0) {
  Write-Host 'CURRENT_WRITE_MEMBERS_PERMISSION=PRESENT'
  Write-Host 'DELEGATION_REQUIRED=NO'
  Write-Host 'No ACL change needed.'
  return
}

Write-Host 'CURRENT_WRITE_MEMBERS_PERMISSION=MISSING'
Write-Host 'DELEGATION_REQUIRED=YES'

if (-not $IConfirmGrantWriteMembers) {
  Write-Host ''
  Write-Host 'Dry run only. Re-run elevated with -IConfirmGrantWriteMembers after approval.'
  Write-Host "ELEVATED_COMMAND_TO_RUN=powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Identity '$Identity' -IConfirmGrantWriteMembers"
  return
}

# Backup SDDL before any change.
New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $BackupDirectory ("QKA-Agent-Pilot-Computers.ntSecurityDescriptor.$stamp.sddl.txt")
$metaPath = Join-Path $BackupDirectory ("QKA-Agent-Pilot-Computers.ntSecurityDescriptor.$stamp.meta.txt")
$sddl = $acl.Sddl
Set-Content -Path $backupPath -Value $sddl -Encoding UTF8
@(
  "timestampUtc=$((Get-Date).ToUniversalTime().ToString('o'))"
  "groupDn=$dn"
  "identity=$Identity"
  "trusteeSid=$($trusteeSid.Value)"
  "memberCount=$($membersBefore.Count)"
  "memberSids=$($membersBefore -join ',')"
  "backupSddlFile=$backupPath"
  "action=grant_writeproperty_member_only"
) | Set-Content -Path $metaPath -Encoding UTF8
Write-Host "ACL_BACKUP_SDDL=$backupPath"
Write-Host "ACL_BACKUP_META=$metaPath"

$rule = New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
  $trusteeSid,
  [System.DirectoryServices.ActiveDirectoryRights]::WriteProperty,
  [System.Security.AccessControl.AccessControlType]::Allow,
  $MemberAttributeGuid,
  [System.DirectoryServices.ActiveDirectorySecurityInheritance]::None
)
$acl.AddAccessRule($rule)
Set-Acl -Path $adPath -AclObject $acl

# Verify ACE present; membership unchanged.
$aclAfter = Get-Acl -Path $adPath
$verifyAce = @(
  $aclAfter.Access | Where-Object {
    try {
      $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier])
    } catch {
      return $false
    }
    $sid.Value -eq $trusteeSid.Value -and
    $_.AccessControlType -eq 'Allow' -and
    $_.ObjectType -eq $MemberAttributeGuid -and
    (([int]$_.ActiveDirectoryRights -band [int]$wp) -ne 0)
  }
)
$membersAfter = @(Get-ADGroupMember -Identity $grp | ForEach-Object { $_.SID.Value })
$membersUnchanged = ($membersBefore.Count -eq $membersAfter.Count) -and
  (-not (Compare-Object $membersBefore $membersAfter))

if ($verifyAce.Count -lt 1) {
  throw "Grant reported applied but WriteProperty(member) ACE for $Identity was not found. Restore from $backupPath if needed."
}
if (-not $membersUnchanged) {
  throw "FATAL: group membership changed unexpectedly. Investigate immediately. Backup=$backupPath"
}

Write-Host 'GRANT_RESULT=OK'
Write-Host 'SECURITY_SCOPE=WriteProperty on attribute member of CN=QKA-Agent-Pilot-Computers only'
Write-Host "MEMBER_COUNT_AFTER=$($membersAfter.Count)"
Write-Host 'MEMBERS_UNCHANGED=YES'
Write-Host 'READY_FOR_LIVE_TEST=YES'
