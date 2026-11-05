<#
.SYNOPSIS
  Create a computer-readable UNC share for QKA-Agent.msi (no enrollment secrets).

.DESCRIPTION
  Publishes \\<Server>\QkaAgent → local folder with Domain Computers Read access.
  Requires elevation. Does not put tokens on this share.
#>
[CmdletBinding()]
param(
  [string]$ShareName = 'QkaAgent',
  [string]$LocalPath = 'C:\QehcAI\QkaAgentShare',
  [string]$MsiSource = 'C:\QehcAI\QKA-Next\agent\dist\QKA-Agent.msi',
  [switch]$IConfirmCreateShare
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run elevated on the QKA file server.' }
if (-not $IConfirmCreateShare) {
  throw 'Pass -IConfirmCreateShare to create/update the MSI distribution share.'
}
if (-not (Test-Path -LiteralPath $MsiSource)) {
  throw "MSI missing: $MsiSource"
}

New-Item -ItemType Directory -Force -Path $LocalPath | Out-Null
$dest = Join-Path $LocalPath 'QKA-Agent.msi'
Copy-Item -LiteralPath $MsiSource -Destination $dest -Force

$acl = Get-Acl -LiteralPath $LocalPath
$acl.SetAccessRuleProtection($true, $false)
@($acl.Access) | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
foreach ($r in @(
    New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'),
    New-Object System.Security.AccessControl.FileSystemAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'),
    New-Object System.Security.AccessControl.FileSystemAccessRule('QEHC\Domain Computers', 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
  )) { $acl.AddAccessRule($r) }
Set-Acl -LiteralPath $LocalPath -AclObject $acl

if (-not (Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue)) {
  New-SmbShare -Name $ShareName -Path $LocalPath -ReadAccess 'QEHC\Domain Computers' -FullAccess 'QEHC\Domain Admins' `
    -Description 'QKA Desktop Agent MSI (no secrets)'
}

$unc = "\\$env:COMPUTERNAME\$ShareName\QKA-Agent.msi"
Write-Host ("MSI_UNC=" + $unc)
Write-Host ("MSI_SIZE=" + (Get-Item $dest).Length)
Write-Host 'Set QKA_AGENT_MSI_UNC to this path, then restart API.'
