#Requires -Version 5.1
<#
.SYNOPSIS
  Initialize a SEPARATE openai tunnel-client profile for QKA-Next MCP at http://127.0.0.1:5265/mcp.
  Does NOT overwrite the legacy qka-mcp-local profile that targets :5255.
#>
param(
    [string]$TunnelId = $env:QKA_NEXT_CONTROL_PLANE_TUNNEL_ID,
    [string]$McpServerUrl = "http://127.0.0.1:5265/mcp",
    [string]$Profile = "qka-next-mcp-staging",
    [string]$TunnelClient = "tunnel-client"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($TunnelId)) {
    throw "Set QKA_NEXT_CONTROL_PLANE_TUNNEL_ID (a NEW staging tunnel id) or pass -TunnelId. Do not reuse the legacy :5255 tunnel id."
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "Set CONTROL_PLANE_API_KEY to a runtime key with Tunnels Read+Use (do not commit)."
}

$cmd = Get-Command $TunnelClient -ErrorAction SilentlyContinue
if (-not $cmd) {
    $legacy = "C:\QehcAI\App\scripts\mcp-tunnel\bin\tunnel-client.exe"
    if (Test-Path $legacy) {
        $TunnelClient = $legacy
    }
    else {
        throw "tunnel-client not found. Use App\scripts\mcp-tunnel\bin\tunnel-client.exe or PATH."
    }
}

Write-Host "Initializing SEPARATE profile '$Profile' → $McpServerUrl (tunnel $TunnelId)"
Write-Host "Legacy profile qka-mcp-local (:5255) is left untouched."
& $TunnelClient init `
    --sample sample_mcp_remote_no_auth `
    --profile $Profile `
    --tunnel-id $TunnelId `
    --mcp-server-url $McpServerUrl

Write-Host "Running doctor --explain..."
& $TunnelClient doctor --profile $Profile --explain
Write-Host "Init complete. Start with .\Run-QkaNextMcpTunnel.ps1"
