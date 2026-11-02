$ErrorActionPreference = "Stop"
$node = "C:\QehcAI\Temp\nodejs\node-v24.22.1-win-x64"
$env:PATH = "$node;" + $env:PATH
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")
if (-not (Test-Path "node_modules")) {
  npm install
}
npm run dev
