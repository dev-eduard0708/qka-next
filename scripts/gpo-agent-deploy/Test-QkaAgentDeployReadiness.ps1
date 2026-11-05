<#
.SYNOPSIS
  Read-only readiness checks for remote deploy (does not link GPO, add members, or install).
#>
[CmdletBinding()]
param(
  [string]$MsiUnc = '',
  [string]$TokenDropUnc = '',
  [string]$PilotGroup = 'QKA-Agent-Pilot-Computers',
  [string]$GpoGuid = '{73E8998A-A3B3-435F-9219-419C331BA2D4}'
)

$ErrorActionPreference = 'Continue'
Import-Module ActiveDirectory -ErrorAction SilentlyContinue

Write-Host '=== MSI local ==='
Write-Host ("local_msi=" + (Test-Path 'C:\QehcAI\QKA-Next\agent\dist\QKA-Agent.msi'))
if ($MsiUnc) {
  Write-Host ("msi_unc_reachable=" + (Test-Path -LiteralPath $MsiUnc) + " path=" + $MsiUnc)
  if (Test-Path -LiteralPath $MsiUnc) {
    $acl = Get-Acl -LiteralPath (Split-Path $MsiUnc)
    $acl.Access | Select-Object IdentityReference, FileSystemRights, AccessControlType |
      Format-Table -AutoSize | Out-String | Write-Host
  }
}

Write-Host '=== Token drop ==='
if ($TokenDropUnc) {
  Write-Host ("token_drop_reachable=" + (Test-Path -LiteralPath $TokenDropUnc) + " path=" + $TokenDropUnc)
  if (Test-Path -LiteralPath $TokenDropUnc) {
    $acl = Get-Acl -LiteralPath $TokenDropUnc
    $acl.Access | Select-Object IdentityReference, FileSystemRights, AccessControlType |
      Format-Table -AutoSize | Out-String | Write-Host
  }
}

Write-Host '=== GPO / group (must stay unlinked/empty until approval) ==='
try {
  $dn = "CN=$GpoGuid,CN=Policies,CN=System,DC=qehc,DC=edu,DC=sa"
  $gpo = Get-ADObject -Identity $dn -Properties displayName, gPCMachineExtensionNames, versionNumber
  Write-Host ("gpo=" + $gpo.displayName + " ver=" + $gpo.versionNumber)
  Write-Host ("extensions=" + $gpo.gPCMachineExtensionNames)
  $links = Get-ADOrganizationalUnit -Filter * -Properties gPLink |
    Where-Object { $_.gPLink -and $_.gPLink -like ("*" + $GpoGuid.Trim('{}') + "*") }
  if ($links) { $links | ForEach-Object { Write-Host ("LINK=" + $_.DistinguishedName) } }
  else { Write-Host 'LINK=none (expected until approval)' }
  $grp = Get-ADGroup $PilotGroup
  $n = @(Get-ADGroupMember $grp -ErrorAction SilentlyContinue).Count
  Write-Host ("pilot_members=" + $n + " (expected 0 until approval)")
} catch {
  Write-Host ("AD_ERR=" + $_.Exception.Message)
}

Write-Host '=== GPMC on this host ==='
Write-Host ("gpmc=" + [bool](Get-Module -ListAvailable GroupPolicy))
