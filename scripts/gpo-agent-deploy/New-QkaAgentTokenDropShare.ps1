<#
.SYNOPSIS
  Create a locked-down UNC share for per-computer enrollment token drop files.

.DESCRIPTION
  Default share ACL: Administrators + SYSTEM only (NO Domain Computers / Authenticated Users).
  QKA Deploy writes one file per computer SID and grants that computer$ Read+Delete only.
  Agents claim+delete their own file — no PowerShell paste, no SYSVOL, no MSI secrets.

  Share example: \\QEC-AI-MCP\QkaAgentTokens
#>
[CmdletBinding()]
param(
  [string]$ShareName = 'QkaAgentTokens',
  [string]$LocalPath = 'C:\QehcAI\QkaAgentTokens',
  [switch]$IConfirmCreateShare
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run elevated on the QKA file server.' }
if (-not $IConfirmCreateShare) {
  throw 'Pass -IConfirmCreateShare to create the token-drop share.'
}

New-Item -ItemType Directory -Force -Path $LocalPath | Out-Null
# Placeholder so share is never empty
Set-Content -Path (Join-Path $LocalPath 'README.txt') -Value 'Per-computer *.tok files only. Do not place secrets in GPO/SYSVOL.' -Encoding ASCII

$acl = Get-Acl -LiteralPath $LocalPath
$acl.SetAccessRuleProtection($true, $false)
@($acl.Access) | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
foreach ($r in @(
    New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'),
    New-Object System.Security.AccessControl.FileSystemAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
  )) { $acl.AddAccessRule($r) }
Set-Acl -LiteralPath $LocalPath -AclObject $acl

$existing = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if (-not $existing) {
  # No ReadAccess for Domain Computers at share level — per-file ACL only after drop.
  New-SmbShare -Name $ShareName -Path $LocalPath -FullAccess 'QEHC\Domain Admins', 'BUILTIN\Administrators' `
    -Description 'QKA per-computer enrollment token drop (ACL per file)'
} else {
  Write-Host ("Share exists path=" + $existing.Path)
}

# Share Change (not Read-only): agent must DELETE its one-time *.tok after claim.
# Isolation is NTFS per-file ACL (computer SID R,D only) — not share-level secrecy.
# Agent runs as Network Service so outbound SMB authenticates as DOMAIN\COMPUTER$.
# Replace prior Read-only share ACE if present (Read blocks Delete of claimed *.tok).
try { Revoke-SmbShareAccess -Name $ShareName -AccountName 'QEHC\Domain Computers' -Force -ErrorAction SilentlyContinue | Out-Null } catch { }
Grant-SmbShareAccess -Name $ShareName -AccountName 'QEHC\Domain Computers' -AccessRight Change -Force | Out-Null

$unc = "\\$env:COMPUTERNAME\$ShareName"
Write-Host ("TOKEN_DROP_UNC=" + $unc)
Write-Host 'SHARE_ACCESS=Domain Computers Change (NTFS per-SID isolates tokens)'
Write-Host 'AGENT_IDENTITY=NT AUTHORITY\NETWORK SERVICE (SMB as computer$)'
Write-Host 'Set QKA_AGENT_TOKEN_DROP_UNC and QKA_AGENT_TOKEN_PROVISIONING=unc_acl_drop, then restart API.'
Write-Host 'VERIFY: Run Test-QkaTokenDropIsolation.ps1 -PilotComputer WSIT02 (no production share required for local ACL proof).'
