<#
.SYNOPSIS
  Approval-gated Trusted Root deployment for qka-agent.qehc.edu.sa (public CER only).

.DESCRIPTION
  Distributes verified public certificate trust to QEHC domain-joined Windows
  workstations via GPO Public Key Policies — not private keys, not TLS bypass.

  SCOPE (default): link to all OU=*WorkStation* under the domain that are NOT under
  Domain Controllers / Servers paths. GpoApply = Domain Computers.
  Does NOT link at domain root. Does NOT link Domain Controllers or Servers OUs.

  SECURITY IMPLICATIONS (self-signed):
  - Trusting this CER in LocalMachine\Root makes ANY TLS endpoint presenting this
    exact certificate trusted for TLS on those PCs (MITM risk if the private key
    on QEC-AI-MCP is compromised). Prefer a managed internal CA / public CA cert
    for production and remove this pilot Root once replaced.
  - Scope is limited to workstation OUs so servers/DCs do not receive the trust.

  DEFAULT = dry-run. Live GPO create/link requires -IConfirmCreateTrustGpo.
  Importing into Trusted Root Certification Authorities still requires the GPMC
  Public Key Policies import step after the GPO shell exists (thumbprint verified).

.EXAMPLE
  .\Deploy-QkaAgentHttpsTrustGpo.ps1
  .\Deploy-QkaAgentHttpsTrustGpo.ps1 -IConfirmCreateTrustGpo
#>
[CmdletBinding()]
param(
  [string]$CerPath = 'C:\QehcAI\QKA-Next\Temp\qka-agent-https\qka-agent.qehc.edu.sa.cer',
  [string]$ExpectedThumbprint = 'D030168213D1F589D17FC170A70A4B7340A65BA8',
  [string]$ExpectedSha256 = '18bc5a3d777405f7f12b0212eff8e67fd6a4eb20749410e2e2521453974b9eaa',
  # Empty = auto-discover workstation OUs (excludes Domain Controllers / Servers paths).
  [string[]]$TargetOuDns = @(),
  [string]$GpoName = 'QKA-Agent-HTTPS-Trust',
  [string]$ApplyPrincipal = 'Domain Computers',
  [switch]$IConfirmCreateTrustGpo
)

$ErrorActionPreference = 'Stop'

function Get-WorkstationOuDns {
  Import-Module ActiveDirectory -ErrorAction Stop
  $ous = @(
    Get-ADOrganizationalUnit -Filter * -Properties DistinguishedName |
      Where-Object {
        $_.DistinguishedName -match '(?i)WorkStations?|Workstations|Desktops|Clients' -and
        $_.DistinguishedName -notmatch '(?i)OU=Domain Controllers|OU=Servers[,]|OU=Apps Servers|OU=Member Servers'
      } |
      ForEach-Object { $_.DistinguishedName }
  )
  return ($ous | Sort-Object -Unique)
}

Write-Host '=== QKA HTTPS TRUST GPO — WORKSTATION SCOPE ==='
Write-Host 'TLS_FAILURE_REASON=Self-signed qka-agent.qehc.edu.sa not in LocalMachine\Root on WSIT03/WSIT04'
Write-Host 'CERTIFICATE_CHAIN=Self-signed leaf CN=qka-agent.qehc.edu.sa (no intermediate)'
Write-Host ("EXPECTED_THUMBPRINT=" + $ExpectedThumbprint)
Write-Host ("EXPECTED_SHA256=" + $ExpectedSha256)
Write-Host 'PRODUCTION_PREFERENCE=Replace with managed CA-issued certificate; remove this pilot Root afterward'
Write-Host 'ADMIN_APPROVAL_REQUIRED=YES'
Write-Host ''

if (-not (Test-Path -LiteralPath $CerPath)) {
  throw "CER not found: $CerPath"
}

$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2((Resolve-Path $CerPath).Path)
$tp = $cert.Thumbprint.ToUpperInvariant()
if ($tp -ne $ExpectedThumbprint.ToUpperInvariant()) {
  throw "CER thumbprint mismatch: got $tp expected $ExpectedThumbprint"
}
$bytes = [IO.File]::ReadAllBytes((Resolve-Path $CerPath).Path)
$sha = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
if ($sha -ne $ExpectedSha256.ToLowerInvariant()) {
  throw "CER SHA256 mismatch: got $sha expected $ExpectedSha256"
}
if ($cert.HasPrivateKey) {
  throw 'Refusing CER that includes a private key.'
}
if ($cert.Subject -notmatch 'qka-agent\.qehc\.edu\.sa') {
  throw "Unexpected subject: $($cert.Subject)"
}

Write-Host ("VERIFIED_CER=" + $CerPath)
Write-Host ("VERIFIED_THUMBPRINT=" + $tp)
Write-Host ("VERIFIED_SHA256=" + $sha)
Write-Host ("NOT_AFTER=" + $cert.NotAfter)

