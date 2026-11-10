<#
.SYNOPSIS
  One-shot elevated fix: remove unallowed URL Rewrite serverVariables from
  C:\inetpub\qka-agent\web.config that cause empty HTTP 500 on
  /api/v1/agent/desktop/* while /health still returns 200.

.DESCRIPTION
  Safe for the active WSIT02 deployment. Does not create jobs, cancel, reboot,
  or reinstall. Idempotent. Requires an elevated administrator PowerShell.
#>
[CmdletBinding()]
param(
  [string]$SiteRoot = 'C:\inetpub\qka-agent',
  [string]$Backend = 'http://127.0.0.1:5265'
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated as an authorized administrator.'
}

$path = Join-Path $SiteRoot 'web.config'
if (-not (Test-Path -LiteralPath $path)) {
  throw "Missing $path"
}

$backup = Join-Path $SiteRoot ("web.config.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath $path -Destination $backup -Force

$webConfig = @"
<?xml version="1.0" encoding="UTF-8"?>
<!-- QKA Agent HTTPS reverse proxy — agent endpoints only. Do not broaden rules. -->
<!-- No serverVariables: unallowed HTTP_X_FORWARDED_* caused empty HTTP 500 on desktop API. -->
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="QKA Agent Desktop API" stopProcessing="true">
          <match url="^api/v1/agent/desktop/(.*)" />
          <action type="Rewrite" url="$Backend/api/v1/agent/desktop/{R:1}" />
        </rule>
        <rule name="QKA Health" stopProcessing="true">
          <match url="^health$" />
          <action type="Rewrite" url="$Backend/health" />
        </rule>
        <rule name="Block everything else" stopProcessing="true">
          <match url=".*" />
          <action type="CustomResponse" statusCode="404" statusReason="Not Found" statusDescription="Not Found" />
        </rule>
      </rules>
    </rewrite>
    <security>
      <requestFiltering allowDoubleEscaping="false" />
    </security>
    <httpErrors existingResponse="PassThrough" />
  </system.webServer>
</configuration>
"@
Set-Content -LiteralPath $path -Value $webConfig -Encoding UTF8
Write-Host "Wrote $path (backup $backup)"

# Quick smoke: health must stay 200; desktop POST must not be empty-500 once ARR reloads config.
Start-Sleep -Seconds 1
try {
  $h = Invoke-WebRequest -Uri "https://qka-agent.qehc.edu.sa/health" -UseBasicParsing -TimeoutSec 15
  Write-Host ("health HTTPS status=" + [int]$h.StatusCode)
} catch {
  Write-Warning ("health check failed: " + $_.Exception.Message)
}
Write-Host 'NEXT: confirm install-authorization via HTTPS returns JSON (not empty 500), then wait for WSIT02 QKA-Agent-Install PT2M cycle.'
