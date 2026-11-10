<#
.SYNOPSIS
  Fix HTTP 404 for QEHC CA CRL/AIA under http://QEC-AI-MCP.qehc.edu.sa/CertEnroll/

.DESCRIPTION
  Proven primary cause (2026-11-09): IIS role Web-Static-Content / Web-Common-Http was
  NOT installed. Microsoft-IIS/10.0 then returns HTTP 404 Content-Length:0 for every
  static path (iisstart.htm, .crl, .crt, .txt) even when files and NTFS are correct.

  Also hardens site mapping:
    - Host header on QEHC-PKI-CRL only (never steal *:80 from Default Web Site)
    - Site root C:\inetpub\pki + VDIR /CertEnroll (no nested CertEnroll\CertEnroll)
    - MIME maps for .crl/.crt/.cer
    - W3C logging + optional FREB for 404

  Does NOT rebuild CA, change templates, enroll QKA leaf, touch qka-agent:443, or iisreset.
#>
[CmdletBinding()]
param(
  [string]$ComputerDns = 'QEC-AI-MCP.qehc.edu.sa',
  [string]$CACommonName = 'QEHC-Internal-Root-CA',
  [string]$CrlSiteName = 'QEHC-PKI-CRL',
  [string]$CertEnroll = 'C:\Windows\System32\CertSrv\CertEnroll',
  [string]$SiteRoot = 'C:\inetpub\pki',
  [string]$VdirPhysicalPath = 'C:\inetpub\pki\CertEnroll',
  [string]$ReportPath = 'C:\QehcAI\Temp\qka-ca-backups\certenroll-404-report.txt'
)

$ErrorActionPreference = 'Stop'
$script:Report = [System.Collections.Generic.List[string]]::new()
function Step([string]$m) {
  $line = "[QKA-CRL] $m"
  Write-Host $line
  $script:Report.Add($line) | Out-Null
}
function Fail([string]$m) {
  Step "FAILED: $m"
  New-Item -ItemType Directory -Force -Path (Split-Path $ReportPath) | Out-Null
  $script:Report | Set-Content -Path $ReportPath -Encoding UTF8
  throw $m
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail 'Run elevated as local Administrator.' }

Import-Module ServerManager -ErrorAction SilentlyContinue
Import-Module WebAdministration -ErrorAction Stop
New-Item -ItemType Directory -Force -Path (Split-Path $ReportPath), $SiteRoot, $VdirPhysicalPath | Out-Null

# --- Backup IIS config ---
$backupName = "QKA-CertEnroll-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
try {
  Backup-WebConfiguration -Name $backupName | Out-Null
  Step "IIS_BACKUP=$backupName"
} catch {
  & "$env:windir\system32\inetsrv\appcmd.exe" add backup $backupName | Out-Null
  Step "IIS_BACKUP_APPCMD=$backupName"
}

# --- Prove / install missing IIS features (primary root cause) ---
$requiredFeatures = @(
  'Web-Static-Content',   # REQUIRED — without this all static files 404 empty
  'Web-Default-Doc',
  'Web-Http-Errors',
  'Web-Http-Logging',     # W3C logs (sc-status/substatus/site)
  'Web-Filtering',
  'Web-Http-Tracing',     # FREB
  'Web-Scripting-Tools'
)
$missing = @()
foreach ($f in $requiredFeatures) {
  $feat = Get-WindowsFeature -Name $f -ErrorAction SilentlyContinue
  if (-not $feat) { Step "FEATURE_UNKNOWN $f"; continue }
  Step ("FEATURE {0} state={1}" -f $f, $feat.InstallState)
  if ($feat.InstallState -ne 'Installed') { $missing += $f }
}
$rootCause = if ($missing -contains 'Web-Static-Content') {
  'IIS_WEB_STATIC_CONTENT_NOT_INSTALLED'
} elseif ($missing.Count -gt 0) {
  "IIS_FEATURES_MISSING:$($missing -join ',')"
} else {
  'STATIC_CONTENT_PRESENT_CHECK_SITE_MAPPING'
}
Step "ROOT_CAUSE_CANDIDATE=$rootCause"

