<#
.SYNOPSIS
  IIS SNI cutover to an already-issued enterprise leaf (no CA reinstall, no new enroll).

.DESCRIPTION
  Default: refuses cutover unless a local CA private-key + database backup is present AND
  an off-server backup marker file exists.

  Override (auditable): -IAcceptProceedWithoutOffServerCaBackup logs who/when/why to
  C:\QehcAI\Temp\qka-ca-backups\OFFSERVER-BACKUP-OVERRIDE.log and proceeds. Local CA
  backup under C:\QehcAI\Temp\QKA-CA-Backup is never deleted.

  Does not modify unrelated IIS sites. Preserves self-signed pilot cert in the store.
#>
[CmdletBinding()]
param(
  [string]$CertificateThumbprint = 'EEFD0AB6F6A4C18944D7934960B9DE560BB77E9F',
  [string]$HostName = 'qka-agent.qehc.edu.sa',
  [string]$CACommonName = 'QEHC-Internal-Root-CA',
  [string]$ComputerDns = 'QEC-AI-MCP.qehc.edu.sa',
  [string]$PilotThumbprint = 'D030168213D1F589D17FC170A70A4B7340A65BA8',
  [string]$LocalCaBackupRoot = 'C:\QehcAI\Temp\QKA-CA-Backup',
  [string]$OffServerMarkerPath = 'C:\QehcAI\Temp\qka-ca-backups\OFFSERVER-CA-BACKUP-VERIFIED.txt',
  [string]$OverrideLogPath = 'C:\QehcAI\Temp\qka-ca-backups\OFFSERVER-BACKUP-OVERRIDE.log',
  [string]$IisBackupRoot = 'C:\QehcAI\Temp\qka-iis-backups',
  [string]$ReportPath = 'C:\QehcAI\Temp\qka-ca-backups\cutover-final-report.txt',
  [string]$OverrideReason = 'Administrator accepted proceeding without verified off-server CA backup for QKA HTTPS cutover',
  # Required for cutover when off-server marker is missing
  [switch]$IConfirmIisCutover,
  [switch]$IAcceptProceedWithoutOffServerCaBackup
)

$ErrorActionPreference = 'Stop'
function Step([string]$m) { Write-Host "[QKA-CUTOVER] $m" }
function Fail([string]$m) { Write-Host "FAILED: $m"; throw $m }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail 'Run elevated as local Administrator.' }
if (-not $IConfirmIisCutover) {
  Fail 'Refusing cutover without -IConfirmIisCutover (explicit Phase 3 approval).'
}

New-Item -ItemType Directory -Force -Path (Split-Path $ReportPath), $IisBackupRoot, (Split-Path $OverrideLogPath) | Out-Null
$report = [System.Collections.Generic.List[string]]::new()
function Rep([string]$m) { Step $m; $report.Add($m) | Out-Null }

# --- 1. Preserve / require local CA backup; gate off-server ---
$p12 = Join-Path $LocalCaBackupRoot 'QEHC-Internal-Root-CA.p12'
$edb = Join-Path $LocalCaBackupRoot 'DataBase\QEHC-Internal-Root-CA.edb'
if (-not (Test-Path $p12)) { Fail "Local CA private-key backup missing: $p12 (will not delete; create via certutil -backupKey first)" }
if (-not (Test-Path $edb)) { Fail "Local CA database backup missing: $edb" }
Rep "LOCAL_CA_BACKUP_PRESERVED p12=$p12 ($((Get-Item $p12).Length) bytes) edb=$edb ($((Get-Item $edb).Length) bytes)"

$offServerOk = Test-Path $OffServerMarkerPath
if ($offServerOk) {
  Rep "OFFSERVER_BACKUP_MARKER=PRESENT path=$OffServerMarkerPath"
} elseif ($IAcceptProceedWithoutOffServerCaBackup) {
  $who = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  $when = (Get-Date).ToString('o')
  $audit = @"
[$when] OVERRIDE_ACCEPTED
User=$who
Host=$env:COMPUTERNAME
Switch=-IAcceptProceedWithoutOffServerCaBackup
Reason=$OverrideReason
LocalBackup=$LocalCaBackupRoot
Action=IIS_SNI_CUTOVER thumb=$CertificateThumbprint host=$HostName
NOTE=Default safety check remains: without this switch, cutover refuses when $OffServerMarkerPath is absent.
"@
  Add-Content -Path $OverrideLogPath -Value $audit -Encoding UTF8
  Rep "OFFSERVER_BACKUP_OVERRIDE=ACCEPTED audit=$OverrideLogPath user=$who"
} else {
  Fail @"
Off-server CA backup not verified (missing $OffServerMarkerPath).
Create that marker after copying $LocalCaBackupRoot offline, OR pass -IAcceptProceedWithoutOffServerCaBackup for an auditable one-time override.
"@
}

