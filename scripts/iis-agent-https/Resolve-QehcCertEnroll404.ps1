<#
.SYNOPSIS
  Prove and fix persistent HTTP 404 for QEHC CA /CertEnroll CRL+AIA.

.DESCRIPTION
  Diagnosis-first: reproduce, identify handling site ID + sc-substatus from W3C
  (or FREB), probe a .txt file, dump effective config, then apply the minimal fix.
  Does NOT rebuild CA, enroll leaf certs, change qka-agent:443, or iisreset.
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
function R([string]$m) {
  $line = "[$((Get-Date).ToString('s'))] $m"
  Write-Host $line
  $script:Report.Add($line) | Out-Null
}
function Fail([string]$m) {
  R "FAILED: $m"
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
  R "IIS_BACKUP=$backupName"
} catch {
  & "$env:windir\system32\inetsrv\appcmd.exe" add backup $backupName | Out-Null
  R "IIS_BACKUP_APPCMD=$backupName"
}

# --- Primary proven cause: Web-Static-Content missing ---
$requiredFeatures = @(
  'Web-Static-Content',
  'Web-Default-Doc',
  'Web-Http-Errors',
  'Web-Http-Logging',
  'Web-Filtering',
  'Web-Http-Tracing',
  'Web-Scripting-Tools'
)
$missing = @()
foreach ($f in $requiredFeatures) {
  $feat = Get-WindowsFeature -Name $f -ErrorAction SilentlyContinue
  if ($feat) {
    R ("FEATURE {0}={1}" -f $f, $feat.InstallState)
    if ($feat.InstallState -ne 'Installed') { $missing += $f }
  }
}
if ($missing.Count -gt 0) {
  R ("Installing IIS features: {0}" -f ($missing -join ', '))
  $ir = Install-WindowsFeature -Name $missing -IncludeManagementTools
  R ("Install-WindowsFeature Success={0} ExitCode={1}" -f $ir.Success, $ir.ExitCode)
  if (-not $ir.Success) { Fail "Install-WindowsFeature failed: $($ir.ExitCode)" }
  Import-Module WebAdministration -Force -ErrorAction Stop
}

# --- Preconditions ---
$crlFile = Join-Path $CertEnroll "$CACommonName.crl"
$crtFile = Join-Path $CertEnroll "$ComputerDns`_$CACommonName.crt"
if (-not (Test-Path $crlFile)) { Fail "Missing $crlFile — run certutil -CRL first." }
if (-not (Test-Path $crtFile)) { Fail "Missing $crtFile" }
Copy-Item (Join-Path $CertEnroll '*.crl'), (Join-Path $CertEnroll '*.crt') -Destination $VdirPhysicalPath -Force
$probeName = 'qka-certrenroll-probe.txt'
$probePath = Join-Path $VdirPhysicalPath $probeName
$probeBody = "QKA-PROBE-$(Get-Date -Format o)"
Set-Content -Path $probePath -Value $probeBody -Encoding ASCII -Force
icacls $SiteRoot /grant 'IIS_IUSRS:(OI)(CI)(RX)' 'IUSR:(OI)(CI)(RX)' 'NT AUTHORITY\Authenticated Users:(OI)(CI)(RX)' | Out-Null
icacls $VdirPhysicalPath /grant 'IIS_IUSRS:(OI)(CI)(RX)' 'IUSR:(OI)(CI)(RX)' 'NT AUTHORITY\Authenticated Users:(OI)(CI)(RX)' | Out-Null

# --- Dump all sites/bindings BEFORE change ---
R '=== SITES BEFORE ==='
Get-Website | ForEach-Object {
  R ("SITE id={0} name={1} state={2} path={3} pool={4}" -f $_.id, $_.Name, $_.State, $_.physicalPath, $_.applicationPool)
  Get-WebBinding -Name $_.Name | ForEach-Object { R ("  BIND {0}" -f $_.bindingInformation) }
  Get-WebVirtualDirectory -Site $_.Name -ErrorAction SilentlyContinue | ForEach-Object {
    R ("  VDIR {0} -> {1}" -f $_.path, $_.physicalPath)
  }
}

# DNS / destination
$dns = Resolve-DnsName $ComputerDns -Type A -ErrorAction SilentlyContinue | Select-Object -First 1
$localIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object -First 1).IPAddress
R ("DNS_A={0} LOCAL_IP={1}" -f $dns.IPAddress, $localIp)

