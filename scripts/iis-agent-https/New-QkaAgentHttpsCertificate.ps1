<#
.SYNOPSIS
  Create or import a TLS certificate for https://qka-agent.qehc.edu.sa (server host).

.DESCRIPTION
  Prefer -PfxPath (organization-approved cert). If unavailable, -PilotSelfSigned creates a
  PILOT-ONLY self-signed cert with SAN DNS=qka-agent.qehc.edu.sa after -IApprovePilotSelfSigned.

  Pilot self-signed: 90-day lifetime, non-exportable private key, public CER export only.
  Never disables TLS verification. Never installs AD CS.
  Do not commit PFX/CER/keys to git.

.PARAMETER IApprovePilotSelfSigned
  Required together with -PilotSelfSigned. Explicit admin acknowledgement for pilot-only use.
#>
[CmdletBinding(DefaultParameterSetName = 'OrgPfx')]
param(
  [Parameter(ParameterSetName = 'OrgPfx')]
  [string]$PfxPath = '',

  [Parameter(ParameterSetName = 'OrgPfx')]
  [SecureString]$PfxPassword,

  [Parameter(ParameterSetName = 'Pilot', Mandatory)]
  [switch]$PilotSelfSigned,

  [Parameter(ParameterSetName = 'Pilot', Mandatory)]
  [switch]$IApprovePilotSelfSigned,

  [string]$DnsName = 'qka-agent.qehc.edu.sa',
  [string]$FriendlyName = 'QKA Agent HTTPS (pilot)',
  [string]$ExportDir = 'C:\QehcAI\Temp\qka-agent-https'
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated as an authorized administrator (LocalMachine certificate store).'
}

New-Item -ItemType Directory -Force -Path $ExportDir | Out-Null

if ($PSCmdlet.ParameterSetName -eq 'OrgPfx') {
  if (-not $PfxPath -or -not (Test-Path -LiteralPath $PfxPath)) {
    throw 'Provide -PfxPath to an organization-approved certificate, or use -PilotSelfSigned -IApprovePilotSelfSigned.'
  }
  if (-not $PfxPassword) {
    $PfxPassword = Read-Host -AsSecureString -Prompt 'PFX password'
  }
  $cert = Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation Cert:\LocalMachine\My -Password $PfxPassword
  Write-Host ("Imported thumbprint=" + $cert.Thumbprint)
} else {
  if (-not $IApprovePilotSelfSigned) {
    throw 'Pilot self-signed requires -IApprovePilotSelfSigned (explicit administrator approval).'
  }
  Write-Warning 'PILOT-ONLY self-signed certificate (90 days, non-exportable key). Trust only on the approved pilot PC Root store. Prefer org CA / DNS-01 LE when available.'
  $notAfter = (Get-Date).AddDays(90)
  $cert = New-SelfSignedCertificate `
    -DnsName $DnsName `
    -CertStoreLocation 'Cert:\LocalMachine\My' `
    -FriendlyName $FriendlyName `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy NonExportable `
    -NotAfter $notAfter `
    -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1')

  $cerPath = Join-Path $ExportDir 'qka-agent.qehc.edu.sa.cer'
  # Public certificate only (no private key) — safe for pilot-PC trust import.
  Export-Certificate -Cert $cert -FilePath $cerPath -Force | Out-Null
  Write-Host ("Pilot CER (public only) exported: " + $cerPath)
  Write-Host 'On the pilot PC only: .\Trust-QkaAgentPilotCertOnClient.ps1 -CerPath <cer> -IConfirmPilotPcOnly'
  Write-Host 'Private key is NonExportable in LocalMachine\My — no PFX export.'
}

$sans = @($cert.DnsNameList | ForEach-Object { $_.Unicode })
Write-Host ("Subject=" + $cert.Subject)
Write-Host ("Thumbprint=" + $cert.Thumbprint)
Write-Host ("NotAfter=" + $cert.NotAfter.ToString('u'))
Write-Host ("SAN=" + ($sans -join ', '))
if ($DnsName -notin $sans -and $cert.Subject -notmatch [regex]::Escape($DnsName)) {
  Write-Warning "SAN may not include $DnsName — verify before binding."
}

[pscustomobject]@{
  Thumbprint   = $cert.Thumbprint
  Subject      = $cert.Subject
  NotAfter     = $cert.NotAfter
  DnsNames     = $sans
  StorePath    = ("Cert:\LocalMachine\My\" + $cert.Thumbprint)
  PilotOnly    = ($PSCmdlet.ParameterSetName -eq 'Pilot')
  KeyExportable = ($PSCmdlet.ParameterSetName -ne 'Pilot')
} | ConvertTo-Json -Compress | Set-Content -Path (Join-Path $ExportDir 'last-cert.json') -Encoding UTF8

Write-Host ("Wrote " + (Join-Path $ExportDir 'last-cert.json'))
return $cert.Thumbprint
