$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "C:\QehcAI\Temp\python312\tools\python.exe"
$env:PYTHONPATH = Join-Path $root "backend\src"
& $py -m qka_workers.main