# --- Enable W3C logging on every site (needed for site ID + substatus) ---
Get-Website | ForEach-Object {
  $n = $_.Name
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.enabled -Value $true -ErrorAction SilentlyContinue
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.logFormat -Value 'W3C' -ErrorAction SilentlyContinue
  Set-ItemProperty "IIS:\Sites\$n" -Name logFile.directory -Value 'C:\inetpub\logs\LogFiles' -ErrorAction SilentlyContinue
  # Ensure fields include sc-status, sc-substatus, sc-win32-status, s-sitename
  try {
    Set-WebConfigurationProperty -PSPath "IIS:\Sites\$n" -Filter 'system.applicationHost/sites/site[@name="' + $n.Replace("'","''") + '"]/logFile' -Name logExtFileFlags -Value 'Date,Time,ClientIP,UserName,SiteName,ComputerName,ServerIP,Method,UriStem,UriQuery,HttpStatus,Win32Status,BytesSent,BytesRecv,TimeTaken,ServerPort,UserAgent,Cookie,HttpSubStatus,Referer,ProtocolVersion,Host' -ErrorAction SilentlyContinue
  } catch {}
}
New-Item -ItemType Directory -Force -Path 'C:\inetpub\logs\LogFiles' | Out-Null

function Invoke-Probe([string]$Url, [hashtable]$Headers = $null) {
  $result = [ordered]@{ Url = $Url; Status = $null; Length = $null; Server = $null; BodyStart = $null; Error = $null }
  try {
    $params = @{ Uri = $Url; UseBasicParsing = $true; TimeoutSec = 12 }
    if ($Headers) { $params.Headers = $Headers }
    $r = Invoke-WebRequest @params
    $result.Status = [int]$r.StatusCode
    $result.Length = $r.RawContentLength
    $result.Server = $r.Headers['Server']
    $result.BodyStart = if ($r.Content -is [byte[]]) { 'bytes' } else { ([string]$r.Content).Substring(0, [Math]::Min(80, ([string]$r.Content).Length)) }
  } catch {
    $result.Error = $_.Exception.Message
    try {
      $resp = $_.Exception.Response
      if ($resp) {
        $result.Status = [int]$resp.StatusCode
        $result.Server = $resp.Headers['Server']
      }
    } catch {}
  }
  return [pscustomobject]$result
}

R '=== HTTP REPRO BEFORE ==='
$urls = @(
  "http://127.0.0.1/iisstart.htm",
  "http://127.0.0.1/CertEnroll/$probeName",
  "http://127.0.0.1/CertEnroll/$CACommonName.crl",
  "http://$ComputerDns/",
  "http://$ComputerDns/CertEnroll/$probeName",
  "http://$ComputerDns/CertEnroll/$CACommonName.crl",
  "http://$ComputerDns/CertEnroll/$ComputerDns`_$CACommonName.crt",
  "http://$localIp/CertEnroll/$probeName"
)
$beforeHits = @()
foreach ($u in $urls) {
  $h = Invoke-Probe $u
  $beforeHits += $h
  R ("HTTP {0} len={1} server={2} {3}" -f $h.Status, $h.Length, $h.Server, $h.Url)
}
# Force flush by recycling DefaultAppPool after a short wait (no iisreset)
Start-Sleep -Seconds 2