if ($missing.Count -gt 0) {
  Step ("Installing features: {0}" -f ($missing -join ', '))
  $r = Install-WindowsFeature -Name $missing -IncludeManagementTools
  Step ("Install-WindowsFeature Success={0} ExitCode={1} RestartNeeded={2}" -f $r.Success, $r.ExitCode, $r.RestartNeeded)
  if (-not $r.Success) { Fail "Install-WindowsFeature failed: $($r.ExitCode)" }
  # Re-import IIS module after feature install
  Import-Module WebAdministration -Force -ErrorAction Stop
}

# --- Preconditions: published files ---
$crlFile = Join-Path $CertEnroll "$CACommonName.crl"
$crtFile = Join-Path $CertEnroll "$ComputerDns`_$CACommonName.crt"
if (-not (Test-Path $crlFile)) { Fail "Missing $crlFile — run certutil -CRL first." }
if (-not (Test-Path $crtFile)) { Fail "Missing $crtFile" }
Step "SOURCE crl=$crlFile ($((Get-Item $crlFile).Length) bytes) crt=$crtFile"

Copy-Item (Join-Path $CertEnroll '*.crl'), (Join-Path $CertEnroll '*.crt') -Destination $VdirPhysicalPath -Force
if ((Resolve-Path $SiteRoot).Path -eq (Resolve-Path $VdirPhysicalPath).Path) {
  Fail "SiteRoot and VdirPhysicalPath must differ. SiteRoot=parent (C:\inetpub\pki), Vdir=...\CertEnroll"
}

# Probe file to distinguish routing vs cert-specific handling
$probeName = 'qka-certrenroll-probe.txt'
$probePath = Join-Path $VdirPhysicalPath $probeName
Set-Content -Path $probePath -Value ("QKA-PROBE-" + (Get-Date -Format o)) -Encoding ASCII -Force

# --- MIME ---
function Ensure-Mime([string]$ext, [string]$type) {
  $filter = "system.webServer/staticContent/mimeMap[@fileExtension='$ext']"
  $existing = Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter $filter -Name fileExtension -ErrorAction SilentlyContinue
  if (-not $existing) {
    Add-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/staticContent' -Name '.' -Value @{
      fileExtension = $ext; mimeType = $type
    }
    Step "MIME added $ext -> $type"
  } else {
    Step "MIME ok $ext"
  }
}
Ensure-Mime '.crl' 'application/pkix-crl'
Ensure-Mime '.crt' 'application/pkix-cert'
Ensure-Mime '.cer' 'application/pkix-cert'
Ensure-Mime '.txt' 'text/plain'

# --- Dump sites BEFORE ---
Step '=== SITES BEFORE ==='
Get-Website | ForEach-Object {
  Step ("SITE id={0} name={1} state={2} path={3} pool={4}" -f $_.id, $_.Name, $_.State, $_.physicalPath, $_.applicationPool)
  Get-WebBinding -Name $_.Name | ForEach-Object { Step ("  BIND {0}" -f $_.bindingInformation) }
  Get-WebVirtualDirectory -Site $_.Name -ErrorAction SilentlyContinue | ForEach-Object {
    Step ("  VDIR {0} -> {1}" -f $_.path, $_.physicalPath)
  }
}

