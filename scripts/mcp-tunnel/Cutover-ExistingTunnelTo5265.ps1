#Requires -Version 5.1
<#
.SYNOPSIS
  Retarget the EXISTING qka-mcp-local Secure MCP Tunnel from :5255/mcp to :5265/mcp.
  Reuses the same tunnel id / profile / runtime key. Does not create a new Platform tunnel.
  Stops any competing tunnel-client before starting one client on :8080.
#>
param(
    [string]$Profile = "qka-mcp-local",
    [string]$NewMcpServerUrl = "http://127.0.0.1:5265/mcp",
    [string]$HealthListenAddr = "127.0.0.1:8080",
    [string]$TunnelClient = ""
)

$ErrorActionPreference = "Stop"
$LegacyBin = "C:\QehcAI\App\scripts\mcp-tunnel\bin\tunnel-client.exe"
$ProfilePath = Join-Path $env:APPDATA "tunnel-client\$Profile.yaml"
$StatePath = Join-Path $env:LOCALAPPDATA "QkaMcpTunnel\state.json"

function Resolve-TunnelClient([string]$Preferred) {
    if ($Preferred -and (Test-Path $Preferred)) { return (Resolve-Path $Preferred).Path }
    if (Test-Path $LegacyBin) { return (Resolve-Path $LegacyBin).Path }
    $cmd = Get-Command "tunnel-client" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "tunnel-client.exe not found."
}

function Get-ProcessEnvValue([int]$ProcessId, [string]$Name) {
    $code = @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class EnvReader2 {
  [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool b, int c);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr p, int c, ref PBI bi, int s, out int r);
  [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, int n, out IntPtr r);
  [StructLayout(LayoutKind.Sequential)] public struct PBI { public IntPtr a,b,c,d,e,f; }
  public static string Get(int pid, string key) {
    IntPtr h = OpenProcess(0x0010|0x0400, false, pid);
    if (h == IntPtr.Zero) return null;
    try {
      PBI pbi = new PBI(); int ret;
      if (NtQueryInformationProcess(h, 0, ref pbi, Marshal.SizeOf(pbi), out ret) != 0) return null;
      byte[] buf = new byte[8]; IntPtr read;
      if (!ReadProcessMemory(h, pbi.b + 0x20, buf, 8, out read)) return null;
      IntPtr procParams = (IntPtr)BitConverter.ToInt64(buf, 0);
      if (!ReadProcessMemory(h, procParams + 0x80, buf, 8, out read)) return null;
      IntPtr envPtr = (IntPtr)BitConverter.ToInt64(buf, 0);
      byte[] env = new byte[131072];
      if (!ReadProcessMemory(h, envPtr, env, env.Length, out read)) return null;
      string s = Encoding.Unicode.GetString(env);
      foreach (string part in s.Split(new char[]{'\0'}, StringSplitOptions.RemoveEmptyEntries)) {
        int i = part.IndexOf('=');
        if (i > 0 && part.Substring(0,i) == key) return part.Substring(i+1);
      }
      return "";
    } finally { CloseHandle(h); }
  }
}
"@
    if (-not ("EnvReader2" -as [type])) { Add-Type -TypeDefinition $code }
    return [EnvReader2]::Get($ProcessId, $Name)
}

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

if (-not (Test-Path $ProfilePath)) { throw "Missing profile $ProfilePath" }
if (-not (Test-Path $StatePath)) { throw "Missing state $StatePath" }

$state = Get-Content $StatePath -Raw | ConvertFrom-Json
$tunnelId = [string]$state.TunnelId
if ([string]::IsNullOrWhiteSpace($tunnelId)) { throw "state.json missing TunnelId" }

Assert-McpReady -Url $NewMcpServerUrl
$prmd = Invoke-RestMethod "http://127.0.0.1:5265/.well-known/oauth-protected-resource"
$as = Invoke-RestMethod "http://127.0.0.1:5265/.well-known/oauth-authorization-server"
Write-Host "PRMD_RESOURCE=$($prmd.resource)"
Write-Host "AS_ISSUER=$($as.issuer)"

$TunnelClient = Resolve-TunnelClient -Preferred $TunnelClient

# Capture runtime key from existing tunnel-client if present
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    $running = Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" | Select-Object -First 1
    if ($running) {
        $extracted = Get-ProcessEnvValue -ProcessId ([int]$running.ProcessId) -Name "CONTROL_PLANE_API_KEY"
        if ($extracted) {
            $env:CONTROL_PLANE_API_KEY = $extracted
            Write-Host "CONTROL_PLANE_API_KEY captured from running tunnel-client (length=$($extracted.Length); value not printed)"
        }
    }
}
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    throw "CONTROL_PLANE_API_KEY not available. Set it in this process env, then re-run."
}
$env:CONTROL_PLANE_TUNNEL_ID = $tunnelId

$newProfile = @"
config_version: 1
control_plane:
  base_url: "https://api.openai.com"
  tunnel_id: "$tunnelId"
  api_key: "env:CONTROL_PLANE_API_KEY"
health:
  listen_addr: "$HealthListenAddr"
admin_ui:
  open_browser: false
log:
  level: info
  format: json
mcp:
  server_urls:
    - channel: main
      url: "$NewMcpServerUrl"
  oauth_trusted_origins:
    - "http://127.0.0.1:5265"
    - "http://localhost:5265"
harpoon:
  allow_plaintext_http: true
  hosts_include_loopback: true
  hosts_include_private: true
  targets:
    - label: qka-oauth-as
      url: "http://127.0.0.1:5265"
      description: "QKA-Next MCP OAuth authorization server (cutover from :5255)"
"@
Set-Content -Path $ProfilePath -Value $newProfile -Encoding UTF8
Write-Host "PROFILE_UPDATED=$ProfilePath → $NewMcpServerUrl"

@{
    TunnelId = $tunnelId
    Profile = $Profile
    McpServerUrl = $NewMcpServerUrl
    PreviousMcpServerUrl = "http://127.0.0.1:5255/mcp"
    UpdatedAt = (Get-Date).ToString("o")
    Cutover = "5255-to-5265"
} | ConvertTo-Json | Set-Content -Path $StatePath -Encoding UTF8

Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" | ForEach-Object {
    Write-Host "Stopping tunnel-client pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

Write-Host "=== doctor --explain ==="
& $TunnelClient doctor --profile $Profile --explain
if ($LASTEXITCODE -ne 0) { throw "doctor failed ($LASTEXITCODE)" }

Write-Host "Starting single tunnel-client health=$HealthListenAddr"
Write-Host "UI http://$HealthListenAddr/ui  readyz http://$HealthListenAddr/readyz"
& $TunnelClient run `
    --profile $Profile `
    --health.listen-addr $HealthListenAddr `
    --log.level=info `
    --harpoon.allow-plaintext-http `
    --mcp.oauth-trusted-origin "http://127.0.0.1:5265" `
    --mcp.oauth-trusted-origin "http://localhost:5265"
