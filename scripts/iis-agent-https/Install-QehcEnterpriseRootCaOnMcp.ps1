<#
.SYNOPSIS
  Gate-controlled install of a single-tier Enterprise Root CA on QEC-AI-MCP for QKA HTTPS trust.

.DESCRIPTION
  DEFAULT: dry-run / inventory only. Does NOT install AD CS, publish templates, or change IIS
  until -IConfirmInstallEnterpriseCa is passed by an elevated Enterprise Admin.

  Keeps hostname, qka-agent.qehc.edu.sa SNI site, MSI, GPO deploy logic, and WSIT02 untouched.
  Does NOT install AD CS Web Enrollment.
  Does NOT overwrite an existing CertSvc Configuration.

.NOTES
  Run elevated as a member of Enterprise Admins (and local Administrators).
  Review every printed REQUIRED_CHANGE before approving the confirm switch.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$CACommonName = 'QEHC-Internal-Root-CA',
  [ValidateSet(2048, 4096)]
  [int]$KeyLength = 4096,
  [string]$HashAlgorithmName = 'SHA256',
  # CNG KSP MUST use PublicKeyAlgorithm#Provider (MSDN ICertSrvSetupKeyInformation::ProviderName).
  # Bare "Microsoft Software Key Storage Provider" → CCertSrvSetup::SetCASetupProperty 0x80070057.
  [string]$CryptoProviderName = 'RSA#Microsoft Software Key Storage Provider',
  [ValidateRange(5, 20)]
  [int]$ValidityPeriodYears = 10,
  [string]$HostName = 'qka-agent.qehc.edu.sa',
  [string]$ComputerDns = 'QEC-AI-MCP.qehc.edu.sa',
  [string]$TemplateDisplayName = 'QKA Web Server',
  [string]$TemplateName = 'QKAWebServer',
  [string]$BackupRoot = 'C:\QehcAI\Temp\qka-ca-backups',
  [string]$IisBackupRoot = 'C:\QehcAI\Temp\qka-iis-backups',
  [string]$CrlHttpPath = 'C:\inetpub\pki\CertEnroll',
  [string]$CrlSiteName = 'QEHC-PKI-CRL',
  [string]$PilotSelfSignedThumbprint = 'D030168213D1F589D17FC170A70A4B7340A65BA8',
  # Explicit approvals (required for mutating phases)
  [switch]$IConfirmInstallEnterpriseCa,
  [switch]$IConfirmPublishTemplateAndEnroll,
  [switch]$IConfirmIisCutover,
  # Phase 2 prep only: repair HTTP CRL + create/publish QKAWebServer; do NOT enroll leaf / change IIS
  [switch]$IConfirmPublishTemplateOnly,
  # Auditable override: allow IIS cutover when OFFSERVER-CA-BACKUP-VERIFIED.txt is absent.
  # Default remains: refuse cutover without that marker (or this switch). Local CA backup is never deleted.
  [switch]$IAcceptProceedWithoutOffServerCaBackup,
  [string]$ExistingLeafThumbprint = 'EEFD0AB6F6A4C18944D7934960B9DE560BB77E9F'
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)

function Write-Step([string]$Message) { Write-Host ("[QKA-CA] " + $Message) }
function Write-Req([string]$Message) { Write-Host ("REQUIRED_CHANGE: " + $Message) }

Write-Host '=== QEHC ENTERPRISE ROOT CA ON QEC-AI-MCP — PLAN / GATE ==='
Write-Step ("ShellElevated=" + $isAdmin + " User=" + ([Security.Principal.WindowsIdentity]::GetCurrent().Name))
Write-Step ("Host=" + $env:COMPUTERNAME + " Domain=" + (Get-CimInstance Win32_ComputerSystem).Domain)

# --- 1. Preconditions (always) ---
$role = Get-WindowsFeature -Name ADCS-Cert-Authority
$caReg = Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration'
$certSvc = Get-Service CertSvc -ErrorAction SilentlyContinue
Write-Step ("ADCS-Cert-Authority InstallState=" + $role.InstallState)
Write-Step ("CertSvc Status=" + $(if ($certSvc) { $certSvc.Status } else { 'n/a' }) + " StartType=" + $(if ($certSvc) { $certSvc.StartType } else { 'n/a' }))
Write-Step ("CA Configuration registry present=" + $caReg)

$caAlreadyInstalled = $caReg -and ((Get-Service CertSvc -ErrorAction SilentlyContinue).Status -eq 'Running')
if ($caReg -and $IConfirmInstallEnterpriseCa -and -not $IConfirmPublishTemplateAndEnroll -and -not $IConfirmPublishTemplateOnly) {
  throw 'Existing CertSvc Configuration detected. Refusing to overwrite. Use -IConfirmPublishTemplateOnly or -IConfirmPublishTemplateAndEnroll for Phase 2.'
}
if ($role.InstallState -ne 'Installed') {
  Write-Req 'Install Windows feature ADCS-Cert-Authority (role files only) before Install-AdcsCertificationAuthority.'
}
Write-Step ("CA_ALREADY_INSTALLED=" + $caAlreadyInstalled)

