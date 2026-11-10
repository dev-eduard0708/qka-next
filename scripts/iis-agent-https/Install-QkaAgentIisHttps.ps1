<#
.SYNOPSIS
  Dedicated IIS HTTPS site for qka-agent.qehc.edu.sa → reverse-proxy agent APIs to :5265.

.DESCRIPTION
  Creates/updates site QKA-Agent-HTTPS only (HTTPS + SNI). Does not modify Default Web Site,
  QEC PKI, MCP, Google OAuth, or any unrelated bindings/sslcert mappings.

  Proxies only:
    /api/v1/agent/desktop/*
    /health → http://127.0.0.1:5265/health

  Safe to re-run (idempotent). Does not set QKA_AGENT_PUBLIC_BASE_URL.
  Does NOT execute until an authorized administrator runs this elevated script.
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
  [int]$HttpsPort = 443,
  [string]$BackupRoot = 'C:\QehcAI\Temp\qka-iis-backups'
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated as an authorized administrator.'
}

function Write-Step([string]$Message) { Write-Host ("[QKA-IIS] " + $Message) }

function Get-SslCertMappingLines([string]$HostnamePort) {
  $raw = & netsh http show sslcert hostnameport=$HostnamePort 2>&1 | Out-String
  return $raw
}

function Test-SslCertThumbprint([string]$HostnamePort, [string]$ExpectedThumb) {
  $raw = Get-SslCertMappingLines -HostnamePort $HostnamePort
  if ($raw -notmatch 'Certificate Hash\s+:\s*([0-9a-fA-F]+)') {
    return $false
  }
  return (($Matches[1]).ToUpperInvariant() -eq $ExpectedThumb.ToUpperInvariant())
}

$thumb = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
if ($thumb -notmatch '^[0-9A-F]{40}$') {
  throw "Certificate thumbprint must be 40 hex chars. Got: $CertificateThumbprint"
}
$cert = Get-Item -LiteralPath ("Cert:\LocalMachine\My\" + $thumb) -ErrorAction SilentlyContinue
if (-not $cert) {
  throw "Certificate thumbprint $thumb not found in Cert:\LocalMachine\My. Run New-QkaAgentHttpsCertificate.ps1 first."
}

# --- Prerequisites: URL Rewrite + ARR ---
$rewriteDll = 'C:\Windows\System32\inetsrv\rewrite.dll'
$arrDir = 'C:\Program Files\IIS\Application Request Routing'
$rewriteOk = Test-Path -LiteralPath $rewriteDll
$arrOk = Test-Path -LiteralPath $arrDir
Write-Step ("URL Rewrite present=" + $rewriteOk + " path=" + $rewriteDll)
Write-Step ("ARR present=" + $arrOk + " path=" + $arrDir)
if (-not $rewriteOk -or -not $arrOk) {
  throw @'
Missing IIS URL Rewrite and/or Application Request Routing (ARR).
Install (elevated), then re-run this script:
  1) URL Rewrite 2.1: https://www.iis.net/downloads/microsoft/url-rewrite
  2) ARR 3.0: https://www.iis.net/downloads/microsoft/application-request-routing
Do not proceed without these modules — do not expose :5265 directly on the LAN.
'@
}

Import-Module WebAdministration -ErrorAction Stop

# --- Preflight inventory (read-only) ---
Write-Step 'Preflight: existing websites and bindings (read-only)'
$protectedSites = @('Default Web Site', 'Default WebSite')
Get-Website | ForEach-Object {
  $n = $_.Name
  Write-Host ("  SITE name=" + $n + " state=" + $_.State + " id=" + $_.Id)
  Get-WebBinding -Name $n | ForEach-Object {
    Write-Host ("    BIND protocol=" + $_.protocol + " info=" + $_.bindingInformation + " sslFlags=" + $_.sslFlags)
  }
  if ($protectedSites -contains $n -and $n -ne $SiteName) {
    Write-Host ("    (protected — will not modify)")
  }
}

$hostnamePort = "${HostName}:${HttpsPort}"
$bindingInfo = "*:${HttpsPort}:${HostName}"
Write-Step ("Preflight: SSL mapping for " + $hostnamePort + " only")
$sslBefore = Get-SslCertMappingLines -HostnamePort $hostnamePort
Write-Host $sslBefore

# Conflict: another site already owns this exact HTTPS host-header binding
$foreignOwner = $null
Get-Website | Where-Object { $_.Name -ne $SiteName } | ForEach-Object {
  $site = $_.Name
  Get-WebBinding -Name $site -ErrorAction SilentlyContinue | Where-Object {
    $_.protocol -eq 'https' -and $_.bindingInformation -eq $bindingInfo
  } | ForEach-Object {
    $script:foreignOwner = $site
  }
}
if ($foreignOwner) {
  throw "HTTPS binding $bindingInfo already exists on site '$foreignOwner'. Refusing to steal it. Resolve manually."
}

# --- Backup IIS configuration ---
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupName = "QKA-Agent-HTTPS-$stamp"
Write-Step ("Backing up IIS configuration as $backupName")
& "$env:windir\System32\inetsrv\appcmd.exe" add backup $backupName | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "appcmd add backup failed (exit $LASTEXITCODE). Aborting before any IIS changes."
}
$configSrc = Join-Path $env:windir 'System32\inetsrv\config\applicationHost.config'
$configCopy = Join-Path $BackupRoot ("applicationHost.config.$stamp")
Copy-Item -LiteralPath $configSrc -Destination $configCopy -Force
Write-Step ("Also copied applicationHost.config → " + $configCopy)
Write-Step ("Restore: appcmd restore backup $backupName")

# --- Site files (agent APIs + /health only) ---
New-Item -ItemType Directory -Force -Path $SiteRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SiteRoot 'logs') | Out-Null

# Allow rewrite serverVariables at MACHINE level BEFORE writing rules that set them.
# Site-scoped adds fail when allowedServerVariables overrideModeDefault=Deny — that left
# the pilot site returning empty HTTP 500 on /api/v1/agent/desktop/* while /health worked.
# Rule values overwrite client X-Forwarded-* (proto=https, host={HTTP_HOST}) — no client spoof.
$proxyPathForVars = 'MACHINE/WEBROOT/APPHOST'
foreach ($varName in @('HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_HOST')) {
  $exists = $false
  try {
    $existingVars = Get-WebConfiguration -pspath $proxyPathForVars `
      -filter 'system.webServer/rewrite/allowedServerVariables' -ErrorAction SilentlyContinue
    if ($existingVars) {
      $exists = @($existingVars.Collection | Where-Object { $_.name -eq $varName }).Count -gt 0
    }
  } catch { $exists = $false }
  if (-not $exists) {
    Add-WebConfigurationProperty -pspath $proxyPathForVars `
      -filter 'system.webServer/rewrite/allowedServerVariables' -name '.' `
      -value @{ name = $varName }
    Write-Step ("Allowed server variable added (server-level): " + $varName)
  } else {
    Write-Step ("Allowed server variable already present (server-level): " + $varName)
  }
}

$webConfig = @"
<?xml version="1.0" encoding="UTF-8"?>
<!-- QKA Agent HTTPS reverse proxy — agent endpoints only. Do not broaden rules. -->
<!-- serverVariables overwrite client X-Forwarded-* (anti-spoof). Names must be server-level allowed. -->
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


# --- ARR proxy (server-wide): enable only if not already on; report change ---
$proxyPath = 'MACHINE/WEBROOT/APPHOST'
$proxyWasEnabled = $false
try {
  $proxyWasEnabled = [bool](Get-WebConfigurationProperty -pspath $proxyPath `
      -filter 'system.webServer/proxy' -name 'enabled').Value
} catch {
  $proxyWasEnabled = $false
}
if ($proxyWasEnabled) {
  Write-Step 'ARR system.webServer/proxy already enabled (no server-wide change).'
} else {
  Write-Warning 'SERVER-WIDE CHANGE: enabling system.webServer/proxy (ARR) on MACHINE/WEBROOT/APPHOST. Required for reverse proxy; does not alter site bindings.'
  Set-WebConfigurationProperty -pspath $proxyPath `
    -filter 'system.webServer/proxy' -name 'enabled' -value 'True'
  Write-Step 'ARR proxy enabled=True (was False).'
}

# --- App pool (idempotent) ---
if (-not (Test-Path ("IIS:\AppPools\" + $AppPoolName))) {
  Write-Step ("Creating app pool " + $AppPoolName)
  New-WebAppPool -Name $AppPoolName | Out-Null
} else {
  Write-Step ("App pool already exists: " + $AppPoolName)
}
Set-ItemProperty ("IIS:\AppPools\" + $AppPoolName) -Name managedRuntimeVersion -Value ''

# --- Site + HTTPS binding (never HTTP on 443) ---
$site = Get-Website -Name $SiteName -ErrorAction SilentlyContinue
if (-not $site) {
  Write-Step ("Creating site $SiteName with HTTPS directly (-Ssl -SslFlags 1); no HTTP binding on port $HttpsPort")
  # -Ssl creates an https binding (not http). SslFlags 1 = SNI.
  New-Website `
    -Name $SiteName `
    -Port $HttpsPort `
    -IPAddress '*' `
    -HostHeader $HostName `
    -PhysicalPath $SiteRoot `
    -ApplicationPool $AppPoolName `
    -Ssl `
    -SslFlags 1 | Out-Null
} else {
  Write-Step ("Site already exists: $SiteName (updating path/pool only; not recreating)")
  Set-ItemProperty ("IIS:\Sites\" + $SiteName) -Name physicalPath -Value $SiteRoot
  Set-ItemProperty ("IIS:\Sites\" + $SiteName) -Name applicationPool -Value $AppPoolName
}

# Remove accidental HTTP binding on *:443:HostName if present (legacy mistake); never touch other ports/hosts
Get-WebBinding -Name $SiteName -ErrorAction SilentlyContinue | Where-Object {
  $_.protocol -eq 'http' -and $_.bindingInformation -eq $bindingInfo
} | ForEach-Object {
  Write-Warning "Removing erroneous HTTP binding $bindingInfo from $SiteName (port $HttpsPort must be HTTPS only)."
  Remove-WebBinding -Name $SiteName -Protocol http -Port $HttpsPort -HostHeader $HostName
}

$httpsBinding = Get-WebBinding -Name $SiteName -Protocol https -ErrorAction SilentlyContinue |
  Where-Object { $_.bindingInformation -eq $bindingInfo }
if (-not $httpsBinding) {
  Write-Step ("Adding HTTPS binding $bindingInfo with SNI (SslFlags=1)")
  New-WebBinding -Name $SiteName -Protocol https -Port $HttpsPort -IPAddress '*' -HostHeader $HostName -SslFlags 1
} else {
  Write-Step ("HTTPS binding already present: " + $bindingInfo)
  # Ensure SNI flag
  try {
    $httpsBinding.AddSslFlags('Sni') | Out-Null
  } catch {
    # Older IIS: Set-WebBinding / raw config
    try {
      Set-WebConfigurationProperty -pspath "IIS:\Sites\$SiteName" `
        -filter "system.applicationHost/sites/site[@name='$SiteName']/bindings/binding[@protocol='https' and @bindingInformation='$bindingInfo']" `
        -name 'sslFlags' -value 1 -ErrorAction SilentlyContinue
    } catch {
      Write-Warning "Could not force sslFlags=1 on existing binding (may already be SNI): $($_.Exception.Message)"
    }
  }
}

# --- SSL cert mapping: only hostnameport=HostName:HttpsPort; never delete unrelated mappings ---
$appId = '{4dc3e181-e14b-4a21-b022-59fc669b0914}' # stable QKA Agent HTTPS appid
if (Test-SslCertThumbprint -HostnamePort $hostnamePort -ExpectedThumb $thumb) {
  Write-Step ("SSL mapping already correct for $hostnamePort → $thumb (no netsh delete).")
} else {
  $existingMap = Get-SslCertMappingLines -HostnamePort $hostnamePort
  if ($existingMap -match 'Certificate Hash') {
    Write-Warning "Updating SSL mapping for $hostnamePort only (delete+add this hostnameport; unrelated sslcert entries untouched)."
    & netsh http delete sslcert hostnameport=$hostnamePort | Out-Host
    if ($LASTEXITCODE -ne 0) {
      throw "netsh http delete sslcert hostnameport=$hostnamePort failed. Aborting; unrelated mappings were not targeted."
    }
  } else {
    Write-Step ("No existing SSL mapping for $hostnamePort — adding.")
  }
  $addOut = & netsh http add sslcert hostnameport=$hostnamePort certhash=$thumb certstorename=MY appid=$appId 2>&1
  $addOut | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "netsh http add sslcert failed for $hostnamePort only: $addOut"
  }
}

# Allowed server variables were ensured at MACHINE/WEBROOT/APPHOST before web.config write.

Start-Website -Name $SiteName -ErrorAction SilentlyContinue

Write-Step 'Post-check: this site bindings only'
Get-WebBinding -Name $SiteName | ForEach-Object {
  Write-Host ("  " + $_.protocol + " " + $_.bindingInformation + " sslFlags=" + $_.sslFlags)
}
Write-Step ("Post-check: SSL mapping " + $hostnamePort)
Write-Host (Get-SslCertMappingLines -HostnamePort $hostnamePort)

Write-Host ''
Write-Host "Site $SiteName ready: https://${HostName}/api/v1/agent/desktop/ (proxy → $Backend)"
Write-Host 'Preserved: all other IIS sites/bindings (Default Web Site, QEC PKI, MCP, OAuth) untouched.'
Write-Host 'NEXT (manual):'
Write-Host '  1) From the pilot PC (with cert trust): Invoke-WebRequest https://qka-agent.qehc.edu.sa/health'
Write-Host '  2) Only after trust OK, set QKA_AGENT_PUBLIC_BASE_URL=https://qka-agent.qehc.edu.sa and restart API'
Write-Host '  3) Do not expose :5265 on the firewall; keep FastAPI localhost-bound'
Write-Host ("  4) IIS backup name: $backupName")