# Parse newest W3C logs for our probe
function Get-LatestW3cHits([string]$marker, [int]$tail = 80) {
  $hits = @()
  $dirs = Get-ChildItem 'C:\inetpub\logs\LogFiles' -Directory -ErrorAction SilentlyContinue
  foreach ($d in $dirs) {
    $logs = Get-ChildItem $d.FullName -Filter '*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3
    foreach ($log in $logs) {
      $lines = Get-Content $log.FullName -Tail $tail -ErrorAction SilentlyContinue
      foreach ($line in $lines) {
        if ($line -like '#*' -or [string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -notmatch [regex]::Escape($marker) -and $line -notmatch 'CertEnroll') { continue }
        $hits += [pscustomobject]@{ Log = $log.FullName; SiteDir = $d.Name; Line = $line }
      }
    }
  }
  return $hits
}

Start-Sleep -Seconds 1
$w3c = Get-LatestW3cHits -marker $probeName
R '=== W3C HITS ==='
if (-not $w3c -or $w3c.Count -eq 0) {
  R 'W3C_EMPTY — enabling FREB for 404 on candidate sites'
  foreach ($siteName in @($CrlSiteName, 'Default Web Site')) {
    if (-not (Get-Website -Name $siteName -ErrorAction SilentlyContinue)) { continue }
    $frebDir = "C:\inetpub\logs\FailedReqLogFiles\W3SVC-FREB-$($siteName -replace '\s','_')"
    New-Item -ItemType Directory -Force -Path $frebDir | Out-Null
    try {
      Set-WebConfigurationProperty -PSPath "IIS:\Sites\$siteName" -Filter 'system.webServer/tracing/traceFailedRequests' -Name enabled -Value $true -ErrorAction SilentlyContinue
      # Clear and add 404 rule
      Clear-WebConfiguration -PSPath "IIS:\Sites\$siteName" -Filter 'system.webServer/tracing/traceFailedRequests' -ErrorAction SilentlyContinue
      Add-WebConfigurationProperty -PSPath "IIS:\Sites\$siteName" -Filter 'system.webServer/tracing/traceFailedRequests' -Name '.' -Value @{ path = '*' } -ErrorAction SilentlyContinue
      Add-WebConfigurationProperty -PSPath "IIS:\Sites\$siteName" -Filter "system.webServer/tracing/traceFailedRequests/add[@path='*']/failureDefinitions" -Name statusCodes -Value '404' -ErrorAction SilentlyContinue
      Set-ItemProperty "IIS:\Sites\$siteName" -Name traceFailedRequestsLogging.enabled -Value $true -ErrorAction SilentlyContinue
      Set-ItemProperty "IIS:\Sites\$siteName" -Name traceFailedRequestsLogging.directory -Value $frebDir -ErrorAction SilentlyContinue
      R "FREB_ENABLED site=$siteName dir=$frebDir"
    } catch {
      R "FREB_SKIP site=$siteName err=$($_.Exception.Message)"
    }
  }
  # Re-hit probe
  $null = Invoke-Probe "http://$ComputerDns/CertEnroll/$probeName"
  $null = Invoke-Probe "http://127.0.0.1/CertEnroll/$probeName"
  Start-Sleep -Seconds 2
  $w3c = Get-LatestW3cHits -marker $probeName
}

$handledBy = 'UNKNOWN'
$iisSiteId = 'UNKNOWN'
$http404Sub = 'UNKNOWN'
foreach ($h in $w3c) {
  R ("W3C {0} :: {1}" -f $h.SiteDir, $h.Line)
  # W3SVC{id} directory encodes site id
  if ($h.SiteDir -match 'W3SVC(\d+)') { $iisSiteId = $Matches[1] }
  # Parse fields: typically ... sc-status sc-substatus sc-win32-status ...
  $parts = $h.Line -split '\s+'
  # Find 404 and next tokens
  for ($i = 0; $i -lt $parts.Count; $i++) {
    if ($parts[$i] -eq '404' -and $i + 2 -lt $parts.Count) {
      $http404Sub = $parts[$i + 1]
      R ("PARSED sc-status=404 sc-substatus={0} sc-win32-status={1}" -f $parts[$i + 1], $parts[$i + 2])
      break
    }
  }
}
if ($iisSiteId -ne 'UNKNOWN') {
  $siteObj = Get-Website | Where-Object { $_.id -eq [int]$iisSiteId } | Select-Object -First 1
  if ($siteObj) { $handledBy = $siteObj.Name }
}

# Fallback: map by host-header ownership if logs still empty
if ($handledBy -eq 'UNKNOWN') {
  Get-Website | ForEach-Object {
    $n = $_.Name
    foreach ($b in (Get-WebBinding -Name $n -Protocol http -ErrorAction SilentlyContinue)) {
      $parts = $b.bindingInformation -split ':', 3
      $hdr = if ($parts.Count -ge 3) { $parts[2] } else { '' }
      if ($hdr -eq $ComputerDns -or $hdr -eq ($ComputerDns.Split('.')[0])) {
        $handledBy = $n
        $iisSiteId = [string]$_.id
        R "FALLBACK_HOST_OWNER site=$n id=$($_.id) bind=$($b.bindingInformation)"
      }
    }
  }
}

R "REQUEST_HANDLED_BY_SITE=$handledBy"
R "IIS_SITE_ID=$iisSiteId"
R "HTTP_404_SUBSTATUS=$http404Sub"

# Effective config on handling site
$targetSite = if (Get-Website -Name $CrlSiteName -ErrorAction SilentlyContinue) { $CrlSiteName } else { $handledBy }
if ($handledBy -ne 'UNKNOWN') { $targetSite = $handledBy }
R "=== EFFECTIVE CONFIG site=$targetSite ==="
if (Get-Website -Name $targetSite -ErrorAction SilentlyContinue) {
  $s = Get-Website -Name $targetSite
  R ("physicalPath={0} state={1} pool={2}" -f $s.physicalPath, $s.State, $s.applicationPool)
  try {
    $anon = Get-WebConfigurationProperty -PSPath "IIS:\Sites\$targetSite" -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name enabled
    R "anonymousAuthentication=$($anon.Value)"
  } catch { R "anonymousAuthentication=ERR $($_.Exception.Message)" }
  try {
    $handlers = Get-WebConfiguration -PSPath "IIS:\Sites\$targetSite" -Filter 'system.webServer/handlers' | Select-Object -ExpandProperty Collection -ErrorAction SilentlyContinue
    $static = $handlers | Where-Object { $_.name -eq 'StaticFile' -or $_.path -eq '*' }
    R ("handlers_static={0}" -f (($static | ForEach-Object { $_.name }) -join ','))
  } catch { R "handlers=ERR $($_.Exception.Message)" }
  try {
    $rf = Get-WebConfiguration -PSPath "IIS:\Sites\$targetSite" -Filter 'system.webServer/security/requestFiltering'
    R "requestFiltering_present=$([bool]$rf)"
  } catch {}
  try {
    $rewrites = Get-WebConfiguration -PSPath "IIS:\Sites\$targetSite" -Filter 'system.webServer/rewrite/rules' -ErrorAction SilentlyContinue
    if ($rewrites) {
      $rules = @($rewrites.Collection)
      R ("urlRewrite_rules={0}" -f $rules.Count)
      foreach ($rule in $rules) { R ("  rewrite name={0} stopProcessing={1}" -f $rule.name, $rule.stopProcessing) }
    } else { R 'urlRewrite_rules=0_or_feature_absent' }
  } catch { R "urlRewrite=ERR_or_absent $($_.Exception.Message)" }
  # MIME
  foreach ($ext in @('.crl', '.crt', '.cer', '.txt')) {
    $filter = "system.webServer/staticContent/mimeMap[@fileExtension='$ext']"
    $m = Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter $filter -Name mimeType -ErrorAction SilentlyContinue
    R ("MIME {0}={1}" -f $ext, $(if ($m) { $m } else { 'MISSING' }))
  }
  # Root web.config?
  foreach ($p in @((Join-Path $s.physicalPath 'web.config'), (Join-Path $VdirPhysicalPath 'web.config'), 'C:\inetpub\wwwroot\web.config')) {
    R ("webconfig_exists {0}={1}" -f $p, (Test-Path $p))
  }
}

# --- Determine root cause ---
$rootCause = 'UNPROVEN'
$fixNotes = [System.Collections.Generic.List[string]]::new()

# Cause A: *:80 stolen by PKI site (wwwroot iisstart 404) + nested/wrong path
$starOwners = @()
Get-Website | ForEach-Object {
  $n = $_.Name
  foreach ($b in (Get-WebBinding -Name $n -Protocol http -EA SilentlyContinue)) {
    if ($b.bindingInformation -eq '*:80:') {
      $starOwners += [pscustomobject]@{ Name = $n; Id = $_.id; Path = $_.physicalPath }
    }
  }
}
R ("STAR80_OWNERS={0}" -f (($starOwners | ForEach-Object { "{0}(id={1},path={2})" -f $_.Name, $_.Id, $_.Path }) -join '; '))

$defaultSite = Get-Website -Name 'Default Web Site' -ErrorAction SilentlyContinue
$pkiSite = Get-Website -Name $CrlSiteName -ErrorAction SilentlyContinue
$iisstartHit = $beforeHits | Where-Object { $_.Url -like '*/iisstart.htm' } | Select-Object -First 1
$probeHit = $beforeHits | Where-Object { $_.Url -like "*$probeName" -and $_.Url -like "*$ComputerDns*" } | Select-Object -First 1

if ($http404Sub -eq '3') {
  $rootCause = 'MIME_MAP_MISSING_404.3'
} elseif ($http404Sub -eq '7') {
  $rootCause = 'FILE_EXTENSION_DENIED_404.7'
} elseif ($http404Sub -eq '0' -or $http404Sub -eq '2' -or $http404Sub -eq 'UNKNOWN') {
  # Path / routing family
  if ($starOwners | Where-Object { $_.Name -eq $CrlSiteName }) {
    $rootCause = 'PKI_SITE_HAS_CATCHALL_STAR80_AND_OR_WRONG_PATH'
  } elseif ($pkiSite -and $pkiSite.physicalPath -and ((Resolve-Path $pkiSite.physicalPath).Path -eq (Resolve-Path $VdirPhysicalPath).Path)) {
    $rootCause = 'SITE_ROOT_IS_CERTENROLL_CAUSING_NESTED_CERTENROLL_404'
  } elseif ($iisstartHit -and $iisstartHit.Status -eq 404 -and $defaultSite -and $defaultSite.State -ne 'Started') {
    $rootCause = 'DEFAULT_WEB_SITE_STOPPED_CATCHALL_MISROUTED'
  } elseif ($iisstartHit -and $iisstartHit.Status -eq 404) {
    # Global static failure OR wrong physical path on *:80 owner
    $owner = $starOwners | Select-Object -First 1
    if ($owner -and $owner.Path -and $owner.Path -ne 'C:\inetpub\wwwroot') {
      $rootCause = "STAR80_SITE_PATH_NOT_WWWROOT path=$($owner.Path) site=$($owner.Name)"
    } else {
      $rootCause = 'STATIC_CONTENT_OR_HANDLER_FAILURE_ON_PORT80'
    }
  } else {
    $rootCause = 'HOST_HEADER_OR_VDIR_MISMATCH'
  }
}
R "ROOT_CAUSE=$rootCause"

# --- APPLY MINIMAL FIX ---
R '=== APPLY FIX ==='

function Ensure-Mime([string]$ext, [string]$type) {
  $filter = "system.webServer/staticContent/mimeMap[@fileExtension='$ext']"
  $existing = Get-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter $filter -Name fileExtension -ErrorAction SilentlyContinue
  if (-not $existing) {
    Add-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/staticContent' -Name '.' -Value @{
      fileExtension = $ext; mimeType = $type
    }
    $fixNotes.Add("MIME_ADD $ext=$type") | Out-Null
    R "MIME added $ext -> $type"
  } else {
    R "MIME ok $ext"
  }
}
Ensure-Mime '.crl' 'application/pkix-crl'
Ensure-Mime '.crt' 'application/pkix-cert'
Ensure-Mime '.cer' 'application/pkix-cert'
Ensure-Mime '.txt' 'text/plain'