$ip = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq '192.168.120.35' }
Write-Step ("Static IP 192.168.120.35 present=" + [bool]$ip)
$ssl = & netsh http show sslcert hostnameport="$HostName`:443" 2>&1 | Out-String
Write-Step ("Current SNI binding contains pilot hash=" + ($ssl -match $PilotSelfSignedThumbprint))

$cfg = (Get-ADRootDSE).configurationNamingContext
$enrollCount = @(Get-ADObject -SearchBase "CN=Enrollment Services,CN=Public Key Services,CN=Services,$cfg" -LDAPFilter '(objectClass=pKIEnrollmentService)' -SearchScope OneLevel -ErrorAction SilentlyContinue).Count
Write-Step ("AD Enrollment Services CA count=" + $enrollCount)
if ($enrollCount -gt 0 -and $IConfirmInstallEnterpriseCa) {
  throw 'An Enrollment Services CA already exists in AD. Refusing to install a second Enterprise Root without redesign.'
}
if ($enrollCount -gt 0) {
  Write-Step 'Enterprise CA already published in AD (Phase 1 complete).'
}

Write-Host ''
Write-Host 'SECURITY_IMPACT=Single-tier Enterprise Root on the QKA application host concentrates CA private key risk with the app server. Domain-wide trust of this Root means CA key compromise allows spoofing of any internal TLS name this CA issues. Restrict CA Administrators; backup CA keys offline; monitor CertSvc.'
Write-Host 'CRYPTO_PROVIDER_RULE=For Microsoft Software Key Storage Provider use RSA#Microsoft Software Key Storage Provider (algorithm#KSP). Omitting RSA# causes SetCASetupProperty 0x80070057.'
Write-Host ''

# --- Parameter validation (always; no install) ---
$caParamsPreview = [ordered]@{
  CAType              = 'EnterpriseRootCA'
  CACommonName        = $CACommonName
  KeyLength           = $KeyLength
  HashAlgorithmName   = $HashAlgorithmName
  CryptoProviderName  = $CryptoProviderName
  ValidityPeriod      = 'Years'
  ValidityPeriodUnits = $ValidityPeriodYears
  Force               = $true
}
Write-Host 'CA_PARAMS_NON_SECRET:'
$caParamsPreview.GetEnumerator() | ForEach-Object { Write-Host ("  {0}={1}" -f $_.Key, $_.Value) }

$providerOk = $CryptoProviderName -match '^(RSA|ECDSA_P256|ECDSA_P384|ECDSA_P521)#'
if ($CryptoProviderName -match 'Key Storage Provider' -and -not $providerOk) {
  throw "INVALID CryptoProviderName='$CryptoProviderName'. CNG KSP requires prefix e.g. RSA#Microsoft Software Key Storage Provider."
}
if ($CryptoProviderName -ne 'RSA#Microsoft Software Key Storage Provider') {
  Write-Host "WARNING: Non-default CryptoProviderName='$CryptoProviderName' — confirm against certutil -csplist / MSDN."
}
if ($KeyLength -ne 4096) {
  Write-Host "WARNING: KeyLength=$KeyLength (project default is RSA 4096; change only if a specific incompatibility is demonstrated)."
}
if ($HashAlgorithmName -ne 'SHA256') {
  throw "HashAlgorithmName must be SHA256 for this deployment (got '$HashAlgorithmName')."
}
Write-Host 'PARAMETER_VALIDATION=PASS'
Write-Host "PARTIAL_CA_CHECK registry=$(Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration') CertSrv=$(Test-Path 'C:\Windows\System32\CertSrv')"

Write-Req "Install Enterprise Root CA CN=$CACommonName (SHA256, RSA $KeyLength, provider=$CryptoProviderName, validity ${ValidityPeriodYears}y) on QEC-AI-MCP"
Write-Req 'Publish LDAP + HTTP AIA/CDP; create isolated IIS site QEHC-PKI-CRL for /CertEnroll (not QKA reverse-proxy)'
Write-Req "Create template $TemplateName (display '$TemplateDisplayName'); Enroll/Autoenroll = QEC-AI-MCP$ only"
Write-Req "Enroll leaf SAN=$HostName Server Authentication; private key LocalMachine\My non-exportable"
Write-Req 'Register scheduled task to rebind IIS SNI when renewed cert thumbprint changes'
Write-Req "IIS cutover: replace SNI $HostName`:443 hash $PilotSelfSignedThumbprint with enterprise leaf"
Write-Req 'Do NOT install ADCS-Web-Enrollment; do NOT distribute pilot self-signed leaf via GPO'

