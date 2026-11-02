$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "C:\QehcAI\Temp\python312\tools\python.exe"
$env:PYTHONPATH = Join-Path $root "backend\src"
Remove-Item Env:QKA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:QKA_DATABASE_URL -ErrorAction SilentlyContinue
# Prefer .env for OpenAI; drop shell leftovers from prior test sessions.
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:QKA_OPENAI_API_KEY -ErrorAction SilentlyContinue
Set-Location $root
if (-not (Test-Path (Join-Path $root ".env"))) {
  Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
}
& $py (Join-Path $root "backend\run_api.py")
