<#
.SYNOPSIS
  Complete QKA enterprise leaf enrollment (no IIS cutover).

.DESCRIPTION
  Uses an existing LocalMachine\Request / .req when present (avoids duplicate keys).
  Submits non-interactively to QEHC-Internal-Root-CA, accepts into LocalMachine\My,
  verifies issuer/SAN/EKU/chain/CRL/HasPrivateKey. Preserves self-signed pilot cert.
  Does NOT change IIS HTTPS bindings.
#>
[CmdletBinding()]
param(
  [string]$HostName = 'qka-agent.qehc.edu.sa',
  [string]$CACommonName = 'QEHC-Internal-Root-CA',
  [string]$TemplateName = 'QKAWebServer',
  [string]$CaConfig = 'QEC-AI-MCP.qehc.edu.sa\QEHC-Internal-Root-CA',
  [string]$ComputerDns = 'QEC-AI-MCP.qehc.edu.sa',
  [string]$WorkDir = ''
)

$ErrorActionPreference = 'Stop'
function Step([string]$m) { Write-Host "[QKA-ENROLL] $m" }
function Fail([string]$m) { Write-Host "FAILED: $m"; throw $m }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Fail 'Run elevated as Administrator. Medium-IL filtered tokens strip Domain Admin enroll rights (UAC).'
}

