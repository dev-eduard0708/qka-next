# QKA-Next

Sibling rebuild of QEC Knowledge AI using Vue 3 + Quasar + FastAPI. It does **not** replace `C:\QehcAI\App`.

## URLs

- UI: http://127.0.0.1:5186
- API: http://127.0.0.1:5265 (`/health`, `/docs`)

## Start (Windows)

Python used for this machine: `C:\QehcAI\Temp\python312\tools\python.exe`

```powershell
cd C:\QehcAI\QKA-Next
copy .env.example .env
# set QKA_SESSION_SECRET to a long random value
.\scripts\start-api.ps1
.\scripts\start-frontend.ps1
```

Sign in with `eduard@qehc.edu.sa` (DEV copy allowlist). Search `Laptop`.

## Tests

```powershell
cd C:\QehcAI\QKA-Next\backend
C:\QehcAI\Temp\python312\tools\python.exe -m pytest
cd C:\QehcAI\QKA-Next\frontend
npm test
```
