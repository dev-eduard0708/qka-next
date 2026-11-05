#Requires -Version 5.1
<#
.SYNOPSIS
  Silent install / uninstall helper for the QKA Desktop Agent pilot.
  Prefer QKA-Agent.msi when WiX build is available. This script registers a
  NetworkService scheduled task / service wrapper (computer$ SMB auth for token-drop).
  Scanner stays non-admin / read-only — never SYSTEM or Administrator.
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

# Network Service: domain SMB uses COMPUTER$ (Local Service is anonymous). Restricted locally.
$action = New-ScheduledTaskAction -Execute $runTarget
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NETWORK SERVICE" -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
# Prefer real Windows service when exe supports it
if ($exe) {
  sc.exe stop $ServiceName 2>$null | Out-Null
  sc.exe delete $ServiceName 2>$null | Out-Null
  sc.exe create $ServiceName binPath= "`"$($exe.FullName)`" service" start= auto obj= "NT AUTHORITY\NETWORK SERVICE" DisplayName= "QKA Desktop Agent" | Out-Null
  sc.exe failure $ServiceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
  sc.exe failureflag $ServiceName 1 | Out-Null
  sc.exe description $ServiceName "Read-only QKA indexer (Network Service / computer account for token-drop)" | Out-Null
  sc.exe start $ServiceName | Out-Null
} else {
  Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

Write-Host "INSTALLED_DIR=$InstallDir"
Write-Host "DATA_DIR=$DataDir"
Write-Host "SERVICE_ACCOUNT=NT AUTHORITY\NETWORK SERVICE"
Write-Host "NOTE=Enrollment via unc_acl_drop or one-time token. Not embedded in installer."
Write-Host "NOTE=Deployment is not complete until the agent enrolls and heartbeats to QKA."