Write-Host ''
Write-Host 'PHASE_COMMANDS (elevated Enterprise Admin):'
Write-Host '  Phase1 (done): -IConfirmInstallEnterpriseCa'
Write-Host '  Phase2 prep (CRL IIS + QKAWebServer template ONLY, no leaf enroll):'
Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File "C:\QehcAI\QKA-Next\scripts\iis-agent-https\Install-QehcEnterpriseRootCaOnMcp.ps1" -IConfirmPublishTemplateOnly'
Write-Host '  Phase2 enroll (issues qka-agent leaf; does NOT rebind IIS):'
Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File "C:\QehcAI\QKA-Next\scripts\iis-agent-https\Install-QehcEnterpriseRootCaOnMcp.ps1" -IConfirmPublishTemplateAndEnroll'
Write-Host '  Phase3 IIS cutover:'
Write-Host '    ... -IConfirmPublishTemplateAndEnroll -IConfirmIisCutover'
Write-Host ''

$doPhase1 = [bool]$IConfirmInstallEnterpriseCa
$doPhase2Prep = [bool]$IConfirmPublishTemplateOnly -or [bool]$IConfirmPublishTemplateAndEnroll
$doPhase2Enroll = [bool]$IConfirmPublishTemplateAndEnroll
$doPhase3 = [bool]$IConfirmIisCutover

if (-not ($doPhase1 -or $doPhase2Prep -or $doPhase3)) {
  Write-Host 'IMPLEMENTATION_STATUS=VERIFY_ONLY (no confirm switches)'
  Write-Host 'ADMIN_APPROVAL_REQUIRED=YES'
  return
}

if (-not $isAdmin) {
  throw 'Elevated administrator token required (UAC elevation). Current shell is not elevated.'
}

# Enterprise Admins check (best-effort; deny-only filtered tokens fail this)
$groups = whoami /groups /fo csv | ConvertFrom-Csv
$ea = $groups | Where-Object { $_.'Group Name' -match 'Enterprise Admins' -and $_.'Type' -match 'Group' -and $_.'Attributes' -notmatch 'deny' }
if (-not $ea) {
  throw 'Enterprise Admins membership with enabled (non-deny) token required. Re-launch elevated as Enterprise Admin.'
}

Import-Module ActiveDirectory -ErrorAction Stop
if ($doPhase1) { Import-Module ADCSDeployment -ErrorAction Stop }

# --- Backup dirs ---
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $BackupRoot $stamp
$iisBackupDir = Join-Path $IisBackupRoot ("pre-ca-" + $stamp)
New-Item -ItemType Directory -Force -Path $backupDir, $iisBackupDir, $CrlHttpPath | Out-Null

# Capture IIS binding rollback artifact
$ssl | Set-Content -LiteralPath (Join-Path $iisBackupDir 'sslcert-qka-agent.txt') -Encoding UTF8
$pilot = Get-Item -LiteralPath ("Cert:\LocalMachine\My\" + $PilotSelfSignedThumbprint) -ErrorAction SilentlyContinue
if ($pilot) {
  [IO.File]::WriteAllBytes((Join-Path $iisBackupDir 'pilot-selfsigned.cer'), $pilot.Export('Cert'))
}
@"
Rollback IIS SNI (elevated):
netsh http update sslcert hostnameport=$HostName`:443 certhash=$($PilotSelfSignedThumbprint.ToLowerInvariant()) appid={4dc3e181-e14b-4a21-b022-59fc669b0914} certstorename=MY

Or:
powershell -NoProfile -ExecutionPolicy Bypass -File C:\QehcAI\QKA-Next\scripts\iis-agent-https\Install-QkaAgentIisHttps.ps1 -CertificateThumbprint $PilotSelfSignedThumbprint
"@ | Set-Content -LiteralPath (Join-Path $iisBackupDir 'ROLLBACK.txt') -Encoding UTF8
Write-Step ("IIS rollback notes written to " + $iisBackupDir)

function Repair-QkaCrlHttpEndpoint {
  param([string]$ComputerDns, [string]$CrlSiteName, [string]$CrlHttpPath)
  # Prefer dedicated fix script (MIME + host-header ownership + no nested CertEnroll).
  $fix = 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Fix-QehcPkiCrlHttp.ps1'
  if (Test-Path $fix) {
    Write-Step "Delegating CRL HTTP repair to $fix"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fix -ComputerDns $ComputerDns -CrlSiteName $CrlSiteName -VdirPhysicalPath $CrlHttpPath
    return
  }
  throw "Missing $fix — cannot configure IIS CertEnroll mapping."
}

function Invoke-CertutilChecked {
  param([Parameter(Mandatory)][string[]]$CertutilArgs, [string]$FailureMessage)
  $output = & certutil @CertutilArgs 2>&1
  $code = $LASTEXITCODE
  $text = ($output | Out-String)
  if ($code -ne 0) {
    throw ("$FailureMessage (certutil exit=$code)`n$text")
  }
  # Some certutil commands return 0 with embedded FAILED text — treat as failure
  if ($text -match 'CertUtil: -.* command FAILED') {
    throw ("$FailureMessage (certutil reported FAILED)`n$text")
  }
  return $text
}