# Enable W3C logging
Get-Website | ForEach-Object {
  $n = $_.Name
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.enabled -Value $true -ErrorAction SilentlyContinue
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.logFormat -Value 'W3C' -ErrorAction SilentlyContinue
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.directory -Value 'C:\inetpub\logs\LogFiles' -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path 'C:\inetpub\logs\LogFiles' | Out-Null

# --- Remove MCP host headers from OTHER sites ---
Get-Website | ForEach-Object {
  $n = $_.Name
  if ($n -eq $CrlSiteName) { return }
  Get-WebBinding -Name $n -Protocol http -ErrorAction SilentlyContinue | ForEach-Object {
    $parts = $_.bindingInformation -split ':', 3
    $hdr = if ($parts.Count -ge 3) { $parts[2] } else { '' }
    if ($hdr -eq $ComputerDns -or $hdr -eq ($ComputerDns.Split('.')[0])) {
      Step "Removing host header '$hdr' from site '$n'"
      Remove-WebBinding -Name $n -Protocol http -Port 80 -HostHeader $hdr -ErrorAction SilentlyContinue
    }
  }
}

# --- Ensure PKI site (host-header only; never keep *:80 on PKI) ---
if (-not (Get-Website -Name $CrlSiteName -ErrorAction SilentlyContinue)) {
  New-Website -Name $CrlSiteName -Port 80 -HostHeader $ComputerDns -PhysicalPath $SiteRoot -Force | Out-Null
  Step "Created site $CrlSiteName root=$SiteRoot"
} else {
  Set-ItemProperty "IIS:\Sites\$CrlSiteName" -Name physicalPath -Value $SiteRoot
  Step "Set $CrlSiteName physicalPath=$SiteRoot"
}

# Strip catch-all *:80 from PKI if present (Default Web Site must own it)
foreach ($b in @(Get-WebBinding -Name $CrlSiteName -Protocol http -ErrorAction SilentlyContinue)) {
  if ($b.bindingInformation -eq '*:80:') {
    Remove-WebBinding -Name $CrlSiteName -BindingInformation '*:80:' -ErrorAction SilentlyContinue
    & "$env:windir\system32\inetsrv\appcmd.exe" set site /site.name:"$CrlSiteName" /-"bindings.[protocol='http',bindingInformation='*:80:']" 2>&1 | Out-Null
    Step 'Removed *:80: catch-all from QEHC-PKI-CRL (Default Web Site owns catch-all)'
  }
}

# Ensure Default Web Site has *:80 + wwwroot
if (Get-Website -Name 'Default Web Site' -ErrorAction SilentlyContinue) {
  $def = Get-Website -Name 'Default Web Site'
  if ($def.physicalPath -ne 'C:\inetpub\wwwroot') {
    Set-ItemProperty 'IIS:\Sites\Default Web Site' -Name physicalPath -Value 'C:\inetpub\wwwroot'
    Step 'Restored Default Web Site path=C:\inetpub\wwwroot'
  }
  $hasStar = Get-WebBinding -Name 'Default Web Site' -Protocol http -ErrorAction SilentlyContinue |
    Where-Object { $_.bindingInformation -eq '*:80:' }
  if (-not $hasStar) {
    New-WebBinding -Name 'Default Web Site' -Protocol http -Port 80 -IPAddress '*' -ErrorAction SilentlyContinue
    Step 'Added *:80: to Default Web Site'
  }
  Start-Website -Name 'Default Web Site' -ErrorAction SilentlyContinue
}

$have = @(Get-WebBinding -Name $CrlSiteName | ForEach-Object { $_.bindingInformation })
foreach ($h in @($ComputerDns, ($ComputerDns.Split('.')[0]), 'pki.qehc.edu.sa')) {
  $want = "*:80:$h"
  $exists = $false
  foreach ($b in $have) { if ($b -ieq $want) { $exists = $true; break } }
  if (-not $exists) {
    try {
      New-WebBinding -Name $CrlSiteName -Protocol http -Port 80 -HostHeader $h
      Step "Added binding $want"
      $have += $want
    } catch {
      Step "BIND_SKIP $want $($_.Exception.Message)"
    }
  }
}

# Virtual directory /CertEnroll
$vdir = Get-WebVirtualDirectory -Site $CrlSiteName -Name 'CertEnroll' -ErrorAction SilentlyContinue
if (-not $vdir) {
  New-WebVirtualDirectory -Site $CrlSiteName -Name 'CertEnroll' -PhysicalPath $VdirPhysicalPath | Out-Null
  Step "Created VDIR /CertEnroll -> $VdirPhysicalPath"
} else {
  Set-ItemProperty "IIS:\Sites\$CrlSiteName\CertEnroll" -Name physicalPath -Value $VdirPhysicalPath
  Step "Updated VDIR /CertEnroll -> $VdirPhysicalPath"
}

# Fallback on Default Web Site for IP/catchall clients
if (Get-Website -Name 'Default Web Site' -ErrorAction SilentlyContinue) {
  $dv = Get-WebVirtualDirectory -Site 'Default Web Site' -Name 'CertEnroll' -ErrorAction SilentlyContinue
  if (-not $dv) {
    New-WebVirtualDirectory -Site 'Default Web Site' -Name 'CertEnroll' -PhysicalPath $VdirPhysicalPath -ErrorAction SilentlyContinue | Out-Null
    Step 'Fallback VDIR on Default Web Site/CertEnroll'
  } else {
    Set-ItemProperty 'IIS:\Sites\Default Web Site\CertEnroll' -Name physicalPath -Value $VdirPhysicalPath -ErrorAction SilentlyContinue
  }
}

# NTFS read for IIS
icacls $SiteRoot /grant 'IIS_IUSRS:(OI)(CI)(RX)' 'IUSR:(OI)(CI)(RX)' 'NT AUTHORITY\Authenticated Users:(OI)(CI)(RX)' | Out-Null
icacls $VdirPhysicalPath /grant 'IIS_IUSRS:(OI)(CI)(RX)' 'IUSR:(OI)(CI)(RX)' 'NT AUTHORITY\Authenticated Users:(OI)(CI)(RX)' | Out-Null
icacls $CertEnroll /grant 'IIS_IUSRS:(OI)(CI)(RX)' 'IUSR:(OI)(CI)(RX)' | Out-Null

Set-WebConfigurationProperty -PSPath "IIS:\Sites\$CrlSiteName" -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name enabled -Value $true -ErrorAction SilentlyContinue

Start-Website -Name $CrlSiteName -ErrorAction SilentlyContinue
try { Restart-WebAppPool -Name (Get-Item "IIS:\Sites\$CrlSiteName").applicationPool } catch {}
Start-Sleep -Seconds 3

# --- HTTP repro + W3C parse ---
function Invoke-Probe([string]$Url) {
  $o = [ordered]@{ Url = $Url; Status = $null; Length = $null }
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
    $o.Status = [int]$r.StatusCode; $o.Length = $r.RawContentLength
  } catch {
    try { $o.Status = [int]$_.Exception.Response.StatusCode } catch {}
  }
  return [pscustomobject]$o
}

