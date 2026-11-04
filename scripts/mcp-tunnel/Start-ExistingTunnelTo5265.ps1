#Requires -Version 5.1
<#
.SYNOPSIS
  Start the EXISTING Secure MCP Tunnel (qka-mcp-local → :5265/mcp, health :8080).
  Does not create a Platform tunnel. Loads CONTROL_PLANE_API_KEY from (in order):
  process env, DPAPI vault, or QKA-Next .env OPENAI_API_KEY / CONTROL_PLANE_API_KEY.
#>
param(
    [string]$Profile = "qka-mcp-local",
    [string]$HealthListenAddr = "127.0.0.1:8080",
    [string]$McpServerUrl = "http://127.0.0.1:5265/mcp",
    [string]$EnvFile = "C:\QehcAI\QKA-Next\.env",
    [string]$TunnelClient = "C:\QehcAI\App\scripts\mcp-tunnel\bin\tunnel-client.exe",
    [switch]$PersistKeyToVault,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$StateDir = Join-Path $env:LOCALAPPDATA "QkaMcpTunnel"
$VaultPath = Join-Path $StateDir "control-plane-api-key.dpapi"
$StatePath = Join-Path $StateDir "state.json"
$ProfilePath = Join-Path $env:APPDATA "tunnel-client\$Profile.yaml"
$LogDir = Join-Path $StateDir "logs"
$OutLog = Join-Path $LogDir "tunnel-client.out.log"
$ErrLog = Join-Path $LogDir "tunnel-client.err.log"

function Assert-McpReady([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method POST -Headers @{
            Accept = "application/json, text/event-stream"
            "Content-Type" = "application/json"
        } -Body '{}' -UseBasicParsing -TimeoutSec 8
        $code = [int]$resp.StatusCode
    }
    catch {
        if (-not $_.Exception.Response) { throw "MCP not reachable at $Url : $($_.Exception.Message)" }
        $code = [int]$_.Exception.Response.StatusCode
    }
    if ($code -notin 200, 401) { throw "Unexpected MCP status $code from $Url" }
    Write-Host "MCP_TARGET_OK=$Url STATUS=$code"
}

function Read-EnvFileValue([string]$Path, [string]$Name) {
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*([^#=]+)=(.*)$') {
            if ($matches[1].Trim() -eq $Name) {
                return $matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $null
}

function Save-DpapiSecret([string]$Path, [string]$Plain) {
    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $bytes = [Text.Encoding]::UTF8.GetBytes($Plain)
    $protected = [Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [IO.File]::WriteAllBytes($Path, $protected)
    Write-Host "VAULT_SAVED=$Path bytes=$($protected.Length)"
}

function Load-DpapiSecret([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $protected = [IO.File]::ReadAllBytes($Path)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Text.Encoding]::UTF8.GetString($bytes)
}

if (-not (Test-Path $TunnelClient)) { throw "tunnel-client not found: $TunnelClient" }
if (-not (Test-Path $ProfilePath)) { throw "Missing profile $ProfilePath" }
if (-not (Test-Path $StatePath)) { throw "Missing state $StatePath" }

$state = Get-Content $StatePath -Raw | ConvertFrom-Json
$tunnelId = [string]$state.TunnelId
if ([string]::IsNullOrWhiteSpace($tunnelId)) { throw "state.json missing TunnelId" }
$env:CONTROL_PLANE_TUNNEL_ID = $tunnelId

Assert-McpReady -Url $McpServerUrl

# Resolve runtime key without printing it.
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    $fromVault = Load-DpapiSecret -Path $VaultPath
    if ($fromVault) {
        $env:CONTROL_PLANE_API_KEY = $fromVault
        Write-Host "CONTROL_PLANE_API_KEY loaded from DPAPI vault (len=$($fromVault.Length))"
    }
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    $fromEnvFile = Read-EnvFileValue -Path $EnvFile -Name "CONTROL_PLANE_API_KEY"
    if (-not $fromEnvFile) { $fromEnvFile = Read-EnvFileValue -Path $EnvFile -Name "OPENAI_API_KEY" }
    if ($fromEnvFile) {
        $env:CONTROL_PLANE_API_KEY = $fromEnvFile
        Write-Host "CONTROL_PLANE_API_KEY loaded from .env (len=$($fromEnvFile.Length); value not printed)"
    }
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "CONTROL_PLANE_API_KEY unavailable. Set env, populate DPAPI vault, or add CONTROL_PLANE_API_KEY/OPENAI_API_KEY to .env"
}

if ($PersistKeyToVault -or -not (Test-Path $VaultPath)) {
    Save-DpapiSecret -Path $VaultPath -Plain $env:CONTROL_PLANE_API_KEY
}

# Ensure profile still targets :5265 (never invent a new tunnel id).
$profileRaw = Get-Content $ProfilePath -Raw
if ($profileRaw -notmatch [regex]::Escape($tunnelId)) {
    throw "Profile tunnel_id does not match state.json ($tunnelId). Refusing to start a different tunnel."
}
if ($profileRaw -notmatch '127\.0\.0\.1:5265/mcp') {
    throw "Profile MCP URL is not http://127.0.0.1:5265/mcp. Run Cutover-ExistingTunnelTo5265.ps1 first."
}

if (-not $SkipDoctor) {
    Write-Host "=== doctor --explain ==="
    & $TunnelClient doctor --profile $Profile --explain
    if ($LASTEXITCODE -ne 0) { throw "doctor failed ($LASTEXITCODE)" }
}

Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" | ForEach-Object {
    Write-Host "Stopping tunnel-client pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

Write-Host "Starting single tunnel-client health=$HealthListenAddr profile=$Profile"
Write-Host "UI http://$HealthListenAddr/ui  readyz http://$HealthListenAddr/readyz"
# Launch via a short-lived PowerShell child so CONTROL_PLANE_* env is inherited without
# putting the key on the process command line.
$launcher = @"
`$env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY','Process')
`$env:CONTROL_PLANE_TUNNEL_ID = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_TUNNEL_ID','Process')
& '$TunnelClient' run --profile $Profile --health.listen-addr $HealthListenAddr --log.level=info --harpoon.allow-plaintext-http --mcp.oauth-trusted-origin http://127.0.0.1:5265 --mcp.oauth-trusted-origin http://localhost:5265 *>> '$OutLog' 2>> '$ErrLog'
"@
$launcherPath = Join-Path $LogDir "start-once.ps1"
Set-Content -Path $launcherPath -Value $launcher -Encoding UTF8
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", $launcherPath
) -PassThru -WindowStyle Hidden
if (-not $proc) { throw "Failed to start tunnel-client launcher" }
Write-Host "STARTED_LAUNCHER_PID=$($proc.Id)"

$deadline = (Get-Date).AddSeconds(45)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri ("http://{0}/readyz" -f $HealthListenAddr) -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) {
    Write-Host "READYZ_FAIL — see $ErrLog"
    if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 40 }
    throw "Tunnel health /readyz did not become ready"
}
Write-Host "READYZ_OK"
try {
    $h = Invoke-RestMethod ("http://{0}/health?details=true" -f $HealthListenAddr) -TimeoutSec 5
    Write-Host ("HEALTH ready={0} live={1}" -f $h.ready, $h.live)
}
catch {
    Write-Host "HEALTH_DETAILS_UNAVAILABLE=$($_.Exception.Message)"
}