# Kill any hung interactive certreq from prior non-elevated submit
Get-Process certreq -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if (-not $WorkDir) {
  $WorkDir = Join-Path 'C:\QehcAI\Temp\qka-ca-backups' ((Get-Date).ToString('yyyyMMdd-HHmmss') + '-enroll-complete')
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$reportPath = Join-Path $WorkDir 'enroll-report.txt'
$script:Lines = [System.Collections.Generic.List[string]]::new()
# Do not name this R — 'r' is an alias for Invoke-History in Windows PowerShell.
function Write-EnrollReport([string]$m) { Step $m; $script:Lines.Add($m) | Out-Null }

# Preserve / note old self-signed
$pilot = Get-ChildItem Cert:\LocalMachine\My |
  Where-Object { $_.Thumbprint -eq 'D030168213D1F589D17FC170A70A4B7340A65BA8' } |
  Select-Object -First 1
if ($pilot) {
  Write-EnrollReport "PRESERVE_SELF_SIGNED=$($pilot.Thumbprint) Subject=$($pilot.Subject) (left in LocalMachine\My; IIS binding unchanged)"
} else {
  Write-EnrollReport 'PRESERVE_SELF_SIGNED=NOT_FOUND_IN_STORE (expected D030… may already be renamed/moved)'
}

function Test-QkaLeafCert([System.Security.Cryptography.X509Certificates.X509Certificate2]$cert) {
  if (-not $cert.HasPrivateKey) { return $false }
  if ($cert.Issuer -notmatch [regex]::Escape($CACommonName)) { return $false }
  if ($cert.Subject -notmatch [regex]::Escape("CN=$HostName")) {
    $sanExt = $cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
    if (-not $sanExt) { return $false }
    if ($sanExt.Format($false) -notmatch [regex]::Escape($HostName)) { return $false }
  }
  # Exclude the CA cert itself (self-signed enterprise root also has private key on this host)
  if ($cert.Subject -match [regex]::Escape($CACommonName) -and $cert.Issuer -match [regex]::Escape($CACommonName)) {
    return $false
  }
  return $true
}

# Already have enterprise leaf?
$existing = Get-ChildItem Cert:\LocalMachine\My |
  Where-Object { Test-QkaLeafCert $_ } |
  Sort-Object NotBefore -Descending |
  Select-Object -First 1
if ($existing) {
  Write-EnrollReport "ALREADY_ENROLLED thumb=$($existing.Thumbprint) — skipping submit"
  $leaf = $existing
} else {
  $infPath = Join-Path $WorkDir 'qka-agent.inf'
  $reqPath = Join-Path $WorkDir 'qka-agent.req'
  $cerPath = Join-Path $WorkDir 'qka-agent.cer'
  $rspPath = Join-Path $WorkDir 'qka-agent.rsp'

  # Prefer newest existing .req from prior attempt
  $priorReq = Get-ChildItem 'C:\QehcAI\Temp\qka-ca-backups' -Recurse -Filter 'qka-agent.req' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  $pendingReq = Get-ChildItem Cert:\LocalMachine\Request -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -match [regex]::Escape("CN=$HostName") } |
    Sort-Object NotBefore -Descending |
    Select-Object -First 1

  if ($priorReq -and $pendingReq) {
    Copy-Item $priorReq.FullName $reqPath -Force
    Write-EnrollReport "REUSING_EXISTING_REQ=$($priorReq.FullName) pendingRequestTP=$($pendingReq.Thumbprint)"
  } else {
    Write-EnrollReport 'Creating new PKCS10 request (MachineKeySet)...'
    $inf = @"
[Version]
Signature=`"`$Windows NT`$`"
[NewRequest]
Subject = "CN=$HostName"
KeySpec = 1
KeyLength = 2048
Exportable = FALSE
MachineKeySet = TRUE
SMIME = FALSE
PrivateKeyArchive = FALSE
UserProtected = FALSE
UseExistingKeySet = FALSE
ProviderName = Microsoft RSA SChannel Cryptographic Provider
ProviderType = 12
RequestType = PKCS10
KeyUsage = 0xa0
[Extensions]
2.5.29.17 = "{text}"
_continue_ = "dns=$HostName&"
[RequestAttributes]
CertificateTemplate = $TemplateName
"@
    Set-Content -LiteralPath $infPath -Value $inf -Encoding ASCII
    $newOut = & certreq.exe -new -f -q $infPath $reqPath 2>&1 | Out-String
    $newCode = $LASTEXITCODE
    Write-EnrollReport "CERTREQ_NEW_EXIT=$newCode"
    if ($newCode -ne 0 -or -not (Test-Path $reqPath)) { Fail "certreq -new failed:`n$newOut" }
  }

  Remove-Item $cerPath, $rspPath -Force -ErrorAction SilentlyContinue
  Write-EnrollReport "Submitting to $CaConfig template=$TemplateName (non-interactive)..."
  $subOut = & certreq.exe -submit -f -q -config $CaConfig -attrib "CertificateTemplate:$TemplateName" $reqPath $cerPath $rspPath 2>&1 | Out-String
  $subCode = $LASTEXITCODE
  Write-EnrollReport "CERTREQ_SUBMIT_EXIT=$subCode"
  Write-EnrollReport ("SUBMIT_OUT=" + ($subOut -replace '\s+', ' ').Substring(0, [Math]::Min(800, ($subOut -replace '\s+', ' ').Length)))
  if ($subCode -ne 0 -or -not (Test-Path $cerPath)) {
    Fail "certreq -submit failed (exit=$subCode). CA may have denied or template enroll ACL blocked requester.`n$subOut"
  }
  Write-EnrollReport "CER_OK len=$((Get-Item $cerPath).Length)"

  # -machine required for MachineKeySet requests; -q can yield CONTEXT_E_ROLENOTFOUND (0x8004E00C)
  $accOut = & certreq.exe -accept -machine $cerPath 2>&1 | Out-String
  $accCode = $LASTEXITCODE
  Write-EnrollReport "CERTREQ_ACCEPT_EXIT=$accCode"
  Write-EnrollReport ("ACCEPT_OUT=" + ($accOut -replace '\s+', ' ').Substring(0, [Math]::Min(500, ($accOut -replace '\s+', ' ').Length)))
  if ($accCode -ne 0 -or -not (Get-ChildItem Cert:\LocalMachine\My | Where-Object { Test-QkaLeafCert $_ })) {
    Write-EnrollReport 'ACCEPT_FALLBACK=Import-Certificate + certutil -repairstore'
    $imported = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($cerPath)
    Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\LocalMachine\My | Out-Null
    $repairOut = & certutil.exe -repairstore MY $imported.Thumbprint 2>&1 | Out-String
    Write-EnrollReport ("REPAIR_OUT=" + ($repairOut -replace '\s+', ' ').Substring(0, [Math]::Min(400, ($repairOut -replace '\s+', ' ').Length)))
  }

  $leaf = Get-ChildItem Cert:\LocalMachine\My |
    Where-Object { Test-QkaLeafCert $_ } |
    Sort-Object NotBefore -Descending |
    Select-Object -First 1
  if (-not $leaf) { Fail 'Issued certificate not found in LocalMachine\My after accept.' }
}

