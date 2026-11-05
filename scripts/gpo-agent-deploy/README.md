# Remote QKA Agent deployment (pilot-scoped, automatic enrollment)

## Security model

| Channel | Allowed | Forbidden |
| --- | --- | --- |
| MSI UNC | MSI binary + `QKASERVERURL` | Enrollment tokens |
| GPO / SYSVOL | Startup install script, optional `TokenDropUnc` path (non-secret) | Tokens, shared secrets |
| Token-drop UNC | Per-SID `*.tok` with **that computer$ only** Read+Delete | World-readable tokens |
| PowerShell history / logs | — | Plaintext tokens |

**Preferred enrollment:** `QKA_AGENT_TOKEN_PROVISIONING=unc_acl_drop`  
Deploy stages the one-time token to the drop share; the agent (as the computer account) claims+deletes it. No employee paste.

**Fallback:** `hkml_out_of_band` + `Provision-MachineEnrollmentToken.ps1 -TokenSecure` (Local Service can clear ProgramData/HKLM).

## Admin order (nothing runs until you confirm)

1. **MSI share** (elevated): `New-QkaAgentMsiShare.ps1 -IConfirmCreateShare`
2. **Token-drop share** (elevated): `New-QkaAgentTokenDropShare.ps1 -IConfirmCreateShare`
3. **.env** (API host) then restart API:
   ```text
   QKA_AGENT_MSI_UNC=\\QEC-AI-MCP\QkaAgent\QKA-Agent.msi
   QKA_AGENT_TOKEN_DROP_UNC=\\QEC-AI-MCP\QkaAgentTokens
   QKA_AGENT_TOKEN_PROVISIONING=unc_acl_drop
   ```
4. **GPMC host** (only when approved): `Configure-QkaAgentGpo.ps1 -PilotOuDn '...' -MsiUnc '...' -IConfirmGpoScope`  
   Registers Machine Startup via `scripts.ini` **and** Scripts CSE + GPT.INI version bump (not scripts.ini alone).
5. **One pilot PC** (only when approved): `Add-QkaPilotComputer.ps1 -ComputerName 'PC' -IConfirmAddPilotComputer`
6. Trust HTTPS CER on that PC; `gpupdate /force`
7. QKA UI → select that PC → `DEPLOY SELECTED` → tokens auto-stage → agent enrolls + heartbeat

Until approval: leave GPO **unlinked** and pilot group **empty**. Use `Test-QkaAgentDeployReadiness.ps1` for read-only checks.

## GPO scope

`Configure-QkaAgentGpo.ps1` refuses domain-root links and fails if `Authenticated Users` / `Domain Computers` / `Everyone` retain **GpoApply**. Only `QKA-Agent-Pilot-Computers` may Apply.