# --- 2/4. Verify issued leaf (reuse; do not enroll) ---
$tp = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
$leaf = Get-Item -LiteralPath "Cert:\LocalMachine\My\$tp" -ErrorAction SilentlyContinue
if (-not $leaf) { Fail "Certificate $tp not in LocalMachine\My" }
if (-not $leaf.HasPrivateKey) { Fail "Certificate $tp HasPrivateKey=False" }
if ($leaf.Issuer -notmatch [regex]::Escape($CACommonName)) { Fail "Issuer mismatch: $($leaf.Issuer)" }
$sanExt = $leaf.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
$san = if ($sanExt) { $sanExt.Format($false) } else { '' }
if ($san -notmatch [regex]::Escape($HostName) -and $leaf.Subject -notmatch [regex]::Escape("CN=$HostName")) {
  Fail "SAN/CN missing $HostName (SAN=$san Subject=$($leaf.Subject))"
}
$eku = @($leaf.EnhancedKeyUsageList | ForEach-Object { $_.FriendlyName }) -join ', '
if ($eku -notmatch 'Server Authentication') { Fail "Missing Server Authentication EKU (got $eku)" }
$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::Online
$chain.ChainPolicy.RevocationFlag = [System.Security.Cryptography.X509Certificates.X509RevocationFlag]::ExcludeRoot
$chainOk = $chain.Build($leaf)
$chainStatuses = @($chain.ChainStatus | ForEach-Object { "$($_.Status):$($_.StatusInformation.Trim())" }) -join '; '
if (-not $chainOk) { Fail "Chain/revocation failed: $chainStatuses" }
Rep "LEAF_OK tp=$tp SAN=$san EKU=$eku HasPK=True CHAIN_VALID=True"

$pilot = Get-Item -LiteralPath "Cert:\LocalMachine\My\$PilotThumbprint" -ErrorAction SilentlyContinue
if (-not $pilot) { Rep "WARN_PILOT_MISSING $PilotThumbprint (rollback cert not in store)" }
else { Rep "PILOT_PRESERVED=$PilotThumbprint HasPK=$($pilot.HasPrivateKey)" }

# --- 3/5. Backup current binding + IIS config, then cutover ---
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bindBefore = netsh http show sslcert hostnameport="$HostName`:443" | Out-String
$bindBackup = Join-Path $IisBackupRoot "sslcert-$HostName-443-before-$stamp.txt"
Set-Content -Path $bindBackup -Value $bindBefore -Encoding UTF8
Rep "IIS_BINDING_BACKUP=$bindBackup"
if ($bindBefore -match 'Certificate Hash\s+:\s*([0-9a-fA-F]+)') {
  Rep "IIS_BINDING_BEFORE=$($Matches[1].ToUpperInvariant())"
}

# Dedicated installer: backs up applicationHost + updates only QKA-Agent-HTTPS / this hostnameport
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Install-QkaAgentIisHttps.ps1' `
  -CertificateThumbprint $tp -HostName $HostName
if ($LASTEXITCODE -ne 0) { Fail "Install-QkaAgentIisHttps.ps1 failed exit=$LASTEXITCODE" }

$bindAfter = netsh http show sslcert hostnameport="$HostName`:443" | Out-String
if ($bindAfter -notmatch 'Certificate Hash\s+:\s*([0-9a-fA-F]+)') { Fail 'SNI binding missing after cutover' }
$active = $Matches[1].ToUpperInvariant()
if ($active -ne $tp) { Fail "Active hash $active != expected $tp" }
Rep "IIS_CUTOVER=SUCCESS active=$active"

# --- 7. Automatic renewal + rebind task ---
$renewPath = 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Update-QkaAgentIisBindingFromStore.ps1'
if (-not (Test-Path $renewPath)) { Fail "Missing renewal script $renewPath" }
$taskName = 'QKA-Agent-HTTPS-Binding-Renew'
cmd /c "schtasks /Delete /TN `"$taskName`" /F >nul 2>&1" | Out-Null
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$renewPath`""
$create1 = cmd /c "schtasks /Create /TN `"$taskName`" /SC DAILY /ST 04:15 /RU SYSTEM /RL HIGHEST /TR `"$tr`" /F"
if ($LASTEXITCODE -ne 0) { Fail "schtasks create $taskName failed: $create1" }
# Weekly best-effort template renew + binding refresh
$enrollTask = 'QKA-Agent-HTTPS-Cert-Renew'
$enrollScript = 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Invoke-QkaAgentHttpsCertAutoEnroll.ps1'
$enrollCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$enrollScript`""
cmd /c "schtasks /Delete /TN `"$enrollTask`" /F >nul 2>&1" | Out-Null
$create2 = cmd /c "schtasks /Create /TN `"$enrollTask`" /SC WEEKLY /D SUN /ST 03:45 /RU SYSTEM /RL HIGHEST /TR `"$enrollCmd`" /F"
if ($LASTEXITCODE -ne 0) { Fail "schtasks create $enrollTask failed: $create2" }
& $renewPath | ForEach-Object { Rep "RENEW_CHECK $_" }
Rep "AUTO_RENEWAL=BINDING_TASK=$taskName; ENROLL_TASK=$enrollTask"

