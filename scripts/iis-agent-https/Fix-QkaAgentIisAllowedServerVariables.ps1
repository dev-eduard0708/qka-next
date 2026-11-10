<#
.SYNOPSIS
  Elevated fix: allow HTTP_X_FORWARDED_PROTO / HTTP_X_FORWARDED_HOST for URL Rewrite
  so https://qka-agent.qehc.edu.sa/api/v1/agent/desktop/* stops returning empty HTTP 500.

.DESCRIPTION
  Root cause: web.config rewrite rules set those serverVariables, but they are not on
  the IIS Allowed Server Variables list (and site-scoped adds fail when
  overrideModeDefault=Deny). This script adds them at MACHINE/WEBROOT/APPHOST only —
  no broad unlock, no IISRESET, no other sites touched.

  The rewrite rule itself OVERWRITES proto/host (value=https / {HTTP_HOST}), so
  clients cannot spoof forwarded headers through this path to change scheme/host.

  Safe for active WSIT02 job b5bf2232…. Does not mint tokens, change GPO/MSI/AD,
  reboot, or retry deploy.
#>
[CmdletBinding()]
param(
  [string]$SiteName = 'QKA-Agent-HTTPS',
  [string]$SiteRoot = 'C:\inetpub\qka-agent',
  [string]$BackupRoot = 'C:\QehcAI\Temp\qka-iis-backups',
  [string[]]$VariableNames = @('HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_HOST')
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated as an authorized administrator.'
}

Import-Module WebAdministration -ErrorAction Stop

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupName = "QKA-Agent-AllowedServerVars-$stamp"
Write-Host "[QKA-IIS] appcmd backup $backupName"
& "$env:windir\System32\inetsrv\appcmd.exe" add backup $backupName | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "appcmd add backup failed (exit $LASTEXITCODE). Aborting."
}

$configSrc = Join-Path $env:windir 'System32\inetsrv\config\applicationHost.config'
$configCopy = Join-Path $BackupRoot ("applicationHost.config.$stamp")
Copy-Item -LiteralPath $configSrc -Destination $configCopy -Force
Write-Host "[QKA-IIS] Copied applicationHost.config → $configCopy"

$webPath = Join-Path $SiteRoot 'web.config'
if (Test-Path -LiteralPath $webPath) {
  $webBak = Join-Path $BackupRoot ("web.config.$stamp")
  Copy-Item -LiteralPath $webPath -Destination $webBak -Force
  Write-Host "[QKA-IIS] Copied web.config → $webBak"
}

# Inspect live rewrite serverVariables (read-only)
Write-Host '[QKA-IIS] Live web.config serverVariables (expected HTTP_X_FORWARDED_PROTO/HOST):'
Select-String -Path $webPath -Pattern 'HTTP_X_FORWARDED_|serverVariables' | ForEach-Object { Write-Host ("  " + $_.Line.Trim()) }

$pspath = 'MACHINE/WEBROOT/APPHOST'
foreach ($varName in $VariableNames) {
  $exists = $false
  try {
    $existing = Get-WebConfiguration -pspath $pspath `
      -filter 'system.webServer/rewrite/allowedServerVariables' -ErrorAction Stop
    if ($existing) {
      $exists = @($existing.Collection | Where-Object { $_.name -eq $varName }).Count -gt 0
    }
  } catch {
    $exists = $false
  }
  if ($exists) {
    Write-Host "[QKA-IIS] Already allowed (server-level): $varName"
    continue
  }
  Add-WebConfigurationProperty -pspath $pspath `
    -filter 'system.webServer/rewrite/allowedServerVariables' -name '.' `
    -value @{ name = $varName }
  Write-Host "[QKA-IIS] Added server-level allowedServerVariable: $varName"
}

# Confirm site still present; do not modify bindings or other sites
$site = Get-Website -Name $SiteName -ErrorAction SilentlyContinue
if (-not $site) {
  Write-Warning "Site $SiteName not found by name — allowed vars are still server-level and apply to rewrite rules that set them."
} else {
  Write-Host ("[QKA-IIS] Site intact: " + $SiteName + " state=" + $site.State)
}

$resultPath = 'C:\Windows\Temp\qka-iis-allowed-vars-fix.ok'
@(
  "stamp=$stamp"
  "backup=$backupName"
  "vars=$($VariableNames -join ',')"
) | Set-Content -LiteralPath $resultPath -Encoding UTF8
Write-Host "[QKA-IIS] Done. Marker: $resultPath"
Write-Host '[QKA-IIS] No IISRESET performed. URL Rewrite picks up allowedServerVariables immediately.'
