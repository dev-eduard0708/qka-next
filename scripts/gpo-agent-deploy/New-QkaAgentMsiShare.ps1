<#
.SYNOPSIS
  Create a computer-readable UNC share for QKA-Agent.msi (no enrollment secrets).

.DESCRIPTION
  Publishes \\<Server>\QkaAgent → local folder with Domain Computers Read access.
  Does not put tokens in SYSVOL. Requires elevation on the file server (QEC-AI-MCP).
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

# NTFS: Domain Computers + Authenticated Users read (computers need to read MSI over UNC)
$acl = Get-Acl -LiteralPath $LocalPath
$rules = @(
  New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'),
  New-Object System.Security.AccessControl.FileSystemAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'),
  New-Object System.Security.AccessControl.FileSystemAccessRule('QEHC\Domain Computers', 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
)
foreach ($r in $rules) {
  $acl.SetAccessRule($r)
}
Set-Acl -LiteralPath $LocalPath -AclObject $acl

$existing = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if (-not $existing) {
  New-SmbShare -Name $ShareName -Path $LocalPath -ReadAccess 'QEHC\Domain Computers', 'QEHC\Domain Admins' -Description 'QKA Desktop Agent MSI (no secrets)'
} else {
  Write-Host ("Share already exists: " + $existing.Path)
}

$unc = "\\$env:COMPUTERNAME\$ShareName\QKA-Agent.msi"
Write-Host ("MSI_UNC=" + $unc)
Write-Host ("MSI_SIZE=" + (Get-Item $dest).Length)
Write-Host 'Set QKA_AGENT_MSI_UNC to this UNC on the QKA API host, then restart API.'
Write-Host $unc