# --- 6. Verify HTTPS / CRL / AIA / trust path ---
Start-Sleep -Seconds 2
function Probe([string]$Url) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
    return @{ Ok = $true; Status = [int]$r.StatusCode; Body = $r.Content; Len = $r.RawContentLength }
  } catch {
    $c = $null; try { $c = [int]$_.Exception.Response.StatusCode } catch {}
    return @{ Ok = $false; Status = $c; Error = $_.Exception.Message }
  }
}
$health = Probe "https://$HostName/health"
$bodyText = ("{0}" -f $health.Body) -replace '\s+', ' '
if ($bodyText.Length -gt 80) { $bodyText = $bodyText.Substring(0, 80) }
Rep ("HTTPS_HEALTH status={0} body={1}" -f $health.Status, $bodyText)
if (-not $health.Ok -or $health.Status -ne 200) { Fail 'HTTPS /health failed after cutover' }

# TLS cert presented
try {
  $tcp = New-Object Net.Sockets.TcpClient($HostName, 443)
  $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, ({ $true }))
  $ssl.AuthenticateAsClient($HostName)
  $remote = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
  Rep "TLS_PRESENTED_TP=$($remote.Thumbprint) Issuer=$($remote.Issuer)"
  if ($remote.Thumbprint.ToUpperInvariant() -ne $tp) { Fail "TLS presented $($remote.Thumbprint) != $tp" }
  $ssl.Close(); $tcp.Close()
} catch {
  Rep "TLS_PROBE_WARN=$($_.Exception.Message)"
}

$crl = Probe "http://$ComputerDns/CertEnroll/$CACommonName.crl"
$aia = Probe "http://$ComputerDns/CertEnroll/$ComputerDns`_$CACommonName.crt"
Rep "CRL_HTTP=$($crl.Status) AIA_HTTP=$($aia.Status)"
if ($crl.Status -ne 200 -or $aia.Status -ne 200) { Fail 'CRL/AIA HTTP not 200' }

# Domain trust without manual install: enterprise Root published in AD
$cfg = (Get-ADRootDSE).configurationNamingContext
$adRoot = Get-ADObject -Identity "CN=$CACommonName,CN=Certification Authorities,CN=Public Key Services,CN=Services,$cfg" -ErrorAction SilentlyContinue
$entRoot = (certutil -store -enterprise Root 2>&1 | Out-String) -match '3ed4290069c19ec6e8d3a97991bc996c3716155c'
Rep "DOMAIN_TRUST AD_CA_OBJECT=$([bool]$adRoot) ENTERPRISE_ROOT_STORE=$entRoot (no manual client install required for domain-joined PCs)"

# --- 9. WSIT status (read-only) ---
$wsit = @{ WSIT03 = 'UNKNOWN'; WSIT04 = 'UNKNOWN' }
try {
  $psql = @('C:\Program Files\PostgreSQL\18\bin\psql.exe', 'C:\Program Files\PostgreSQL\16\bin\psql.exe') |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($psql) {
    $map = @{ 0 = 'REQUESTED'; 1 = 'APPROVED'; 2 = 'SCHEDULED'; 3 = 'WAITING_COMPUTER'; 4 = 'INSTALLING'; 5 = 'INSTALLED_REGISTERED'; 6 = 'FAILED'; 7 = 'CANCELLED'; 8 = 'TIMED_OUT' }
    $rows = & $psql -h 127.0.0.1 -p 55432 -U qka_test -d qka_next_dev -t -A -F '|' -c @"
SELECT DISTINCT ON (w.computer_name) w.computer_name, j.status, j.last_result
FROM qka_next_agent_deployment_job j
JOIN qka_next_ad_workstation w ON w.id = j.workstation_id
WHERE w.computer_name IN ('WSIT03','WSIT04')
ORDER BY w.computer_name, j.updated_at DESC;
"@ 2>&1
    foreach ($line in @($rows)) {
      if ($line -match '^(WSIT0[34])\|(\d+)\|(.*)$') {
        $name = $Matches[1]; $stNum = [int]$Matches[2]
        $wsit[$name] = "{0} (status={1}, last_result={2})" -f $map[$stNum], $stNum, $Matches[3]
      }
    }
  }
} catch {
  Rep "WSIT_QUERY_WARN=$($_.Exception.Message)"
}
Rep "WSIT03=$($wsit.WSIT03)"
Rep "WSIT04=$($wsit.WSIT04)"
Rep 'WSIT_ACTION=none (timed-out jobs not cancelled/retried)'

$readyUi = ($health.Ok -and $active -eq $tp -and $crl.Status -eq 200 -and $entRoot)
Rep '=== REPORT ==='
Rep "IIS_CUTOVER=SUCCESS"
Rep "ACTIVE_CERTIFICATE=$active"
Rep "HTTPS_HEALTH=200"
Rep "DOMAIN_TRUST=$(if ($entRoot -and $adRoot) { 'ENTERPRISE_ROOT_PUBLISHED' } else { 'INCOMPLETE' })"
Rep "AUTO_RENEWAL=CONFIGURED"
Rep "WSIT03=$($wsit.WSIT03)"
Rep "WSIT04=$($wsit.WSIT04)"
Rep "READY_FOR_UI_DEPLOYMENT=$readyUi"
Rep 'RESULT=SUCCESS'

$report | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "REPORT_FILE=$ReportPath"