$crlUrl = "http://$ComputerDns/CertEnroll/$CACommonName.crl"
$aiaUrl = "http://$ComputerDns/CertEnroll/$ComputerDns`_$CACommonName.crt"
$probeUrl = "http://$ComputerDns/CertEnroll/$probeName"

$null = Invoke-Probe $probeUrl
$null = Invoke-Probe $crlUrl
$null = Invoke-Probe 'http://127.0.0.1/iisstart.htm'
Start-Sleep -Seconds 2

$handledBy = 'UNKNOWN'
$iisSiteId = 'UNKNOWN'
$http404Sub = 'N/A'
$logDirs = Get-ChildItem 'C:\inetpub\logs\LogFiles' -Directory -ErrorAction SilentlyContinue
foreach ($d in $logDirs) {
  if ($d.Name -match 'W3SVC(\d+)') {
    $candidateId = $Matches[1]
    $logs = Get-ChildItem $d.FullName -Filter '*.log' -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 2
    foreach ($log in $logs) {
      foreach ($line in (Get-Content $log.FullName -Tail 60 -EA SilentlyContinue)) {
        if ($line -like '#*' -or $line -notmatch 'CertEnroll') { continue }
        Step "W3C $($d.Name) :: $line"
        $iisSiteId = $candidateId
        $siteObj = Get-Website | Where-Object { $_.id -eq [int]$iisSiteId } | Select-Object -First 1
        if ($siteObj) { $handledBy = $siteObj.Name }
        $parts = $line -split '\s+'
        for ($i = 0; $i -lt $parts.Count; $i++) {
          if ($parts[$i] -match '^\d{3}$' -and $i + 2 -lt $parts.Count -and $parts[$i + 1] -match '^\d+$') {
            if ($parts[$i] -eq '404') { $http404Sub = $parts[$i + 1] }
            break
          }
        }
      }
    }
  }
}
if ($handledBy -eq 'UNKNOWN') {
  Get-Website | ForEach-Object {
    foreach ($b in (Get-WebBinding -Name $_.Name -Protocol http -EA SilentlyContinue)) {
      $parts = $b.bindingInformation -split ':', 3
      $hdr = if ($parts.Count -ge 3) { $parts[2] } else { '' }
      if ($hdr -eq $ComputerDns) {
        $handledBy = $_.Name
        $iisSiteId = [string]$_.id
      }
    }
  }
}