function Get-QkaUniqueTemplateOid {
  param([string]$ConfigNc, [string]$DisplayName)
  $oidContainer = "CN=OID,CN=Public Key Services,CN=Services,$ConfigNc"
  $forestOids = @(Get-ADObject -SearchBase $oidContainer -LDAPFilter '(objectClass=msPKI-Enterprise-Oid)' -SearchScope OneLevel -Properties 'msPKI-Cert-Template-OID' |
    ForEach-Object { $_.'msPKI-Cert-Template-OID' } |
    Where-Object { $_ })
  # Forest base from WebServer-style OIDs: ...92.1.<n>
  $base = '1.3.6.1.4.1.311.21.8.13785139.4644308.11814306.2973404.2575399.92.1'
  $used = New-Object 'System.Collections.Generic.HashSet[int]'
  foreach ($o in $forestOids) {
    if ($o -match [regex]::Escape($base) + '\.(\d+)$') { [void]$used.Add([int]$Matches[1]) }
  }
  $tplOids = @(Get-ADObject -SearchBase "CN=Certificate Templates,CN=Public Key Services,CN=Services,$ConfigNc" -LDAPFilter '(objectClass=pKICertificateTemplate)' -SearchScope OneLevel -Properties 'msPKI-Cert-Template-OID' |
    ForEach-Object { $_.'msPKI-Cert-Template-OID' })
  foreach ($o in $tplOids) {
    if ($o -match [regex]::Escape($base) + '\.(\d+)$') { [void]$used.Add([int]$Matches[1]) }
  }
  $n = 500
  while ($used.Contains($n)) { $n++ }
  $newOid = "$base.$n"
  $existingReg = Get-ADObject -SearchBase $oidContainer -LDAPFilter "(msPKI-Cert-Template-OID=$newOid)" -SearchScope OneLevel -ErrorAction SilentlyContinue
  if (-not $existingReg) {
    $cn = "$n." + ([guid]::NewGuid().ToString('N').ToUpperInvariant())
    New-ADObject -Name $cn -Type 'msPKI-Enterprise-Oid' -Path $oidContainer -OtherAttributes @{
      displayName = $DisplayName
      'msPKI-Cert-Template-OID' = $newOid
      flags = 2
    } | Out-Null
  }
  return $newOid
}

function Set-QkaTemplateMachineAcl {
  param([string]$TemplateDn, [string]$EnrollComputerSam)
  $acl = Get-Acl -Path "AD:$TemplateDn"
  $computer = Get-ADComputer -Identity ($EnrollComputerSam.TrimEnd('$'))
  $identity = [System.Security.Principal.SecurityIdentifier]$computer.SID
  $enrollGuid = [Guid]'0e10c968-78fb-11d2-90d4-00c04f79dc55'
  $autoGuid = [Guid]'a05b8cc2-17bc-4802-a710-e7c15ab866a2'
  $readRule = New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
    $identity,
    [System.DirectoryServices.ActiveDirectoryRights]::GenericRead,
    [System.Security.AccessControl.AccessControlType]::Allow
  )
  $acl.AddAccessRule($readRule)
  foreach ($guid in @($enrollGuid, $autoGuid)) {
    $rule = New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
      $identity,
      [System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight,
      [System.Security.AccessControl.AccessControlType]::Allow,
      $guid
    )
    $acl.AddAccessRule($rule)
  }
  $au = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-11')
  $auRead = New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
    $au,
    [System.DirectoryServices.ActiveDirectoryRights]::GenericRead,
    [System.Security.AccessControl.AccessControlType]::Allow
  )
  $acl.AddAccessRule($auRead)
  Set-Acl -Path "AD:$TemplateDn" -AclObject $acl
}