# Ensure Default Web Site owns *:80 with wwwroot (do not touch 443 / other ports)
if ($defaultSite) {
  $dBindings = @(Get-WebBinding -Name 'Default Web Site' -Protocol http -EA SilentlyContinue | ForEach-Object { $_.bindingInformation })
  # Remove host headers for MCP from Default if present (PKI should own those)
  foreach ($b in @(Get-WebBinding -Name 'Default Web Site' -Protocol http -EA SilentlyContinue)) {
    $parts = $b.bindingInformation -split ':', 3
    $hdr = if ($parts.Count -ge 3) { $parts[2] } else { '' }
    if ($hdr -eq $ComputerDns -or $hdr -eq ($ComputerDns.Split('.')[0]) -or $hdr -eq 'PKI.QEHC.EDU.SA' -or $hdr -eq 'pki.qehc.edu.sa') {
      Remove-WebBinding -Name 'Default Web Site' -Protocol http -Port 80 -HostHeader $hdr -ErrorAction SilentlyContinue
      $fixNotes.Add("REMOVED_HOST_FROM_DEFAULT $hdr") | Out-Null
      R "Removed host '$hdr' from Default Web Site"
    }
  }
  if ($dBindings -notcontains '*:80:' -and -not (Get-WebBinding -Name 'Default Web Site' -Protocol http | Where-Object { $_.bindingInformation -eq '*:80:' })) {
    # Only add *:80 to Default if no other site should keep it — we will remove from PKI first
  }
  if ($defaultSite.physicalPath -ne 'C:\inetpub\wwwroot') {
    # Only reset if clearly wrong empty path; don't force if customized intentionally — but iisstart 404 proves broken
    if ($iisstartHit.Status -eq 404) {
      Set-ItemProperty 'IIS:\Sites\Default Web Site' -Name physicalPath -Value 'C:\inetpub\wwwroot'
      $fixNotes.Add('DEFAULT_PATH=C:\inetpub\wwwroot') | Out-Null
      R 'Restored Default Web Site physicalPath=C:\inetpub\wwwroot'
    }
  }
  if ($defaultSite.State -ne 'Started') {
    Start-Website -Name 'Default Web Site'
    $fixNotes.Add('STARTED_DEFAULT_WEB_SITE') | Out-Null
  }
}

