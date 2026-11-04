#Requires -Version 5.1
param(
    [string]$PayloadDir = "C:\QehcAI\QKA-Next\agent\dist\qka-desktop-agent",
    [string]$OutDir = "C:\QehcAI\QKA-Next\agent\dist"
)

$ErrorActionPreference = "Stop"
$wxs = Join-Path $PSScriptRoot "QKA-Agent.wxs"
$candle = Get-Command candle.exe -ErrorAction SilentlyContinue
$light = Get-Command light.exe -ErrorAction SilentlyContinue
if (-not $candle -or -not $light) {
    Write-Host "WIX_MISSING=true"
    Write-Host "BLOCKER=WiX Toolset (candle/light) is not installed on this build host."
    Write-Host "FALLBACK=Use Install-QkaAgent.ps1 for pilot silent install until WiX is available."
    Write-Host "GPO_READY=wxs source prepared; MSI not produced in this environment."
    exit 2
}
if (-not (Test-Path (Join-Path $PayloadDir "qka-desktop-agent.exe"))) {
    throw "Missing payload exe. Run build-exe.ps1 first."
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$wixobj = Join-Path $OutDir "QKA-Agent.wixobj"
& $candle.Source -nologo -dPayload="$PayloadDir" -o $wixobj $wxs
if ($LASTEXITCODE -ne 0) { throw "candle failed" }
$msi = Join-Path $OutDir "QKA-Agent.msi"
& $light.Source -nologo -o $msi $wixobj
if ($LASTEXITCODE -ne 0) { throw "light failed" }
Write-Host "MSI_OUT=$msi"
Write-Host "SILENT_INSTALL=msiexec /i `"$msi`" /qn QKASERVERURL=https://qka.internal.example"
Write-Host "SILENT_UNINSTALL=msiexec /x `"$msi`" /qn"
Write-Host "SIGNING=not performed unless signtool + approved cert configured"
