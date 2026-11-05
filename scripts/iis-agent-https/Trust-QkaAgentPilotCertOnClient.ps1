<#
.SYNOPSIS
  Pilot-PC-only: import the public CER into LocalMachine\Root so TLS validates without bypass.

.DESCRIPTION
  Run elevated ON THE APPROVED PILOT PC only. Do not push via GPO / all workstations.
  Never use -SkipCertificateCheck on the agent.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$CerPath,

  [switch]$IConfirmPilotPcOnly
)

$ErrorActionPreference = 'Stop'
if (-not $IConfirmPilotPcOnly) {
  throw 'Refusing: pass -IConfirmPilotPcOnly to acknowledge this trust is limited to this pilot PC.'
}
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  throw 'Run elevated on the pilot PC.'
}
if (-not (Test-Path -LiteralPath $CerPath)) {
  throw "CER not found: $CerPath"
}

$cert = Import-Certificate -FilePath $CerPath -CertStoreLocation Cert:\LocalMachine\Root
Write-Host ("Trusted thumbprint=" + $cert.Thumbprint + " on " + $env:COMPUTERNAME)
Write-Host 'Validate (no TLS bypass):'
Write-Host '  Invoke-WebRequest https://qka-agent.qehc.edu.sa/health -UseBasicParsing'
