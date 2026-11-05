<#
.SYNOPSIS
  Dedicated IIS HTTPS site for qka-agent.qehc.edu.sa → reverse-proxy agent APIs to :5265.

.DESCRIPTION
  Creates site QKA-Agent-HTTPS only. Does not modify other sites, MCP, or OAuth bindings.
  Requires: elevation, URL Rewrite + ARR modules, certificate already in LocalMachine\My.

  Proxies only:
    /api/v1/agent/desktop/*
  Health probe (optional):
    /health → http://127.0.0.1:5265/health

  Does not publish FastAPI publicly. Does not set QKA_AGENT_PUBLIC_BASE_URL
  (set that only after client TLS trust validates).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$CertificateThumbprint,

  [string]$HostName = 'qka-agent.qehc.edu.sa',
  [string]$SiteName = 'QKA-Agent-HTTPS',
  [string]$AppPoolName = 'QKA-Agent-HTTPS',
  [string]$SiteRoot = 'C:\inetpub\qka-agent',
  [string]$Backend = 'http://127.0.0.1:5265',
  [int]$HttpsPort = 443
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated as an authorized administrator.'
}

$thumb = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
$cert = Get-Item -LiteralPath ("Cert:\LocalMachine\My\" + $thumb) -ErrorAction SilentlyContinue
if (-not $cert) {
  throw "Certificate thumbprint $thumb not found in Cert:\LocalMachine\My. Run New-QkaAgentHttpsCertificate.ps1 first."
}

$rewriteDll = 'C:\Windows\System32\inetsrv\rewrite.dll'
$arrDir = 'C:\Program Files\IIS\Application Request Routing'
if (-not (Test-Path $rewriteDll) -or -not (Test-Path $arrDir)) {
  throw @'
Missing IIS URL Rewrite and/or Application Request Routing (ARR).
Install (elevated), then re-run this script:
  1) URL Rewrite 2.1: https://www.iis.net/downloads/microsoft/url-rewrite
  2) ARR 3.0: https://www.iis.net/downloads/microsoft/application-request-routing
Do not proceed without these modules — do not expose :5265 directly on the LAN.
'@
}

Import-Module WebAdministration -ErrorAction Stop

New-Item -ItemType Directory -Force -Path $SiteRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SiteRoot 'logs') | Out-Null

$webConfig = @"
<?xml version="1.0" encoding="UTF-8"?>
<!-- QKA Agent HTTPS reverse proxy — agent endpoints only. Do not broaden rules. -->
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="QKA Agent Desktop API" stopProcessing="true">
          <match url="^api/v1/agent/desktop/(.*)" />
          <action type="Rewrite" url="$Backend/api/v1/agent/desktop/{R:1}" />
          <serverVariables>
            <set name="HTTP_X_FORWARDED_PROTO" value="https" />
            <set name="HTTP_X_FORWARDED_HOST" value="{HTTP_HOST}" />
          </serverVariables>
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
Set-Content -Path (Join-Path $SiteRoot 'web.config') -Value $webConfig -Encoding UTF8

# Allow ARR to proxy to localhost
Set-WebConfigurationProperty -pspath 'MACHINE/WEBROOT/APPHOST' `
  -filter 'system.webServer/proxy' -name 'enabled' -value 'True' -ErrorAction SilentlyContinue

if (-not (Test-Path ("IIS:\AppPools\" + $AppPoolName))) {
  New-WebAppPool -Name $AppPoolName | Out-Null
}
Set-ItemProperty ("IIS:\AppPools\" + $AppPoolName) -Name managedRuntimeVersion -Value ''

if (-not (Get-Website -Name $SiteName -ErrorAction SilentlyContinue)) {
  New-Website -Name $SiteName -Port $HttpsPort -HostHeader $HostName -PhysicalPath $SiteRoot -ApplicationPool $AppPoolName | Out-Null
} else {
  Set-ItemProperty ("IIS:\Sites\" + $SiteName) -Name physicalPath -Value $SiteRoot
  Set-ItemProperty ("IIS:\Sites\" + $SiteName) -Name applicationPool -Value $AppPoolName
}

# Ensure HTTPS binding with SNI + cert (do not touch other sites' bindings)
$bindingInfo = "*:${HttpsPort}:${HostName}"
$existing = Get-WebBinding -Name $SiteName -Protocol https -ErrorAction SilentlyContinue |
  Where-Object { $_.bindingInformation -eq $bindingInfo }
if (-not $existing) {
  New-WebBinding -Name $SiteName -Protocol https -Port $HttpsPort -HostHeader $HostName -SslFlags 1
}

# Bind certificate to this hostname (SNI)
$guid = [guid]::NewGuid().ToString('B')
netsh http delete sslcert hostnameport="${HostName}:${HttpsPort}" 2>$null | Out-Null
$add = netsh http add sslcert hostnameport="${HostName}:${HttpsPort}" certhash=$thumb certstorename=MY appid=$guid
if ($LASTEXITCODE -ne 0) {
  throw "netsh http add sslcert failed: $add"
}

# Enable server variables for X-Forwarded-* (ARR)
try {
  Add-WebConfigurationProperty -pspath "MACHINE/WEBROOT/APPHOST/$SiteName" `
    -filter 'system.webServer/rewrite/allowedServerVariables' -name '.' `
    -value @{ name = 'HTTP_X_FORWARDED_PROTO' } -ErrorAction SilentlyContinue
  Add-WebConfigurationProperty -pspath "MACHINE/WEBROOT/APPHOST/$SiteName" `
    -filter 'system.webServer/rewrite/allowedServerVariables' -name '.' `
    -value @{ name = 'HTTP_X_FORWARDED_HOST' } -ErrorAction SilentlyContinue
} catch {
  Write-Warning "Could not register ARR server variables automatically: $($_.Exception.Message)"
}

Start-Website -Name $SiteName -ErrorAction SilentlyContinue
Write-Host "Site $SiteName ready: https://${HostName}/api/v1/agent/desktop/ (proxy → $Backend)"
Write-Host 'NEXT (manual):'
Write-Host '  1) From the pilot PC (with cert trust): Invoke-WebRequest https://qka-agent.qehc.edu.sa/health'
Write-Host '  2) Only after trust OK, set QKA_AGENT_PUBLIC_BASE_URL=https://qka-agent.qehc.edu.sa and restart API'
Write-Host '  3) Do not expose :5265 on the firewall; keep FastAPI localhost-bound'
