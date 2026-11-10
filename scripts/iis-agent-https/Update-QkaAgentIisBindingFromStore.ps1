<#
.SYNOPSIS
  Rebind qka-agent.qehc.edu.sa:443 SNI to the newest enterprise leaf in LocalMachine\My.

.DESCRIPTION
  Idempotent. Does not enroll. Does not touch unrelated sslcert mappings.
  Prefers certificates issued by QEHC-Internal-Root-CA with SAN/CN matching HostName
  and Server Authentication EKU. Leaves self-signed pilot cert in the store.
#>
[CmdletBinding()]
param(
  [string]$HostName = 'qka-agent.qehc.edu.sa',
  [string]$CaCn = 'QEHC-Internal-Root-CA',
  [string]$AppId = '{4dc3e181-e14b-4a21-b022-59fc669b0914}'
)

$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run elevated (scheduled task should use SYSTEM / Highest).' }

$binding = netsh http show sslcert hostnameport="$HostName`:443" | Out-String
if ($binding -notmatch 'Certificate Hash\s+:\s*([0-9a-fA-F]+)') {
  throw "No SSL binding for $HostName`:443"
}
$current = $Matches[1].ToUpperInvariant()

$leaf = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
  if (-not $_.HasPrivateKey) { return $false }
  if ($_.Issuer -notmatch [regex]::Escape($CaCn)) { return $false }
  if ($_.Subject -match [regex]::Escape($CaCn)) { return $false }
  $ekuOk = @($_.EnhancedKeyUsageList | ForEach-Object { $_.FriendlyName }) -contains 'Server Authentication'
  if (-not $ekuOk -and @($_.EnhancedKeyUsageList).Count -gt 0) { return $false }
  if ($_.Subject -match [regex]::Escape("CN=$HostName")) { return $true }
  $san = $_.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
  return [bool]($san -and $san.Format($false) -match [regex]::Escape($HostName))
} | Sort-Object NotAfter -Descending | Select-Object -First 1

if (-not $leaf) { throw "No enterprise leaf for $HostName issued by $CaCn in LocalMachine\My" }
$new = $leaf.Thumbprint.ToUpperInvariant()
if ($new -eq $current) {
  Write-Output "IIS_BINDING_OK $current"
  exit 0
}

netsh http update sslcert hostnameport="$HostName`:443" certhash=$new appid=$AppId certstorename=MY
if ($LASTEXITCODE -ne 0) {
  # Fall back to delete+add for this hostnameport only
  netsh http delete sslcert hostnameport="$HostName`:443" | Out-Null
  netsh http add sslcert hostnameport="$HostName`:443" certhash=$new appid=$AppId certstorename=MY
  if ($LASTEXITCODE -ne 0) { throw "Failed to rebind $HostName`:443 to $new" }
}
Write-Output "IIS_BINDING_UPDATED $current -> $new"
