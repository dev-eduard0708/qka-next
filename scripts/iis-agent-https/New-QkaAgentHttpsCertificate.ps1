<#
.SYNOPSIS
  Create or import a TLS certificate for https://qka-agent.qehc.edu.sa (server host).

.DESCRIPTION
  Prefer -PfxPath (organization-approved cert). If unavailable, -PilotSelfSigned creates a
  PILOT-ONLY self-signed cert with SAN DNS=qka-agent.qehc.edu.sa after -IApprovePilotSelfSigned.

  Never disables TLS verification. Never installs AD CS.
  Private key material stays under LocalMachine\My (and optional PFX under -ExportDir).
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
  [string]$ExportDir = 'C:\QehcAI\Temp\qka-agent-https',
  [int]$Years = 1
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
  Write-Warning 'PILOT-ONLY self-signed certificate. Trust must be limited to the approved pilot PC Root store. Prefer org CA / DNS-01 LE when available.'
  $cert = New-SelfSignedCertificate `
    -DnsName $DnsName `
    -CertStoreLocation 'Cert:\LocalMachine\My' `
    -FriendlyName $FriendlyName `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy Exportable `
    -NotAfter (Get-Date).AddYears([Math]::Max(1, $Years)) `
    -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.1')

  $cerPath = Join-Path $ExportDir 'qka-agent.qehc.edu.sa.cer'
  Export-Certificate -Cert $cert -FilePath $cerPath -Force | Out-Null
  Write-Host ("Pilot CER (public only) exported: " + $cerPath)
  Write-Host 'On the pilot PC only: Import-Certificate -FilePath <cer> -CertStoreLocation Cert:\LocalMachine\Root'
  Write-Host 'Do not distribute this CER domain-wide. Do not commit it if it contains sensitive metadata you do not want in git.'
}

$sans = @($cert.DnsNameList | ForEach-Object { $_.Unicode })
Write-Host ("Subject=" + $cert.Subject)
Write-Host ("Thumbprint=" + $cert.Thumbprint)
Write-Host ("NotAfter=" + $cert.NotAfter.ToString('u'))
Write-Host ("SAN=" + ($sans -join ', '))
if ($DnsName -notin $sans -and $cert.Subject -notmatch [regex]::Escape($DnsName)) {
  Write-Warning "SAN may not include $DnsName — verify before binding."
}

# Machine-readable output for Install-QkaAgentIisHttps.ps1
[pscustomobject]@{
  Thumbprint = $cert.Thumbprint
  Subject    = $cert.Subject
  NotAfter   = $cert.NotAfter
  DnsNames   = $sans
  StorePath  = ("Cert:\LocalMachine\My\" + $cert.Thumbprint)
} | ConvertTo-Json -Compress | Set-Content -Path (Join-Path $ExportDir 'last-cert.json') -Encoding UTF8

Write-Host ("Wrote " + (Join-Path $ExportDir 'last-cert.json'))
return $cert.Thumbprint
