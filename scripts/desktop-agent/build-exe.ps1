#Requires -Version 5.1
param(
    [string]$Python = "C:\QehcAI\Temp\python312\tools\python.exe",
    [string]$OutDir = "C:\QehcAI\QKA-Next\agent\dist"
)

$ErrorActionPreference = "Stop"
$AgentRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..\agent")
Set-Location $AgentRoot

if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source } else { throw "Python not found" }
}

& $Python -m pip install -q pyinstaller pywin32 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller/pywin32 failed" }
$env:PYTHONPATH = "$AgentRoot;$AgentRoot\..\backend\src"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# --onedir (NOT --onefile): Windows SCM requires the launched process to call
# StartServiceCtrlDispatcher. PyInstaller onefile bootloaders unpack/re-exec and
# break that contract → MSI Error 1920 / local pywintypes 1063.
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name qka-desktop-agent `
    --distpath (Join-Path $OutDir "qka-desktop-agent") `
    --workpath (Join-Path $OutDir "build") `
    --paths $AgentRoot `
    --paths (Join-Path $AgentRoot "..\backend\src") `
    --hidden-import win32timezone `
    --hidden-import win32service `
    --hidden-import win32serviceutil `
    --hidden-import win32event `
    --hidden-import servicemanager `
    --hidden-import pywintypes `
    --hidden-import pythoncom `
    (Join-Path $AgentRoot "qka_desktop_agent\main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit $LASTEXITCODE" }

$payload = Join-Path $OutDir "qka-desktop-agent\qka-desktop-agent"
$exe = Join-Path $payload "qka-desktop-agent.exe"
# PyInstaller may place exe directly under distpath\name depending on version.
if (-not (Test-Path $exe)) {
    $alt = Join-Path $OutDir "qka-desktop-agent\qka-desktop-agent.exe"
    if (Test-Path $alt) {
        $payload = Join-Path $OutDir "qka-desktop-agent"
        $exe = $alt
    }
}
if (-not (Test-Path $exe)) { throw "Expected exe missing under onedir payload" }
if (-not (Test-Path (Join-Path $payload "_internal"))) {
    Write-Warning "Expected _internal folder missing; verify onedir layout before MSI build."
}
Write-Host "PAYLOAD_DIR=$payload"
Write-Host "EXE_OUT=$exe"
Write-Host ("EXE_SIZE_BYTES=" + (Get-Item $exe).Length)
Write-Host "SIGNING=not performed"