if (-not (Get-Module -ListAvailable GroupPolicy)) {
  throw 'RSAT GroupPolicy module required.'
}
Import-Module GroupPolicy -ErrorAction Stop
Import-Module ActiveDirectory -ErrorAction Stop

if (-not $TargetOuDns -or $TargetOuDns.Count -eq 0) {
  $TargetOuDns = @(Get-WorkstationOuDns)
}
if ($TargetOuDns.Count -eq 0) {
  throw 'No workstation OUs discovered.'
}

Write-Host '--- TARGET_OUS (workstations only; servers/DCs excluded) ---'
foreach ($dn in $TargetOuDns) {
  if ($dn -match '(?i)^DC=') { throw "Refusing domain-root link: $dn" }
  if ($dn -match '(?i)OU=Domain Controllers|OU=Servers[,]|OU=Apps Servers') {
    throw "Refusing servers/DC OU: $dn"
  }
  $null = Get-ADOrganizationalUnit -Identity $dn -ErrorAction Stop
  Write-Host ("OU=" + $dn)
}

$existing = Get-GPO -Name $GpoName -ErrorAction SilentlyContinue
Write-Host ("EXISTING_GPO=" + $(if ($existing) { "$($existing.DisplayName) $($existing.Id)" } else { 'none' }))

Write-Host ''
Write-Host 'REQUIRED_GPO_CHANGE='
Write-Host '  Create/link QKA-Agent-HTTPS-Trust to each workstation OU above'
Write-Host '  Domain Computers GpoApply; Authenticated Users GpoRead'
Write-Host '  Computer Configuration → Policies → Windows Settings → Security Settings'
Write-Host '    → Public Key Policies → Trusted Root Certification Authorities → Import CER'
Write-Host ("  Confirm thumbprint " + $ExpectedThumbprint + " (public only)")
Write-Host '  Do not import PFX/private key; do not disable TLS validation'
Write-Host ''
Write-Host 'SECURITY_NOTE=Self-signed Root trust on all workstations increases blast radius if the'
Write-Host '  QEC-AI-MCP private key is compromised. Prefer internal CA / public CA for production.'

if (-not $IConfirmCreateTrustGpo) {
  Write-Host ''
  Write-Host 'Dry-run only. After approval:'
  Write-Host ("  powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -IConfirmCreateTrustGpo")
  Write-Host 'IMPLEMENTATION_STATUS=PROPOSED_NOT_APPLIED'
  return
}

if (-not $existing) {
  $gpo = New-GPO -Name $GpoName -Comment 'QKA agent HTTPS trust (public CER only; workstation OUs; no private key)'
  Write-Host ("CREATED_GPO=" + $gpo.Id)
} else {
  $gpo = $existing
  Write-Host ("REUSING_GPO=" + $gpo.Id)
}

foreach ($dn in $TargetOuDns) {
  $link = (Get-GPInheritance -Target $dn).GpoLinks |
    Where-Object { $_.GpoId -eq $gpo.Id.ToString() -or $_.DisplayName -eq $GpoName }
  if (-not $link) {
    New-GPLink -Guid $gpo.Id -Target $dn -LinkEnabled Yes | Out-Null
    Write-Host ("GPLINK_CREATED=" + $dn)
  } else {
    Write-Host ("GPLINK_EXISTS=" + $dn)
  }
}

Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
Set-GPPermission -Guid $gpo.Id -TargetName $ApplyPrincipal -TargetType Group -PermissionLevel GpoApply -Replace | Out-Null

$pdc = (Get-ADDomain).PDCEmulator
$sysvolGpo = "\\$pdc\SYSVOL\qehc.edu.sa\Policies\{$($gpo.Id)}"
$stageDir = Join-Path $sysvolGpo 'Machine\QKA-HTTPS-Trust'
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
$staged = Join-Path $stageDir 'qka-agent.qehc.edu.sa.cer'
Copy-Item -LiteralPath $CerPath -Destination $staged -Force
@(
  "thumbprint=$ExpectedThumbprint"
  "sha256=$ExpectedSha256"
  "import_into=Trusted Root Certification Authorities (Computer Public Key Policies)"
  "private_key=none"
  "scope=workstation_ous_only"
) | Set-Content -Path (Join-Path $stageDir 'FINGERPRINT.txt') -Encoding UTF8

Write-Host ("STAGED_CER=" + $staged)
Write-Host 'GpoApply:'
Get-GPPermission -Guid $gpo.Id -All | Where-Object { $_.Permission -eq 'GpoApply' } | ForEach-Object {
  Write-Host ("  APPLY=" + $_.Trustee.Name)
}
Write-Host 'IMPLEMENTATION_STATUS=GPO_SHELL_LINKED_CER_STAGED — complete Trusted Root import in GPMC, then await background policy refresh'
Write-Host 'POST_TRUST=Existing QKA-Agent-Install tasks + valid SID tokens should call /install-authorization without redeploy'
