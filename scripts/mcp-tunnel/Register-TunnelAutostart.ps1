#Requires -Version 5.1
<#
.SYNOPSIS
  Register a Current-User logon scheduled task that restores the EXISTING
  qka-mcp-local tunnel (no new Platform tunnel) and a watchdog every 5 minutes.
#>
param(
    [string]$TaskName = "QKA-Next-MCP-Tunnel",
    [string]$WatchdogTaskName = "QKA-Next-MCP-Tunnel-Watchdog"
)

$ErrorActionPreference = "Stop"
$Starter = "C:\QehcAI\QKA-Next\scripts\mcp-tunnel\Start-ExistingTunnelTo5265.ps1"
$Watchdog = "C:\QehcAI\QKA-Next\scripts\mcp-tunnel\Watch-ExistingTunnel.ps1"
if (-not (Test-Path $Starter)) { throw "Missing $Starter" }
if (-not (Test-Path $Watchdog)) { throw "Missing $Watchdog" }

function Register-HiddenPwshTask([string]$Name, [string]$Script, $Trigger) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
    )
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "TASK_REGISTERED=$Name"
}

$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-HiddenPwshTask -Name $TaskName -Script $Starter -Trigger $logon

$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-HiddenPwshTask -Name $WatchdogTaskName -Script $Watchdog -Trigger $watch

Get-ScheduledTask -TaskName $TaskName, $WatchdogTaskName | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host "Autostart registered for existing tunnel only (profile qka-mcp-local → :5265)."
