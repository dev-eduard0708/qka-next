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

## Folder discovery (Agent Devices UI)

Connected agents fulfill Super Admin browse requests over outbound HTTPS polling:

1. Admin selects employee → registered device → **Browse Folders**.
2. Server queues `pendingFolderBrowse` on policy; agent resolves the employee profile via AD SID → ProfileList (not `C:\Users\<username>`).
3. Known folders (Desktop / Documents / Downloads) are resolved on the agent; tree expands under that profile only.
4. Directory names only — no file contents. Junction/symlink escapes and system paths are rejected.
5. Super Admin saves approved folders; indexing still requires separate read-only verification (no ACL auto-grants, no LocalSystem write).

**Access design:** Prefer a restricted domain service account with explicit read/list NTFS on target profiles. LocalService/LocalSystem often cannot read employee Documents — do not “fix” by broadening ACLs automatically.

## Managed deploy (Workstations UI)

1. Super Admin assigns an AD-linked employee to the workstation.
2. Deploy requires: trusted internal HTTPS (`QKA_AGENT_PUBLIC_BASE_URL`), MSI present, RSAT GPMC available to the delegated engine, confirmation phrase `DEPLOY SELECTED`.
3. Jobs persist in Postgres (`requested` → `approved` → `scheduled` → `waiting_for_computer` / `installing` → `installed_agent_registered` | `failed` | `cancelled`).
4. Evidence of install is **agent registration + heartbeat**, never ping or GPO link alone.
5. Machine enrollment tokens are bound to AD computer SID/GUID + assigned employee; hostname/MAC/IP/email alone are rejected.

## HTTPS / DNS / GPO readiness (ops)

| Item | Status (QEC-AI-MCP) |
| --- | --- |
| DNS `qka-agent.qehc.edu.sa` | A → `192.168.120.35` (created) |
| Trusted TLS for that host | **BLOCKED** — no cert in `LocalMachine\My` / WebHosting; no domain CA listed |
| `QKA_AGENT_PUBLIC_BASE_URL` | Leave unset until HTTPS validates |
| AD group `QKA-Agent-Pilot-Computers` | Exists (`OU=Service Users`); **0 members** |
| GPO `QKA-Desktop-Agent-Deployment.` | Exists (GUID `{73E8998A-…}`); **not linked**; SYSVOL empty (version 0) |
| GPMC / `Get-GPO` on this host | **Missing** — install RSAT GPMC elevated |

### Certificate issuance (required before HTTPS)

1. Issue an organization-approved certificate with SAN `qka-agent.qehc.edu.sa` (internal PKI / approved CA — do not use a self-signed production cert).
2. Import into `LocalMachine\My` with private key.
3. Create a **dedicated** IIS site/binding for `qka-agent.qehc.edu.sa:443` only — do not replace MCP/OAuth bindings.
4. Reverse-proxy `/api/v1/agent/desktop/*` → `http://127.0.0.1:5265`.
5. Validate `https://qka-agent.qehc.edu.sa` from a domain workstation with normal trust (no TLS bypass).
6. Only then set `QKA_AGENT_PUBLIC_BASE_URL=https://qka-agent.qehc.edu.sa`.

### GPO / pilot scope (required before deploy)

1. Install GPMC on the management host (elevated).
2. Rename GPO if needed (current display name ends with a trailing `.`).
3. Link GPO to a **narrow** OU; security-filter to `QKA-Agent-Pilot-Computers` only (remove Authenticated Users).
4. Add **one** approved pilot computer account to the group.
5. Publish silent MSI + computer-targeted HKLM `machineEnrollmentToken` (never embed in MSI; never log plaintext).
6. `machineProof` HMAC is **not** Kerberos machine auth — keep Deploy blocked until per-computer token provisioning is real.

## GPO

MSI/WiX source is prepared for AD Software Installation targeting computer group `QKA-Agent-Pilot-Computers` only (never an entire OU). Live GPO apply remains a Domain Ops / delegated RSAT step — QKA does not expose unrestricted PowerShell remoting.
