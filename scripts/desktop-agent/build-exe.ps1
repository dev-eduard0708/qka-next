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

& $Python -m pip install -q pyinstaller pywin32 2>$null
$env:PYTHONPATH = "$AgentRoot;$AgentRoot\..\backend\src"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name qka-desktop-agent `
    --distpath (Join-Path $OutDir "qka-desktop-agent") `
    --workpath (Join-Path $OutDir "build") `
    --paths $AgentRoot `
    --paths (Join-Path $AgentRoot "..\backend\src") `
    (Join-Path $AgentRoot "qka_desktop_agent\main.py")

Write-Host "EXE_OUT=$(Join-Path $OutDir 'qka-desktop-agent\qka-desktop-agent.exe')"
Write-Host "SIGNING=not performed (report as production blocker unless an approved cert is configured)"
