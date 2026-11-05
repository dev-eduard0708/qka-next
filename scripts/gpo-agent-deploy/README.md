# Remote QKA Agent deployment (pilot-scoped GPO)

QKA never uses Domain Admin credentials in-app and never deploys to the whole domain.

## Architecture

1. **MSI UNC share** (`New-QkaAgentMsiShare.ps1`) — Domain Computers can read the MSI only.
2. **GPO** `{73E8998A-A3B3-435F-9219-419C331BA2D4}` linked to a **pilot OU**, filtered to **`QKA-Agent-Pilot-Computers` only** (`Configure-QkaAgentGpo.ps1` on a GPMC host).
3. **GPO installs MSI** with `QKASERVERURL` only — **no enrollment secrets in SYSVOL/MSI/GPO**.
4. **Per-device token** issued by QKA Deploy → delivered by `Provision-MachineEnrollmentToken.ps1` into **HKLM** on that PC only.
5. Agent enrolls with SID + HMAC `machineProof` → heartbeat proves install.

## Ordered admin actions

### A. File server (QEC-AI-MCP, elevated)

```powershell
cd C:\QehcAI\QKA-Next\scripts\gpo-agent-deploy
.\New-QkaAgentMsiShare.ps1 -IConfirmCreateShare
# Note MSI_UNC=\\QEC-AI-MCP\QkaAgent\QKA-Agent.msi
```

Set in `C:\QehcAI\QKA-Next\.env` then restart API:

```text
QKA_AGENT_MSI_UNC=\\QEC-AI-MCP\QkaAgent\QKA-Agent.msi
QKA_AGENT_TOKEN_PROVISIONING=hkml_out_of_band
```

### B. AD management host with RSAT GPMC (elevated)

```powershell
.\Configure-QkaAgentGpo.ps1 `
  -PilotOuDn 'OU=...,DC=qehc,DC=edu,DC=sa' `
  -MsiUnc '\\QEC-AI-MCP\QkaAgent\QKA-Agent.msi' `
  -IConfirmGpoScope
```

### C. Your approved pilot PC (explicit)

```powershell
.\Add-QkaPilotComputer.ps1 -ComputerName 'YOUR-PC-NAME' -IConfirmAddPilotComputer
```

Trust the pilot HTTPS CER on that PC (`docs/11-PILOT-PC-HTTPS-TRUST.md`), then `gpupdate /force`.

### D. QKA UI Deploy (Super Admin)

1. Select **only** your pilot workstation.
2. Confirm phrase `DEPLOY SELECTED`.
3. Copy the one-time `machineEnrollmentToken` from the response (shown once).
4. On the pilot PC (admin):

```powershell
.\Provision-MachineEnrollmentToken.ps1 -Token '<paste-once>' -IConfirmProvisionToThisComputer
Restart-Service QkaDesktopAgent
```

5. Verify Agent Connected in QKA (employee may remain Unassigned).

## Forbidden

- Authenticated Users / Domain Computers Apply on the GPO (use pilot group only)
- Embedding tokens in MSI / GPO Registry Preferences / SYSVOL scripts
- Linking GPO at domain root
- Disabling TLS verification