# --- Verify ---
function Assert-HttpOk([string]$Url, [string]$Label, [scriptblock]$Validate) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
  } catch {
    $code = $null
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    Fail "$Label HTTP_FAIL url=$Url code=$code err=$($_.Exception.Message)"
  }
  if ($r.StatusCode -ne 200) { Fail "$Label status=$($r.StatusCode) url=$Url" }
  if ($r.RawContentLength -lt 64) { Fail "$Label body too small ($($r.RawContentLength)) url=$Url" }
  & $Validate $r
  Step "$Label=200 len=$($r.RawContentLength) ct=$($r.Headers['Content-Type'])"
}

$probe = Invoke-Probe $probeUrl
Step ("PROBE_TXT status={0} len={1}" -f $probe.Status, $probe.Length)
if ($probe.Status -ne 200) { Fail "Probe .txt still $($probe.Status) — static content still broken" }

Assert-HttpOk -Url $crlUrl -Label 'HTTP_CRL' -Validate {
  param($r)
  $tmp = Join-Path $env:TEMP 'qka-http-check.crl'
  [IO.File]::WriteAllBytes($tmp, $r.Content)
  $dump = & certutil -dump $tmp 2>&1 | Out-String
  if ($dump -notmatch 'X509 CRL|Certificate Revocation List|CRL') { Fail "HTTP_CRL content is not a CRL" }
}
Assert-HttpOk -Url $aiaUrl -Label 'HTTP_AIA' -Validate {
  param($r)
  $tmp = Join-Path $env:TEMP 'qka-http-check.crt'
  [IO.File]::WriteAllBytes($tmp, $r.Content)
  $dump = & certutil -dump $tmp 2>&1 | Out-String
  if ($dump -notmatch 'QEHC-Internal-Root-CA') { Fail "HTTP_AIA content is not the CA certificate" }
}

# certutil URL fetch
$crlTmp = Join-Path $env:TEMP 'qka-cu-crl.crl'
$aiaTmp = Join-Path $env:TEMP 'qka-cu-aia.crt'
Remove-Item $crlTmp, $aiaTmp -Force -EA SilentlyContinue
& certutil -f -url $crlUrl $crlTmp 2>&1 | Out-Null
& certutil -f -url $aiaUrl $aiaTmp 2>&1 | Out-Null
$cuOk = (Test-Path $crlTmp) -and ((Get-Item $crlTmp).Length -gt 64) -and (Test-Path $aiaTmp) -and ((Get-Item $aiaTmp).Length -gt 64)
Step ("CERTUTIL_VALIDATION={0}" -f ($(if ($cuOk) { 'PASS' } else { 'FAIL' })))
if (-not $cuOk) { Fail 'certutil -url validation failed' }

$fixApplied = if ($missing -contains 'Web-Static-Content') {
  "Installed Web-Static-Content (+ $($missing -join ', ')); ensured QEHC-PKI-CRL host bindings + /CertEnroll VDIR; MIME; ACLs"
} else {
  "Features already present; ensured QEHC-PKI-CRL host bindings + /CertEnroll VDIR; MIME; ACLs"
}

Step '=== REPORT ==='
Step "REQUEST_HANDLED_BY_SITE=$handledBy"
Step "IIS_SITE_ID=$iisSiteId"
Step "HTTP_404_SUBSTATUS=$http404Sub"
Step "ROOT_CAUSE=$rootCause"
Step "FIX_APPLIED=$fixApplied"
Step 'HTTP_CRL=200'
Step 'HTTP_AIA=200'
Step 'CERTUTIL_VALIDATION=PASS'
Step 'PHASE2_READY=YES'
Step 'RESULT=SUCCESS'

$script:Report | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "REPORT_FILE=$ReportPath"
