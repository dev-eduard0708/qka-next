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

Employee / pilot PCs must use a **reachable internal HTTPS** QKA base URL.

- Set server `QKA_AGENT_PUBLIC_BASE_URL` (example: `https://qka.internal.qehc.edu.sa`)
- Do **not** use `http://127.0.0.1:5265` or `localhost` on the pilot PC
- TLS certificate must be trusted by the pilot PC (corporate CA or public CA)

## Silent install (approved test PC only)

```bat
msiexec /i "\\share\path\QKA-Agent.msi" /qn QKASERVERURL=https://qka.internal.qehc.edu.sa
```

Silent uninstall:

```bat
msiexec /x "\\share\path\QKA-Agent.msi" /qn
```

The MSI does **not** embed enrollment codes, employee passwords, or device keys.
`QKASERVERURL` is only the public HTTPS base for the agent API.

## After install (registration)

1. In QKA: **Sources → Employee Computers → Workstations**
2. Confirm the pilot computer appears (from AD sync).
3. Explicitly **Assign** the AD-linked employee (never inferred from computer name).
4. Open **Agent devices** tab → **Issue enrollment code** for that employee.
5. On the pilot PC, place the one-time code in either:
   - Environment: `QKA_AGENT_ENROLLMENT_CODE=<code>`
   - Or `%ProgramData%\QKA\DesktopAgent\agent.config.json` → `"enrollmentCode"`
6. Restart service `QkaDesktopAgent` (or reboot).
7. Verify in UI: last heartbeat, agent version, Connected status.

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