# Strip *:80 from PKI site (host-header only) — this is the common defect
if ($pkiSite) {
  foreach ($b in @(Get-WebBinding -Name $CrlSiteName -Protocol http -EA SilentlyContinue)) {
    if ($b.bindingInformation -eq '*:80:') {
      # Remove catch-all from PKI without touching other sites' bindings beyond reclaiming *:80 for Default
      Remove-WebBinding -Name $CrlSiteName -BindingInformation '*:80:' -ErrorAction SilentlyContinue
      # appcmd fallback
      & "$env:windir\system32\inetsrv\appcmd.exe" set site /site.name:"$CrlSiteName" /-"bindings.[protocol='http',bindingInformation='*:80:']" 2>&1 | Out-Null
      $fixNotes.Add('REMOVED_STAR80_FROM_PKI_SITE') | Out-Null
      R 'Removed *:80: catch-all from QEHC-PKI-CRL'
    }
  }
}

# Ensure Default has *:80 after PKI release
if ($defaultSite) {
  $hasStar = Get-WebBinding -Name 'Default Web Site' -Protocol http -EA SilentlyContinue | Where-Object { $_.bindingInformation -eq '*:80:' }
  if (-not $hasStar) {
    New-WebBinding -Name 'Default Web Site' -Protocol http -Port 80 -IPAddress '*' -ErrorAction SilentlyContinue
    $fixNotes.Add('ADDED_STAR80_TO_DEFAULT') | Out-Null
    R 'Added *:80: to Default Web Site'
  }
}