function New-QkaWebServerTemplate {
  param(
    [string]$TemplateName,
    [string]$TemplateDisplayName,
    [string]$ConfigNc,
    [string]$EnrollComputerSam = 'QEC-AI-MCP$'
  )
  $tplBase = "CN=Certificate Templates,CN=Public Key Services,CN=Services,$ConfigNc"
  $web = Get-ADObject -SearchBase $tplBase -LDAPFilter '(cn=WebServer)' -SearchScope OneLevel -Properties * -ErrorAction Stop
  $existing = Get-ADObject -SearchBase $tplBase -LDAPFilter "(cn=$TemplateName)" -SearchScope OneLevel -Properties 'msPKI-Cert-Template-OID' -ErrorAction SilentlyContinue
  if ($existing) {
    $webOid = $web.'msPKI-Cert-Template-OID'
    $curOid = $existing.'msPKI-Cert-Template-OID'
    if ([string]::IsNullOrWhiteSpace($curOid) -or $curOid -eq $webOid) {
      $fixedOid = Get-QkaUniqueTemplateOid -ConfigNc $ConfigNc -DisplayName $TemplateName
      Set-ADObject -Identity $existing.DistinguishedName -Replace @{ 'msPKI-Cert-Template-OID' = $fixedOid }
      Write-Step "Repaired duplicate/missing template OID -> $fixedOid"
    }
    Set-QkaTemplateMachineAcl -TemplateDn $existing.DistinguishedName -EnrollComputerSam $EnrollComputerSam
    Write-Step "Template $TemplateName already exists (ACL refreshed)"
    return Get-ADObject -Identity $existing.DistinguishedName -Properties *
  }

  $uniqueOid = Get-QkaUniqueTemplateOid -ConfigNc $ConfigNc -DisplayName $TemplateName
  $destPath = "CN=$TemplateName,$tplBase"
  # Clone via ADSI (Copy-CATemplate is not present on this host) — NEVER copy WebServer OID
  $src = [ADSI]"LDAP://$($web.DistinguishedName)"
  $parent = [ADSI]"LDAP://$tplBase"
  $clone = $parent.Create('pKICertificateTemplate', "CN=$TemplateName")
  foreach ($prop in @(
      'flags','revision','pKIExpirationPeriod','pKIOverlapPeriod','pKIDefaultKeySpec','pKIMaxIssuingDepth',
      'pKICriticalExtensions','pKIKeyUsage','pKIExtendedKeyUsage','pKIDefaultCSPs','pKIRASignature',
      'msPKI-RA-Signature','msPKI-Enrollment-Flag','msPKI-Private-Key-Flag','msPKI-Certificate-Name-Flag',
      'msPKI-Minimal-Key-Size','msPKI-Template-Schema-Version','msPKI-Template-Minor-Revision',
      'msPKI-Certificate-Application-Policy'
    )) {
    if ($null -ne $src.Properties[$prop].Value) {
      $clone.Properties[$prop].Value = $src.Properties[$prop].Value
    }
  }
  $clone.Properties['displayName'].Value = $TemplateDisplayName
  $clone.Properties['msPKI-Certificate-Name-Flag'].Value = 1  # ENROLLEE_SUPPLIES_SUBJECT (SAN)
  $clone.Properties['msPKI-Cert-Template-OID'].Value = $uniqueOid
  $clone.SetInfo()

  Set-QkaTemplateMachineAcl -TemplateDn $destPath -EnrollComputerSam $EnrollComputerSam
  Write-Step "Created template $TemplateName OID=$uniqueOid Enroll/Autoenroll/Read=$EnrollComputerSam"
  return Get-ADObject -Identity $destPath -Properties *
}

