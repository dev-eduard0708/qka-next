<#
.SYNOPSIS
  Add exactly one approved computer to QKA-Agent-Pilot-Computers.

.DESCRIPTION
  Never targets an OU. Requires explicit computer name + confirmation.
  Does not install software. Does not issue enrollment tokens.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$ComputerName,

  [string]$PilotGroup = 'QKA-Agent-Pilot-Computers',
  [switch]$IConfirmAddPilotComputer
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmAddPilotComputer) {
  throw 'Pass -IConfirmAddPilotComputer after confirming this is YOUR approved pilot PC only.'
}
Import-Module ActiveDirectory -ErrorAction Stop
$pc = Get-ADComputer -Identity $ComputerName -ErrorAction Stop
$grp = Get-ADGroup -Identity $PilotGroup -ErrorAction Stop
$already = @(Get-ADGroupMember -Identity $grp | Where-Object { $_.SID -eq $pc.SID })
if ($already.Count -gt 0) {
  Write-Host ("ALREADY_MEMBER=" + $pc.DNSHostName)
  return
}
Add-ADGroupMember -Identity $grp -Members $pc
Write-Host ("ADDED=" + $pc.Name + " DN=" + $pc.DistinguishedName + " to " + $grp.Name)
Write-Host 'Next: gpupdate /force on the pilot PC after GPO is linked+filtered; then Deploy in QKA UI + Provision-MachineEnrollmentToken.ps1'
