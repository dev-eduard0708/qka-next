#Requires -Version 5.1
param(
    [string]$PayloadDir = "C:\QehcAI\QKA-Next\agent\dist\qka-desktop-agent",
    [string]$OutDir = "C:\QehcAI\QKA-Next\agent\dist",
    [string]$WixBinDir = ""
)

$ErrorActionPreference = "Stop"
$wxs = Join-Path $PSScriptRoot "QKA-Agent.wxs"

function Resolve-WixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        $WixBinDir,
        "C:\QehcAI\Temp\wix314",
        "C:\Program Files (x86)\WiX Toolset v3.14\bin",
        "C:\Program Files (x86)\WiX Toolset v3.11\bin"
    ) | Where-Object { $_ }
    foreach ($dir in $candidates) {
        $path = Join-Path $dir $Name
        if (Test-Path $path) { return $path }
    }
    return $null
}

$candle = Resolve-WixTool "candle.exe"
$light = Resolve-WixTool "light.exe"
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
& $candle -nologo "-dPayload=$PayloadDir" -o $wixobj $wxs
if ($LASTEXITCODE -ne 0) { throw "candle failed" }
$msi = Join-Path $OutDir "QKA-Agent.msi"
& $light -nologo -o $msi $wixobj
if ($LASTEXITCODE -ne 0) { throw "light failed" }
Write-Host "MSI_OUT=$msi"
Write-Host "MSI_VERSION=0.1.1.0"
Write-Host "SERVICE_ACCOUNT=NT AUTHORITY\NETWORK SERVICE"
Write-Host "SILENT_INSTALL=msiexec /i `"$msi`" /qn QKASERVERURL=https://qka-agent.qehc.edu.sa"
Write-Host "SILENT_UNINSTALL=msiexec /x `"$msi`" /qn"
Write-Host "SIGNING=not performed (production blocker until approved code-signing certificate is configured)"