if ($doPhase1) {
  if ($caAlreadyInstalled) {
    throw 'CA already installed. Do not pass -IConfirmInstallEnterpriseCa again.'
  }
  # --- 2. Install Enterprise Root CA ---
  Write-Step 'Installing Enterprise Root CA (this publishes to AD)...'
  if ($CryptoProviderName -match 'Key Storage Provider' -and $CryptoProviderName -notmatch '^(RSA|ECDSA_P256|ECDSA_P384|ECDSA_P521)#') {
    throw "Refusing install: CryptoProviderName missing algorithm# prefix (got '$CryptoProviderName')."
  }
  $caParams = @{
    CAType              = 'EnterpriseRootCA'
    CACommonName        = $CACommonName
    KeyLength           = $KeyLength
    HashAlgorithmName   = $HashAlgorithmName
    CryptoProviderName  = $CryptoProviderName
    ValidityPeriod      = 'Years'
    ValidityPeriodUnits = $ValidityPeriodYears
    Force               = $true
  }
  Write-Host 'INSTALL_CA_PARAMS:'
  $caParams.GetEnumerator() | Sort-Object Name | ForEach-Object { Write-Host ("  {0}={1}" -f $_.Key, $_.Value) }
  Install-AdcsCertificationAuthority @caParams

  Start-Service CertSvc
  Start-Sleep -Seconds 3
  if ((Get-Service CertSvc).Status -ne 'Running') {
    throw 'CertSvc failed to start after Install-AdcsCertificationAuthority.'
  }

  Write-Step 'Backing up CA database + private key (set env QKA_CA_BACKUP_PASSWORD for non-interactive)...'
  New-Item -ItemType Directory -Force -Path (Join-Path $backupDir 'CA-Database') | Out-Null
  $bakPass = $env:QKA_CA_BACKUP_PASSWORD
  if ([string]::IsNullOrWhiteSpace($bakPass)) {
    Write-Step 'WARNING: No QKA_CA_BACKUP_PASSWORD — CA private-key backup skipped. Complete off-server certutil -backup/-backupkey before production reliance.'
  } else {
    certutil -backupKey $backupDir
    $caCert = Get-ChildItem Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
      Where-Object { $_.Subject -match [regex]::Escape("CN=$CACommonName") } |
      Select-Object -First 1
    if ($caCert) {
      Export-Certificate -Cert $caCert -FilePath (Join-Path $backupDir 'QEHC-Internal-Root-CA.cer') | Out-Null
    }
    Remove-Variable bakPass -ErrorAction SilentlyContinue
  }

  Write-Step 'Configuring HTTP CDP/AIA registry URLs (separate REG_MULTI_SZ values — never embed `n in one string)...'
  # IMPORTANT: PowerShell `n inside one certutil -setreg argument collapses MULTI_SZ into a single
  # invalid path and causes certutil -CRL → 0x80070003 ERROR_PATH_NOT_FOUND.
  $caReg = "HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration\$CACommonName"
  $crlUrls = @(
    '65:C:\Windows\System32\CertSrv\CertEnroll\%3%8%9.crl',
    ("6:http://" + $ComputerDns + '/CertEnroll/%3%8%9.crl'),
    '79:ldap:///CN=%7%8,CN=%2,CN=CDP,CN=Public Key Services,CN=Services,%6%10'
  )
  $aiaUrls = @(
    '1:C:\Windows\System32\CertSrv\CertEnroll\%1_%3%4.crt',
    ("2:http://" + $ComputerDns + '/CertEnroll/%1_%3%4.crt'),
    '3:ldap:///CN=%7,CN=AIA,CN=Public Key Services,CN=Services,%6%11'
  )
  Set-ItemProperty -Path $caReg -Name CRLPublicationURLs -Value $crlUrls
  Set-ItemProperty -Path $caReg -Name CACertPublicationURLs -Value $aiaUrls
  $crlVerify = @((Get-ItemProperty $caReg -Name CRLPublicationURLs).CRLPublicationURLs)
  if ($crlVerify.Count -lt 3 -or $crlVerify[0] -match "`n") {
    throw "CRLPublicationURLs MULTI_SZ invalid after write (count=$($crlVerify.Count))"
  }
  Restart-Service CertSvc -Force
  Repair-QkaCrlHttpEndpoint -ComputerDns $ComputerDns -CrlSiteName $CrlSiteName -CrlHttpPath $CrlHttpPath
  $crlPub = & certutil -CRL 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0 -or $crlPub -match 'FAILED') {
    throw "certutil -CRL failed after CDP fix:`n$crlPub"
  }
  Write-Step 'Enterprise Root published to AD — domain clients trust via enterprise Root distribution.'
  if (-not $doPhase2Prep) {
    Write-Host 'STOP_AFTER_CA_INSTALL=Pass -IConfirmPublishTemplateOnly (prep) or -IConfirmPublishTemplateAndEnroll'
    return
  }
}

if (-not $doPhase2Prep) {
  if ($doPhase3) {
    Write-Step 'Phase 3 only: reusing existing enterprise leaf (no enroll, no CA reinstall)...'
    $cutover = 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Invoke-QkaAgentIisEnterpriseCutover.ps1'
    $cutArgs = @{
      IConfirmIisCutover = $true
      CertificateThumbprint = $ExistingLeafThumbprint
      HostName = $HostName
      CACommonName = $CACommonName
      ComputerDns = $ComputerDns
      PilotThumbprint = $PilotSelfSignedThumbprint
    }
    if ($IAcceptProceedWithoutOffServerCaBackup) {
      $cutArgs.IAcceptProceedWithoutOffServerCaBackup = $true
    }
    & $cutover @cutArgs
    return
  }
  return
}

if (-not $caAlreadyInstalled -and -not $doPhase1) {
  throw 'No operational CA. Complete Phase 1 before Phase 2.'
}

# Refresh cfg after possible Phase 1
$cfg = (Get-ADRootDSE).configurationNamingContext