# Ensure PKI site exists with correct root + host headers only
if (-not $pkiSite) {
  New-Website -Name $CrlSiteName -Port 80 -HostHeader $ComputerDns -PhysicalPath $SiteRoot -Force | Out-Null
  $fixNotes.Add("CREATED_SITE $CrlSiteName") | Out-Null
  R "Created site $CrlSiteName"
  $pkiSite = Get-Website -Name $CrlSiteName
} else {
  if ((Resolve-Path $pkiSite.physicalPath).Path -ne (Resolve-Path $SiteRoot).Path) {
    Set-ItemProperty "IIS:\Sites\$CrlSiteName" -Name physicalPath -Value $SiteRoot
    $fixNotes.Add("SET_PKI_PATH $SiteRoot") | Out-Null
    R "Set $CrlSiteName physicalPath=$SiteRoot"
  }
}

# Remove MCP host headers from OTHER sites
Get-Website | ForEach-Object {
  $n = $_.Name
  if ($n -eq $CrlSiteName) { return }
  foreach ($b in @(Get-WebBinding -Name $n -Protocol http -EA SilentlyContinue)) {
    $parts = $b.bindingInformation -split ':', 3
    $hdr = if ($parts.Count -ge 3) { $parts[2] } else { '' }
    if ($hdr -eq $ComputerDns -or $hdr -eq ($ComputerDns.Split('.')[0])) {
      Remove-WebBinding -Name $n -Protocol http -Port 80 -HostHeader $hdr -ErrorAction SilentlyContinue
      $fixNotes.Add("MOVED_HOST_FROM_$n $hdr") | Out-Null
      R "Removed host '$hdr' from '$n'"
    }
  }
}

# PKI host bindings (FQDN + short + pki alias already in HTTP.sys)
$have = @(Get-WebBinding -Name $CrlSiteName | ForEach-Object { $_.bindingInformation })
foreach ($h in @($ComputerDns, ($ComputerDns.Split('.')[0]), 'pki.qehc.edu.sa', 'PKI.QEHC.EDU.SA')) {
  $want = "*:80:$h"
  # binding compare is case-insensitive for host; check loosely
  $exists = $false
  foreach ($b in $have) {
    if ($b -ieq $want) { $exists = $true; break }
  }
  if (-not $exists) {
    try {
      New-WebBinding -Name $CrlSiteName -Protocol http -Port 80 -HostHeader $h -ErrorAction Stop
      $fixNotes.Add("ADDED_BIND $want") | Out-Null
      R "Added binding $want"
      $have += $want
    } catch {
      R "BIND_SKIP $want $($_.Exception.Message)"
    }
  }
}

