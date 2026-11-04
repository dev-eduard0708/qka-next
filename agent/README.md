# QKA Desktop Agent (pilot)

Python agent that indexes approved local folders read-only and reports status to QKA-Next.

## Run (dev)

```powershell
$env:PYTHONPATH="C:\QehcAI\QKA-Next\agent;C:\QehcAI\QKA-Next\backend\src"
$env:QKA_AGENT_SERVER_URL="https://qka.internal.example"
$env:QKA_AGENT_ENROLLMENT_CODE="<one-time-code>"
C:\QehcAI\Temp\python312\tools\python.exe -m qka_desktop_agent.main
```

## Package

- `scripts/desktop-agent/build-exe.ps1`
- `scripts/desktop-agent/build-msi.ps1` (requires WiX)
- `scripts/desktop-agent/Install-QkaAgent.ps1` (silent pilot fallback)

See `docs/08-DESKTOP-AGENT.md`.