# Repair CRL HTTP (currently 404) + ensure CDP files mirrored
Repair-QkaCrlHttpEndpoint -ComputerDns $ComputerDns -CrlSiteName $CrlSiteName -CrlHttpPath $CrlHttpPath
certutil -crl 2>$null | Out-Null
Copy-Item -Path 'C:\Windows\System32\CertSrv\CertEnroll\*' -Destination $CrlHttpPath -Force -ErrorAction SilentlyContinue

# --- 4. Template (ADSI clone of WebServer; unique OID required) ---
Write-Step "Publishing restricted template $TemplateName..."
$null = New-QkaWebServerTemplate -TemplateName $TemplateName -TemplateDisplayName $TemplateDisplayName -ConfigNc $cfg -EnrollComputerSam 'QEC-AI-MCP$'

# Publish on CA — prefer AD attribute (works without interactive elevation quirks), then certutil verify
$enrollDn = "CN=$CACommonName,CN=Enrollment Services,CN=Public Key Services,CN=Services,$cfg"
$enrollObj = Get-ADObject -Identity $enrollDn -Properties certificateTemplates
if ($enrollObj.certificateTemplates -notcontains $TemplateName) {
  Set-ADObject -Identity $enrollDn -Add @{ certificateTemplates = $TemplateName }
  Write-Step "Added $TemplateName to CA certificateTemplates via AD"
}
$setOut = & certutil -SetCATemplates "+$TemplateName" 2>&1
$setCode = $LASTEXITCODE
$setText = ($setOut | Out-String)
Write-Host $setText
if ($setCode -ne 0 -and $setText -notmatch 'Already present') {
  # If AD publish succeeded, certutil may still fail for non-elevated shells — verify AD list
  $enrollObj = Get-ADObject -Identity $enrollDn -Properties certificateTemplates
  if ($enrollObj.certificateTemplates -notcontains $TemplateName) {
    throw "certutil -SetCATemplates failed (exit=$setCode) and AD publish missing:`n$setText"
  }
  Write-Step "certutil -SetCATemplates exit=$setCode but AD certificateTemplates contains $TemplateName — continuing"
}

$caTplText = Invoke-CertutilChecked -CertutilArgs @('-CATemplates') -FailureMessage 'certutil -CATemplates failed'
if ($caTplText -notmatch [regex]::Escape($TemplateName)) {
  throw "TEMPLATE_NOT_ON_CA: $TemplateName missing from certutil -CATemplates output.`n$caTplText"
}
Write-Step "VERIFIED certutil -CATemplates lists $TemplateName"

# CRL HTTP must return 200 for CDP/AIA files before declaring prep success
$crlName = "$CACommonName.crl"
$crtName = "$ComputerDns`_$CACommonName.crt"
$crlUrl = "http://$ComputerDns/CertEnroll/$crlName"
$crtUrl = "http://$ComputerDns/CertEnroll/$crtName"
$crlOk = $false
$crtOk = $false
try {
  $crlResp = Invoke-WebRequest -Uri $crlUrl -UseBasicParsing -TimeoutSec 8
  $crlOk = ($crlResp.StatusCode -eq 200 -and $crlResp.RawContentLength -gt 0)
  Write-Step ("CRL_HTTP $crlUrl -> $($crlResp.StatusCode) len=$($crlResp.RawContentLength)")
} catch {
  Write-Step ("CRL_HTTP_FAIL $crlUrl -> $($_.Exception.Message)")
}
try {
  $crtResp = Invoke-WebRequest -Uri $crtUrl -UseBasicParsing -TimeoutSec 8
  $crtOk = ($crtResp.StatusCode -eq 200 -and $crtResp.RawContentLength -gt 0)
  Write-Step ("AIA_HTTP $crtUrl -> $($crtResp.StatusCode) len=$($crtResp.RawContentLength)")
} catch {
  Write-Step ("AIA_HTTP_FAIL $crtUrl -> $($_.Exception.Message)")
}
if (-not ($crlOk -and $crtOk)) {
  throw @"
PHASE2_PREP_FAILED: Template published, but HTTP CRL/AIA not reachable (expected 200).
  CRL=$crlUrl ok=$crlOk
  AIA=$crtUrl ok=$crtOk
Repair elevated IIS site QEHC-PKI-CRL (or Default Web Site /CertEnroll -> C:\inetpub\pki\CertEnroll) then re-run -IConfirmPublishTemplateOnly.
"@
}

if (-not $doPhase2Enroll) {
  Write-Host 'PHASE2_PREP_DONE=QKAWebServer published + certutil -CATemplates verified + HTTP CRL/AIA 200. No leaf enrolled. No IIS HTTPS change.'
  Write-Host 'NEXT=After approval, run -IConfirmPublishTemplateAndEnroll to issue qka-agent leaf (still no IIS cutover).'
  Write-Host 'ALSO_REQUIRED=Off-server CA backup: elevated certutil -backup / -backupkey to removable/offline media (set QKA_CA_BACKUP_PASSWORD).'
  return
}

