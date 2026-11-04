#Requires -Version 5.1
<#
.SYNOPSIS
  Watchdog: if the existing tunnel /readyz is down, restart via Start-ExistingTunnelTo5265.ps1.
#>
$ErrorActionPreference = "Stop"
$ReadyUrl = "http://127.0.0.1:8080/readyz"
$Starter = "C:\QehcAI\QKA-Next\scripts\mcp-tunnel\Start-ExistingTunnelTo5265.ps1"
$LogDir = Join-Path $env:LOCALAPPDATA "QkaMcpTunnel\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$WatchLog = Join-Path $LogDir "watchdog.log"

function Write-Watch([string]$Message) {
    $line = "{0:o} {1}" -f (Get-Date).ToUniversalTime(), $Message
    Add-Content -Path $WatchLog -Value $line
}

try {
    $r = Invoke-WebRequest -Uri $ReadyUrl -UseBasicParsing -TimeoutSec 4
    if ($r.StatusCode -eq 200) {
        Write-Watch "OK readyz=200"
        exit 0
    }
}
catch {
    Write-Watch "DOWN $($_.Exception.Message) — restarting"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Starter -SkipDoctor
if ($LASTEXITCODE -ne 0) {
    Write-Watch "RESTART_FAILED exit=$LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Watch "RESTARTED_OK"
