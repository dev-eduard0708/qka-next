#Requires -Version 5.1
param(
    [string]$PayloadDir = "",
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

if (-not $PayloadDir) {
    $candidate = Join-Path $OutDir "qka-desktop-agent\qka-desktop-agent"
    if (Test-Path (Join-Path $candidate "qka-desktop-agent.exe")) {
        $PayloadDir = $candidate
    } else {
        $PayloadDir = Join-Path $OutDir "qka-desktop-agent"
    }
}

$candle = Resolve-WixTool "candle.exe"
$light = Resolve-WixTool "light.exe"
$heat = Resolve-WixTool "heat.exe"
if (-not $candle -or -not $light -or -not $heat) {
    Write-Host "WIX_MISSING=true"
    Write-Host "BLOCKER=WiX Toolset (candle/light/heat) is not installed on this build host."
    exit 2
}
$exe = Join-Path $PayloadDir "qka-desktop-agent.exe"
$internal = Join-Path $PayloadDir "_internal"
if (-not (Test-Path $exe)) { throw "Missing payload exe: $exe (run build-exe.ps1 --onedir first)" }
if (-not (Test-Path $internal)) { throw "Missing onedir _internal folder: $internal (onefile MSI is not supported)" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$harvested = Join-Path $OutDir "AgentInternal.wxs"
$harvestObj = Join-Path $OutDir "AgentInternal.wixobj"
$wixobj = Join-Path $OutDir "QKA-Agent.wixobj"

# Harvest PyInstaller _internal dependencies into INTERNALFOLDER.
& $heat dir $internal -nologo -cg AgentInternalComponents -gg -sfrag -srd -scom -sreg `
    -dr INTERNALFOLDER -var var.Internal -out $harvested
if ($LASTEXITCODE -ne 0) { throw "heat failed" }

& $candle -nologo "-dPayload=$PayloadDir" "-dInternal=$internal" -o $wixobj $wxs
if ($LASTEXITCODE -ne 0) { throw "candle (product) failed" }
& $candle -nologo "-dPayload=$PayloadDir" "-dInternal=$internal" -o $harvestObj $harvested
if ($LASTEXITCODE -ne 0) { throw "candle (harvest) failed" }

$msi = Join-Path $OutDir "QKA-Agent.msi"
& $light -nologo -o $msi $wixobj $harvestObj
if ($LASTEXITCODE -ne 0) { throw "light failed" }

Write-Host "MSI_OUT=$msi"
Write-Host "MSI_VERSION=0.1.3.0"
Write-Host "MSI_LAYOUT=onedir"
Write-Host "MSI_SHA256=$((Get-FileHash $msi -Algorithm SHA256).Hash)"
Write-Host "SERVICE_ACCOUNT=NT AUTHORITY\NETWORK SERVICE"
Write-Host "SILENT_INSTALL=msiexec /i `"$msi`" /qn QKASERVERURL=https://qka-agent.qehc.edu.sa"
Write-Host "SILENT_UNINSTALL=msiexec /x `"$msi`" /qn"
Write-Host "SIGNING=not performed (production blocker until approved code-signing certificate is configured)"
