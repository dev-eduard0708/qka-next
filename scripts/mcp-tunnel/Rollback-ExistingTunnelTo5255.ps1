#Requires -Version 5.1
<#
.SYNOPSIS
  Restore the existing Secure MCP Tunnel profile to legacy http://127.0.0.1:5255/mcp.
#>
param(
    [string]$BackupDir = "",
    [string]$Profile = "qka-mcp-local",
    [string]$HealthListenAddr = "127.0.0.1:8080"
)

$ErrorActionPreference = "Stop"
$LegacyBin = "C:\QehcAI\App\scripts\mcp-tunnel\bin\tunnel-client.exe"
$ProfilePath = Join-Path $env:APPDATA "tunnel-client\$Profile.yaml"
$StatePath = Join-Path $env:LOCALAPPDATA "QkaMcpTunnel\state.json"

if (-not $BackupDir) {
    $BackupDir = Get-ChildItem (Join-Path $env:LOCALAPPDATA "QkaMcpTunnel\backups") -Directory |
        Sort-Object Name -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $BackupDir -or -not (Test-Path $BackupDir)) {
    throw "No backup directory found under LocalAppData\QkaMcpTunnel\backups"
}

$srcProfile = Join-Path $BackupDir "ROLLBACK-qka-mcp-local.yaml"
if (-not (Test-Path $srcProfile)) { $srcProfile = Join-Path $BackupDir "qka-mcp-local.yaml" }
$srcState = Join-Path $BackupDir "state.json"
Copy-Item $srcProfile $ProfilePath -Force
if (Test-Path $srcState) { Copy-Item $srcState $StatePath -Force }
Write-Host "Restored profile from $BackupDir"

if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "Set CONTROL_PLANE_API_KEY before rollback restart."
}

Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

& $LegacyBin doctor --profile $Profile --explain
& $LegacyBin run `
    --profile $Profile `
    --health.listen-addr $HealthListenAddr `
    --log.level=info `
    --harpoon.allow-plaintext-http `
    --mcp.oauth-trusted-origin "http://127.0.0.1:5255" `
    --mcp.oauth-trusted-origin "http://localhost:5255"