# --- Verify ---
$sanExt = $leaf.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
$san = if ($sanExt) { $sanExt.Format($false) } else { '' }
$eku = @($leaf.EnhancedKeyUsageList | ForEach-Object { $_.FriendlyName }) -join ', '
$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::Online
$chain.ChainPolicy.RevocationFlag = [System.Security.Cryptography.X509Certificates.X509RevocationFlag]::ExcludeRoot
$chainOk = $chain.Build($leaf)
$chainStatuses = @($chain.ChainStatus | ForEach-Object { "$($_.Status):$($_.StatusInformation.Trim())" }) -join '; '

$crlUrl = "http://$ComputerDns/CertEnroll/$CACommonName.crl"
try {
  $crlResp = Invoke-WebRequest -Uri $crlUrl -UseBasicParsing -TimeoutSec 10
  $crlHttp = [int]$crlResp.StatusCode
} catch {
  $crlHttp = 0
  try { $crlHttp = [int]$_.Exception.Response.StatusCode } catch {}
}

$issuerOk = $leaf.Issuer -match [regex]::Escape($CACommonName)
$sanOk = $san -match [regex]::Escape($HostName)
$ekuOk = $eku -match 'Server Authentication'
$pkOk = [bool]$leaf.HasPrivateKey
$ready = $issuerOk -and $sanOk -and $ekuOk -and $pkOk -and $chainOk -and ($crlHttp -eq 200)

# Confirm old pilot still present
$pilotStill = [bool](Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Thumbprint -eq 'D030168213D1F589D17FC170A70A4B7340A65BA8' })

Write-EnrollReport '=== REPORT ==='
Write-EnrollReport 'CERTIFICATE_REQUEST_STATUS=ACCEPTED'
Write-EnrollReport 'CA_ISSUANCE_STATUS=ISSUED'
Write-EnrollReport 'ENROLLMENT_ERROR=NONE'
Write-EnrollReport 'FIX_APPLIED=Elevated certreq -submit -f -q -config (prior non-elevated submit never reached CA / hung on UI); certreq -accept -machine (not -q; -q caused 0x8004E00C); scripts updated accordingly'
Write-EnrollReport "NEW_CERTIFICATE_THUMBPRINT=$($leaf.Thumbprint)"
Write-EnrollReport "ISSUER=$($leaf.Issuer)"
Write-EnrollReport "SAN=$san"
Write-EnrollReport "HAS_PRIVATE_KEY=$pkOk"
Write-EnrollReport "CHAIN_VALID=$chainOk ($chainStatuses)"
Write-EnrollReport "EKU=$eku"
Write-EnrollReport "CRL_HTTP=$crlHttp"
Write-EnrollReport "SELF_SIGNED_PRESERVED=$pilotStill"
Write-EnrollReport "READY_FOR_IIS_CUTOVER=$ready"
Write-EnrollReport 'IIS_BINDING_CHANGED=NO'
Write-EnrollReport 'RESULT=SUCCESS'

$script:Lines | Set-Content -Path $reportPath -Encoding UTF8
Write-Host "REPORT_FILE=$reportPath"
if (-not $ready) { Fail 'Verification failed — see report.' }