# --- 5. Enroll leaf on QEC-AI-MCP ---
Write-Step "Enrolling $HostName via template $TemplateName..."
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
$reqDir = Join-Path $backupDir 'enroll'
New-Item -ItemType Directory -Force -Path $reqDir | Out-Null
$infPath = Join-Path $reqDir 'qka-agent.inf'
$reqPath = Join-Path $reqDir 'qka-agent.req'
$cerPath = Join-Path $reqDir 'qka-agent.cer'
$rspPath = Join-Path $reqDir 'qka-agent.rsp'
$inf | Set-Content -LiteralPath $infPath -Encoding ASCII
# Must be elevated: UAC medium-IL filters Domain Admin → template Enroll denied; -submit without -config/-q hangs on CA picker UI.
$enrollAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $enrollAdmin) {
  throw 'Leaf enrollment requires an elevated Administrator token (UAC). Re-run elevated, or use Complete-QkaAgentEnterpriseEnrollment.ps1.'
}
Get-Process certreq -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$caConfig = "$ComputerDns\$CACommonName"
& certreq.exe -new -f -q $infPath $reqPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $reqPath)) { throw "certreq -new failed (exit=$LASTEXITCODE)" }
& certreq.exe -submit -f -q -config $caConfig -attrib "CertificateTemplate:$TemplateName" $reqPath $cerPath $rspPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $cerPath)) {
  throw "certreq -submit failed (exit=$LASTEXITCODE). Check template Enroll ACL for the elevated caller / QEC-AI-MCP`$ and CA pending requests."
}
# MachineKeySet requests need -machine; avoid -q (can return CONTEXT_E_ROLENOTFOUND).
& certreq.exe -accept -machine $cerPath
if ($LASTEXITCODE -ne 0) {
  $tmpCert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($cerPath)
  Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\LocalMachine\My | Out-Null
  & certutil.exe -repairstore MY $tmpCert.Thumbprint | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "certreq -accept / repairstore failed for $($tmpCert.Thumbprint)" }
}

$leaf = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
  if (-not $_.HasPrivateKey) { return $false }
  if ($_.Issuer -notmatch [regex]::Escape($CACommonName)) { return $false }
  if ($_.Subject -match [regex]::Escape($CACommonName)) { return $false } # exclude CA cert
  if ($_.Subject -match [regex]::Escape("CN=$HostName")) { return $true }
  $sanExt = $_.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
  return [bool]($sanExt -and $sanExt.Format($false) -match [regex]::Escape($HostName))
} | Sort-Object NotBefore -Descending | Select-Object -First 1
if (-not $leaf) { throw "Enrolled leaf for $HostName issued by $CACommonName not found in LocalMachine\My." }
$newThumb = $leaf.Thumbprint
Write-Step ("Enrolled leaf thumbprint=" + $newThumb)

# --- 6. Renewal script is maintained at Update-QkaAgentIisBindingFromStore.ps1 (do not overwrite) ---
$renewPath = 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Update-QkaAgentIisBindingFromStore.ps1'
if (-not (Test-Path $renewPath)) {
  throw "Missing $renewPath — required for automatic IIS rebind after renew."
}
$taskName = 'QKA-Agent-HTTPS-Binding-Renew'
schtasks /Delete /TN $taskName /F 2>$null | Out-Null
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$renewPath`""
schtasks /Create /TN $taskName /SC DAILY /ST 04:15 /RU SYSTEM /RL HIGHEST /TR $tr /F | Out-Null
Write-Step ("Registered scheduled task " + $taskName)

if (-not $IConfirmIisCutover) {
  Write-Host ("STOP_BEFORE_IIS_CUTOVER=New thumbprint $newThumb ready. Pass -IConfirmIisCutover (and -IAcceptProceedWithoutOffServerCaBackup if needed).")
  return
}

# --- 7. IIS cutover via dedicated script (off-server gate + audit override) ---
Write-Step 'Cutting over IIS SNI binding to enterprise leaf...'
$cutArgs = @{
  IConfirmIisCutover = $true
  CertificateThumbprint = $newThumb
  HostName = $HostName
  CACommonName = $CACommonName
  ComputerDns = $ComputerDns
  PilotThumbprint = $PilotSelfSignedThumbprint
}
if ($IAcceptProceedWithoutOffServerCaBackup) {
  $cutArgs.IAcceptProceedWithoutOffServerCaBackup = $true
}
& 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Invoke-QkaAgentIisEnterpriseCutover.ps1' @cutArgs
Write-Host 'IMPLEMENTATION_STATUS=APPLIED_WITH_CONFIRM_SWITCHES'
