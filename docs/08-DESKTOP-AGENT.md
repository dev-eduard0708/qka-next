# QKA Windows Desktop Agent (pilot)

| Field | Value |
| --- | --- |
| Scope | One domain-joined Windows 10/11 PC |
| Content policy | Local index only (no central file upload by default) |
| Installer | `scripts/desktop-agent/` (`QKA-Agent.msi` via WiX, or `Install-QkaAgent.ps1`) |

## Server APIs

- Agent: `/api/v1/agent/desktop/enroll|heartbeat|policy|progress|errors`
- Admin: `/api/v1/admin/desktop-devices…`
- Auth: per-device key after machine-identity enrollment (managed deploy) or one-time enrollment code fallback (never embedded in MSI)

## UI

Super Admin → **Employee Computers** (`/sources/employee-computers`)

## Install (pilot)

See **`docs/10-PILOT-PC-INSTALL.md`** for the one-PC procedure.

1. Build payload: `scripts/desktop-agent/build-exe.ps1`
2. Build MSI: `scripts/desktop-agent/build-msi.ps1` (WiX 3.14; local binaries under `C:\QehcAI\Temp\wix314` are supported)
   - Silent: `msiexec /i QKA-Agent.msi /qn QKASERVERURL=https://qka.internal…`
3. Or script install: `Install-QkaAgent.ps1 -ServerBaseUrl https://qka.internal…`
4. Issue enrollment code in QKA UI for an AD-linked employee.
5. Place code on the PC via env `QKA_AGENT_ENROLLMENT_CODE` or `ProgramData\QKA\DesktopAgent\agent.config.json`.
6. Agent enrolls, heartbeats, and indexes **approved folders only** after read-only verification.
7. Built artifact (build host): `agent\dist\QKA-Agent.msi` (version `0.1.0.0`, **unsigned**).

## Security

- Scanner must not run as SYSTEM/admin with write rights to source files.
- NTFS on approved folders: read/list only for the agent identity.
- Local index under `%ProgramData%\QKA\DesktopAgent\index` (outside sources).
- Default excludes: AppData, browsers, Windows, Program Files, credentials stores.
- Code signing: production blocker until an approved certificate is configured.

## Managed deploy (Workstations UI)

1. Super Admin assigns an AD-linked employee to the workstation.
2. Deploy requires: trusted internal HTTPS (`QKA_AGENT_PUBLIC_BASE_URL`), MSI present, RSAT GPMC available to the delegated engine, confirmation phrase `DEPLOY SELECTED`.
3. Jobs persist in Postgres (`requested` → `approved` → `scheduled` → `waiting_for_computer` / `installing` → `installed_agent_registered` | `failed` | `cancelled`).
4. Evidence of install is **agent registration + heartbeat**, never ping or GPO link alone.
5. Machine enrollment tokens are bound to AD computer SID/GUID + assigned employee; hostname/MAC/IP/email alone are rejected.

## HTTPS (current host)

Internal HTTPS for agent traffic is **BLOCKED** until Domain Ops installs an organization-approved server certificate and reverse-proxies `/api/v1/agent/desktop/*` to FastAPI `:5265` without changing the MCP OAuth issuer or existing IIS sites.

## GPO

MSI/WiX source is prepared for AD Software Installation targeting computer group `QKA-Agent-Pilot-Computers` only (never an entire OU). Live GPO apply remains a Domain Ops / delegated RSAT step — QKA does not expose unrestricted PowerShell remoting.