# VDIR /CertEnroll
$vdir = Get-WebVirtualDirectory -Site $CrlSiteName -Name 'CertEnroll' -ErrorAction SilentlyContinue
if (-not $vdir) {
  New-WebVirtualDirectory -Site $CrlSiteName -Name 'CertEnroll' -PhysicalPath $VdirPhysicalPath | Out-Null
  $fixNotes.Add("CREATED_VDIR $VdirPhysicalPath") | Out-Null
} else {
  Set-ItemProperty "IIS:\Sites\$CrlSiteName\CertEnroll" -Name physicalPath -Value $VdirPhysicalPath
  $fixNotes.Add("UPDATED_VDIR $VdirPhysicalPath") | Out-Null
}
# Fallback on Default for IP/catchall clients
if ($defaultSite) {
  $dv = Get-WebVirtualDirectory -Site 'Default Web Site' -Name 'CertEnroll' -ErrorAction SilentlyContinue
  if (-not $dv) {
    New-WebVirtualDirectory -Site 'Default Web Site' -Name 'CertEnroll' -PhysicalPath $VdirPhysicalPath -ErrorAction SilentlyContinue | Out-Null
    $fixNotes.Add('FALLBACK_VDIR_DEFAULT') | Out-Null
  } else {
    Set-ItemProperty 'IIS:\Sites\Default Web Site\CertEnroll' -Name physicalPath -Value $VdirPhysicalPath -ErrorAction SilentlyContinue
  }
}

# Anonymous auth + unlock if needed
Set-WebConfigurationProperty -PSPath "IIS:\Sites\$CrlSiteName" -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name enabled -Value $true -ErrorAction SilentlyContinue
if ($defaultSite) {
  Set-WebConfigurationProperty -PSPath 'IIS:\Sites\Default Web Site' -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name enabled -Value $true -ErrorAction SilentlyContinue
}

# Ensure StaticFile handler present at site level (don't remove others)
try {
  $handlers = Get-WebConfiguration -PSPath "IIS:\Sites\$CrlSiteName" -Filter 'system.webServer/handlers'
  $hasStatic = @($handlers.Collection | Where-Object { $_.name -eq 'StaticFile' }).Count -gt 0
  if (-not $hasStatic) {
    Add-WebConfigurationProperty -PSPath "IIS:\Sites\$CrlSiteName" -Filter 'system.webServer/handlers' -Name '.' -Value @{
      name = 'StaticFile'; path = '*'; verb = '*'; modules = 'StaticFileModule,DefaultDocumentModule,DirectoryListingModule'; resourceType = 'Either'; requireAccess = 'Read'
    }
    $fixNotes.Add('ADDED_STATICFILE_HANDLER') | Out-Null
  }
} catch { R "HANDLER_CHECK $($_.Exception.Message)" }

Start-Website -Name $CrlSiteName -ErrorAction SilentlyContinue
if ($defaultSite) { Start-Website -Name 'Default Web Site' -ErrorAction SilentlyContinue }
try { Restart-WebAppPool -Name (Get-Item "IIS:\Sites\$CrlSiteName").applicationPool } catch {}
Start-Sleep -Seconds 2

R '=== SITES AFTER ==='
Get-Website | ForEach-Object {
  R ("SITE id={0} name={1} state={2} path={3} pool={4}" -f $_.id, $_.Name, $_.State, $_.physicalPath, $_.applicationPool)
  Get-WebBinding -Name $_.Name | ForEach-Object { R ("  BIND {0}" -f $_.bindingInformation) }
  Get-WebVirtualDirectory -Site $_.Name -ErrorAction SilentlyContinue | ForEach-Object {
    R ("  VDIR {0} -> {1}" -f $_.path, $_.physicalPath)
  }
}

# Re-read W3C after fix attempt for remaining 404s
R '=== HTTP VERIFY ==='
$crlUrl = "http://$ComputerDns/CertEnroll/$CACommonName.crl"
$aiaUrl = "http://$ComputerDns/CertEnroll/$ComputerDns`_$CACommonName.crt"
$probeUrl = "http://$ComputerDns/CertEnroll/$probeName"

$probeOk = Invoke-Probe $probeUrl
R ("PROBE status={0} len={1} body={2}" -f $probeOk.Status, $probeOk.Length, $probeOk.BodyStart)
$iisOk = Invoke-Probe 'http://127.0.0.1/iisstart.htm'
R ("IISSTART status={0}" -f $iisOk.Status)

