#Requires -Version 5.1
<#
.SYNOPSIS
  Run tunnel-client for QKA-Next staging MCP (:5265). Uses health port 8081 so legacy :8080 stays free.
#>
param(
    [string]$Profile = "qka-next-mcp-staging",
    [string]$TunnelClient = "tunnel-client",
    [string]$HealthListenAddr = "127.0.0.1:8081"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    $secure = Read-Host -Prompt "CONTROL_PLANE_API_KEY (hidden; not printed/committed)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $secure.Dispose() }
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "CONTROL_PLANE_API_KEY (runtime key) is required. Do not commit secrets."
}
if ([string]::IsNullOrWhiteSpace($env:QKA_NEXT_CONTROL_PLANE_TUNNEL_ID) -and [string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_TUNNEL_ID)) {
    Write-Warning "Set QKA_NEXT_CONTROL_PLANE_TUNNEL_ID for the staging tunnel (do not overwrite the legacy tunnel)."
}

$cmd = Get-Command $TunnelClient -ErrorAction SilentlyContinue
if (-not $cmd) {
    $legacy = "C:\QehcAI\App\scripts\mcp-tunnel\bin\tunnel-client.exe"
    if (Test-Path $legacy) { $TunnelClient = $legacy }
    else { throw "tunnel-client not found." }
}

if (-not [string]::IsNullOrWhiteSpace($env:QKA_NEXT_CONTROL_PLANE_TUNNEL_ID)) {
    $env:CONTROL_PLANE_TUNNEL_ID = $env:QKA_NEXT_CONTROL_PLANE_TUNNEL_ID
}

Write-Host "Starting QKA-Next staging tunnel profile=$Profile health=$HealthListenAddr"
Write-Host "Target MCP: http://127.0.0.1:5265/mcp (legacy :5255 untouched)"
& $TunnelClient run `
    --profile $Profile `
    --health.listen-addr $HealthListenAddr `
    --log.level=info `
    --harpoon.allow-plaintext-http `
    --mcp.oauth-trusted-origin "http://127.0.0.1:5265" `
    --mcp.oauth-trusted-origin "http://localhost:5265"
