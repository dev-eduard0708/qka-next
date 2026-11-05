<#
.SYNOPSIS
  Deliver a one-time machineEnrollmentToken to HKLM on ONE computer (out-of-band).

.DESCRIPTION
  Tokens must NEVER be placed in MSI, GPO Preferences, or SYSVOL.
  This script writes:
    HKLM\SOFTWARE\QKA\DesktopAgent\MachineEnrollmentToken
  with ACL limited to Administrators + SYSTEM (+ Local Service read).

  Preferred for the first pilot: run locally ON the pilot PC (you are admin of your PC).
  Optional: -ComputerName with Invoke-Command if you already have admin remoting rights
  (not Domain Admin via the QKA app).

  Pass the token from the QKA Deploy response once; do not log it.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$Token,

  [string]$ComputerName = $env:COMPUTERNAME,
  [switch]$IConfirmProvisionToThisComputer
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmProvisionToThisComputer) {
  throw 'Pass -IConfirmProvisionToThisComputer. Refusing to write enrollment token without confirmation.'
}
if ([string]::IsNullOrWhiteSpace($Token) -or $Token.Length -lt 16) {
  throw 'Token looks empty/short. Paste the one-time machineEnrollmentToken from Deploy (not logged).'
}

$scriptBlock = {
  param([string]$PlainToken)
  $ErrorActionPreference = 'Stop'
  $keyPath = 'HKLM:\SOFTWARE\QKA\DesktopAgent'
  New-Item -Path $keyPath -Force | Out-Null
  New-ItemProperty -Path $keyPath -Name 'MachineEnrollmentToken' -Value $PlainToken -PropertyType String -Force | Out-Null
  # Tighten ACL on the key: Admins + SYSTEM full; Local Service read
  $acl = Get-Acl -Path $keyPath
  $acl.SetAccessRuleProtection($true, $false)
  $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
  foreach ($rule in @(
      (New-Object System.Security.AccessControl.RegistryAccessRule('BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')),
      (New-Object System.Security.AccessControl.RegistryAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')),
      (New-Object System.Security.AccessControl.RegistryAccessRule('NT AUTHORITY\LOCAL SERVICE', 'ReadKey', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    )) {
    $acl.AddAccessRule($rule)
  }
  Set-Acl -Path $keyPath -AclObject $acl
  'OK'
}

$target = ($ComputerName -replace '\..*$', '').ToUpperInvariant()
$local = $env:COMPUTERNAME.ToUpperInvariant()
if ($target -eq $local -or $ComputerName -eq '.' -or $ComputerName -eq 'localhost') {
  Write-Host ("Provisioning HKLM token on LOCAL computer " + $env:COMPUTERNAME)
  & $scriptBlock -PlainToken $Token | Out-Null
} else {
  Write-Host ("Provisioning HKLM token via Invoke-Command on " + $ComputerName)
  Invoke-Command -ComputerName $ComputerName -ScriptBlock $scriptBlock -ArgumentList $Token | Out-Null
}
Write-Host 'Token written to HKLM\SOFTWARE\QKA\DesktopAgent\MachineEnrollmentToken (not printed).'
Write-Host 'Restart service QkaDesktopAgent. After enroll succeeds the agent clears the value.'
# Scrub from this session
$Token = $null
Remove-Variable Token -ErrorAction SilentlyContinue