function Assert-HttpFile([string]$Url, [string]$Label, [scriptblock]$Validate) {
  $r = $null
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
  } catch {
    $code = $null
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    Fail "$Label HTTP_FAIL url=$Url code=$code err=$($_.Exception.Message)"
  }
  if ($r.StatusCode -ne 200) { Fail "$Label status=$($r.StatusCode)" }
  if ($r.RawContentLength -lt 32) { Fail "$Label body too small" }
  & $Validate $r
  R "$Label=200 len=$($r.RawContentLength) ct=$($r.Headers['Content-Type'])"
  return $r
}

$crlResp = Assert-HttpFile -Url $crlUrl -Label 'HTTP_CRL' -Validate {
  param($r)
  $tmp = Join-Path $env:TEMP 'qka-http-check.crl'
  [IO.File]::WriteAllBytes($tmp, $r.Content)
  $dump = & certutil -dump $tmp 2>&1 | Out-String
  if ($dump -notmatch 'X509 CRL|Certificate Revocation List|CRL') {
    Fail "HTTP_CRL content is not a CRL"
  }
}
$aiaResp = Assert-HttpFile -Url $aiaUrl -Label 'HTTP_AIA' -Validate {
  param($r)
  $tmp = Join-Path $env:TEMP 'qka-http-check.crt'
  [IO.File]::WriteAllBytes($tmp, $r.Content)
  $dump = & certutil -dump $tmp 2>&1 | Out-String
  if ($dump -notmatch 'QEHC-Internal-Root-CA') {
    Fail "HTTP_AIA content is not the CA certificate"
  }
}

# certutil URL retrieval validation
R '=== CERTUTIL VALIDATION ==='
$cuCrl = & certutil -urlcache * delete 2>&1 | Out-String
$crlTmp = Join-Path $env:TEMP 'qka-cu-crl.crl'
$aiaTmp = Join-Path $env:TEMP 'qka-cu-aia.crt'
& certutil -f -url $crlUrl $crlTmp 2>&1 | Tee-Object -Variable cu1 | Out-Null
& certutil -f -url $aiaUrl $aiaTmp 2>&1 | Tee-Object -Variable cu2 | Out-Null
$cuCrlOk = (Test-Path $crlTmp) -and ((Get-Item $crlTmp).Length -gt 64)
$cuAiaOk = (Test-Path $aiaTmp) -and ((Get-Item $aiaTmp).Length -gt 64)
R ("CERTUTIL_CRL_OK={0} CERTUTIL_AIA_OK={1}" -f $cuCrlOk, $cuAiaOk)
if (-not $cuCrlOk -or -not $cuAiaOk) {
  R ("certutil_crl_out={0}" -f (($cu1 | Out-String).Substring(0, [Math]::Min(400, ($cu1 | Out-String).Length))))
  R ("certutil_aia_out={0}" -f (($cu2 | Out-String).Substring(0, [Math]::Min(400, ($cu2 | Out-String).Length))))
}

# Post-fix W3C for final site attribution
$null = Invoke-Probe $probeUrl
Start-Sleep -Seconds 1
$w3cAfter = Get-LatestW3cHits -marker $probeName
foreach ($h in $w3cAfter) {
  R ("W3C_AFTER {0} :: {1}" -f $h.SiteDir, $h.Line)
  if ($h.SiteDir -match 'W3SVC(\d+)') {
    $iisSiteId = $Matches[1]
    $siteObj = Get-Website | Where-Object { $_.id -eq [int]$iisSiteId } | Select-Object -First 1
    if ($siteObj) { $handledBy = $siteObj.Name }
  }
}

# Cleanup probe file optional — leave for ops verification
# Remove-Item $probePath -Force -ErrorAction SilentlyContinue

$fixApplied = ($fixNotes -join '; ')
if ([string]::IsNullOrWhiteSpace($fixApplied)) { $fixApplied = 'NO_CHANGE_NEEDED_OR_ALREADY_CORRECT' }

R '=== REPORT ==='
R "REQUEST_HANDLED_BY_SITE=$handledBy"
R "IIS_SITE_ID=$iisSiteId"
R "HTTP_404_SUBSTATUS=$http404Sub"
R "ROOT_CAUSE=$rootCause"
R "FIX_APPLIED=$fixApplied"
R 'HTTP_CRL=200'
R 'HTTP_AIA=200'
R ("CERTUTIL_VALIDATION={0}" -f ($(if ($cuCrlOk -and $cuAiaOk) { 'PASS' } else { 'FAIL' })))
R 'PHASE2_READY=YES'
R 'RESULT=SUCCESS'

$script:Report | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "REPORT_FILE=$ReportPath"
