<#
.SYNOPSIS
  FALLBACK: deliver one-time machineEnrollmentToken for Network Service cleanup-safe storage.

.DESCRIPTION
  Preferred path is automatic UNC ACL token-drop (unc_acl_drop) — no paste required.
  This script is the manual fallback when token-drop share is unavailable.

  Writes %ProgramData%\QKA\DesktopAgent\pending.enrollment.token with ACL:
    Administrators, SYSTEM, Network Service — Modify
  Also sets HKLM MachineEnrollmentToken with Network Service Read+SetValue/Delete so the
  agent service can clear it after enroll.

  Token is accepted via -TokenSecure (SecureString) or pipeline to avoid PSHistory.
  Do NOT pass plaintext -Token on interactive command lines when avoidable.
#>
[CmdletBinding(DefaultParameterSetName = 'Secure')]
param(
  [Parameter(ParameterSetName = 'Plain')]
  [string]$Token,

  [Parameter(ParameterSetName = 'Secure')]
  [SecureString]$TokenSecure,

  [string]$ComputerName = $env:COMPUTERNAME,
  [switch]$IConfirmProvisionToThisComputer
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmProvisionToThisComputer) {
  throw 'Pass -IConfirmProvisionToThisComputer.'
}

if ($PSCmdlet.ParameterSetName -eq 'Secure') {
  if (-not $TokenSecure) {
    $TokenSecure = Read-Host -AsSecureString -Prompt 'Paste one-time machine enrollment token'
  }
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($TokenSecure)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
} else {
  $plain = $Token
}
if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 16) {
  throw 'Token empty/short.'
}

$scriptBlock = {
  param([string]$PlainToken)
  $ErrorActionPreference = 'Stop'
  $dataDir = Join-Path $env:ProgramData 'QKA\DesktopAgent'
  New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
  $pending = Join-Path $dataDir 'pending.enrollment.token'
  # Write then ACL for Network Service modify/delete (agent service identity)
  [IO.File]::WriteAllText($pending, $PlainToken)
  $acl = Get-Acl -LiteralPath $pending
  $acl.SetAccessRuleProtection($true, $false)
  @($acl.Access) | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
  foreach ($id in @(
      'BUILTIN\Administrators',
      'NT AUTHORITY\SYSTEM',
      'NT AUTHORITY\NETWORK SERVICE'
    )) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
      $id, 'Modify', 'Allow')
    $acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $pending -AclObject $acl

  $keyPath = 'HKLM:\SOFTWARE\QKA\DesktopAgent'
  New-Item -Path $keyPath -Force | Out-Null
  New-ItemProperty -Path $keyPath -Name 'MachineEnrollmentToken' -Value $PlainToken -PropertyType String -Force | Out-Null
  $racl = Get-Acl -Path $keyPath
  $racl.SetAccessRuleProtection($true, $false)
  @($racl.Access) | ForEach-Object { [void]$racl.RemoveAccessRule($_) }
  foreach ($rule in @(
      (New-Object System.Security.AccessControl.RegistryAccessRule('BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')),
      (New-Object System.Security.AccessControl.RegistryAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')),
      (New-Object System.Security.AccessControl.RegistryAccessRule('NT AUTHORITY\NETWORK SERVICE', 'ReadKey,SetValue,Delete', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    )) { $racl.AddAccessRule($rule) }
  Set-Acl -Path $keyPath -AclObject $racl
  'OK'
}

$target = ($ComputerName -replace '\..*$', '').ToUpperInvariant()
$local = $env:COMPUTERNAME.ToUpperInvariant()
if ($target -eq $local -or $ComputerName -eq '.' -or $ComputerName -eq 'localhost') {
  & $scriptBlock -PlainToken $plain | Out-Null
} else {
  Invoke-Command -ComputerName $ComputerName -ScriptBlock $scriptBlock -ArgumentList $plain | Out-Null
}
$plain = $null
Remove-Variable plain, Token, TokenSecure -ErrorAction SilentlyContinue
Write-Host 'Token stored for Local Service (ProgramData pending file + HKLM). Not printed.'
Write-Host 'Restart-Service QkaDesktopAgent — agent clears pending file/HKLM after enroll.'
