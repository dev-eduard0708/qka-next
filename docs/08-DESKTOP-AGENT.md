# QKA Windows Desktop Agent (pilot)

| Field | Value |
| --- | --- |
| Scope | One domain-joined Windows 10/11 PC |
| Content policy | Local index only (no central file upload by default) |
| Installer | `scripts/desktop-agent/` (`QKA-Agent.msi` via WiX, or `Install-QkaAgent.ps1`) |

## Server APIs

- Agent: `/api/v1/agent/desktop/enroll|heartbeat|policy|progress|errors`
- Admin: `/api/v1/admin/desktop-devices…`
- Auth: per-device key after one-time enrollment code (never in MSI)

## UI

Super Admin → **Employee Computers** (`/sources/employee-computers`)

## Install (pilot)

1. Build payload: `scripts/desktop-agent/build-exe.ps1`
2. Build MSI when WiX is installed: `scripts/desktop-agent/build-msi.ps1`
   - Silent: `msiexec /i QKA-Agent.msi /qn QKASERVERURL=https://qka.internal…`
3. Or script install: `Install-QkaAgent.ps1 -ServerBaseUrl https://qka.internal…`
4. Issue enrollment code in QKA UI for an AD-linked employee.
5. Place code on the PC via env `QKA_AGENT_ENROLLMENT_CODE` or `ProgramData\QKA\DesktopAgent\agent.config.json`.
6. Agent enrolls, heartbeats, and indexes **approved folders only** after read-only verification.

## Security

- Scanner must not run as SYSTEM/admin with write rights to source files.
- NTFS on approved folders: read/list only for the agent identity.
- Local index under `%ProgramData%\QKA\DesktopAgent\index` (outside sources).
- Default excludes: AppData, browsers, Windows, Program Files, credentials stores.
- Code signing: production blocker until an approved certificate is configured.

## GPO

MSI/WiX source is prepared for AD Software Installation. Pushing GPO from the QKA UI is a **separate** integration — the UI reports honest `installer_ready` status and does not fake deployment success.
