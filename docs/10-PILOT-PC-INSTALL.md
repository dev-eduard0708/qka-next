# Pilot PC install (one approved test computer)

**Do not** deploy to employee PCs until this pilot PC enrolls and indexes successfully.
**Deploy Agent** in the Workstations UI stays disabled until a real deployment mechanism exists.

## Artifacts

| Item | Path / value |
| --- | --- |
| MSI | `C:\QehcAI\QKA-Next\agent\dist\QKA-Agent.msi` |
| Version | `0.1.0.0` (agent package `__version__` = `0.1.0`) |
| EXE payload | `agent\dist\qka-desktop-agent\qka-desktop-agent.exe` |
| Signing | **Not signed** — production blocker until an approved cert is used |

## Server URL (required)

Pilot PC must use **reachable internal HTTPS**:

- Hostname: `https://qka-agent.qehc.edu.sa` (internal A → `192.168.120.35`)
- Set server `QKA_AGENT_PUBLIC_BASE_URL` **only after** HTTPS + client trust validate (see `scripts/iis-agent-https/README.md`)
- Do **not** use `http://127.0.0.1:5265` or `localhost` on the pilot PC
- Do **not** bypass TLS verification on the agent

## Silent install (approved test PC only — no GPO required for first pilot)

Prerequisites on the pilot PC: DNS resolves `qka-agent.qehc.edu.sa`, and the pilot CER is trusted in **LocalMachine\Root** (see `docs/11-PILOT-PC-HTTPS-TRUST.md`). Local Service needs machine trust — CurrentUser trust alone is not enough for the agent service.

```bat
msiexec /i "C:\path\QKA-Agent.msi" /qn QKASERVERURL=https://qka-agent.qehc.edu.sa
```

Silent uninstall:

```bat
msiexec /x "C:\path\QKA-Agent.msi" /qn
```

The MSI does **not** embed enrollment codes, employee passwords, or device keys.
`QKASERVERURL` writes `HKLM\Software\QKA\DesktopAgent\ServerBaseUrl` only (read by the agent). Keep `verifyTls=true` (default).

## After install (registration)

Flow: **Install → Register Device → Assign User → Approve Folders → Start Indexing**

1. In QKA: **Sources → Employee Computers → Agent devices**
2. **Issue device registration code** (no employee required).
3. On the pilot PC, place the one-time code in either:
   - Environment: `QKA_AGENT_ENROLLMENT_CODE=<code>` (session/machine env — never logged)
   - Or `%ProgramData%\QKA\DesktopAgent\agent.config.json` → `"enrollmentCode"` (delete after enroll)
4. Restart service `QkaDesktopAgent` (or reboot).
5. Verify in UI: **Connected · Employee: Unassigned · Indexing: Disabled**
6. Super Admin **Assign** an AD-linked employee, then browse/approve a **disposable** pilot folder only.
7. Start indexing only after read-only verification on that disposable folder.

## Approved folders + read-only NTFS

1. Grant the agent scanner identity (**Local Service** / dedicated restricted account) **Read & list only** on the approved folder(s).
2. Do **not** grant Modify/Write/Delete on employee source trees.
3. Do **not** grant blanket rights to all profile directories.
4. In QKA Agent devices UI, set approved folder paths and Start indexing.
5. Confirm **Read-only access verified**. If verification fails, indexing stays blocked.

Negative write tests run only under `%ProgramData%\QKA\DesktopAgent` disposable fixtures — never against employee files.

## Indexing verification

1. Place a few disposable PDF/DOCX/XLSX/TXT/CSV files in the approved folder.
2. Confirm progress counts in the UI (indexed / pending / failed).
3. Modify one file → expect reindex; delete one → expect removal from local index.
4. Restart the service → progress should resume from local SQLite under ProgramData.

## Workstations UI checks (already live on QKA server)

- Sync from AD (Windows Integrated connection)
- DNS + individual / bulk ping on **selected** computers only
- Reachability `no_reply` means ICMP blocked/offline/firewalled — **not** “powered off”
