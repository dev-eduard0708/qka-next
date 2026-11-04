#Requires -Version 5.1
<#
.SYNOPSIS
  Silent install / uninstall helper for the QKA Desktop Agent pilot.
  Prefer QKA-Agent.msi when WiX build is available. This script registers a
  LocalService-friendly scheduled task / service wrapper without embedding secrets.
#>
param(
    [ValidateSet("install", "uninstall")]
    [string]$Action = "install",
    [string]$ServerBaseUrl = "",
    [string]$InstallDir = "$env:ProgramFiles\QKA\DesktopAgent",
    [string]$SourceDir = ""
)

$ErrorActionPreference = "Stop"
$ServiceName = "QkaDesktopAgent"
$TaskName = "QKA-Desktop-Agent"
$DataDir = Join-Path $env:ProgramData "QKA\DesktopAgent"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator rights are required for install/uninstall."
    }
}

Assert-Admin

if ($Action -eq "uninstall") {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    sc.exe stop $ServiceName 2>$null | Out-Null
    sc.exe delete $ServiceName 2>$null | Out-Null
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    Write-Host "UNINSTALLED"
    exit 0
}

if (-not $SourceDir) {
    $SourceDir = Join-Path $PSScriptRoot "..\..\agent\dist\qka-desktop-agent"
}
if (-not (Test-Path $SourceDir)) {
    throw "Agent payload not found at $SourceDir. Build with build-exe.ps1 first."
}
if (-not $ServerBaseUrl) {
    throw "ServerBaseUrl is required (internal HTTPS QKA URL). Do not use localhost for employee PCs."
}
if ($ServerBaseUrl -match '127\.0\.0\.1|localhost') {
    Write-Warning "ServerBaseUrl appears to be loopback. Employee PCs need a reachable internal HTTPS endpoint."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir '*') -Destination $InstallDir -Recurse -Force

$exe = Get-ChildItem $InstallDir -Filter 'qka-desktop-agent.exe' -Recurse | Select-Object -First 1
if (-not $exe) {
    # Pure Python fallback launcher
    $launcher = Join-Path $InstallDir "run-agent.cmd"
    @"
@echo off
set PYTHONPATH=$InstallDir
"%ProgramFiles%\QKA\Python\python.exe" -m qka_desktop_agent.main
"@ | Set-Content $launcher -Encoding ASCII
    $runTarget = $launcher
} else {
    $runTarget = $exe.FullName
}

$config = @{
    serverBaseUrl = $ServerBaseUrl.TrimEnd('/')
    pollSeconds = 30
    verifyTls = $true
}
$config | ConvertTo-Json | Set-Content (Join-Path $DataDir "agent.config.json") -Encoding UTF8

# Register a hidden logon/boot scheduled task as a recovery-friendly service substitute
# when a signed MSI/service binary is not yet available. Prefer Local Service via task.
$action = New-ScheduledTaskAction -Execute $runTarget
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "LOCAL SERVICE" -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host "INSTALLED_DIR=$InstallDir"
Write-Host "DATA_DIR=$DataDir"
Write-Host "TASK=$TaskName"
Write-Host "NOTE=Enrollment code must be supplied separately (env/registry/config). Not embedded in installer."
Write-Host "NOTE=Deployment is not complete until the agent enrolls and heartbeats to QKA."
