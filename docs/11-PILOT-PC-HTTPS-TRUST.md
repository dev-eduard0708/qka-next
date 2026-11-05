# Pilot PC: trust QKA Agent HTTPS certificate

Server: `https://qka-agent.qehc.edu.sa` (internal A → `192.168.120.35`)  
Public CER (no private key): `C:\QehcAI\Temp\qka-agent-https\qka-agent.qehc.edu.sa.cer`  
Thumbprint: `D030168213D1F589D17FC170A70A4B7340A65BA8` (confirm against `last-cert.json`)

This is a **pilot-only self-signed** certificate. Trust it **only on your approved pilot PC** — not via domain GPO / all workstations.

## On YOUR approved pilot PC (elevated PowerShell)

1. Copy the CER to the pilot PC (USB / secure share), e.g. `C:\Temp\qka-agent.qehc.edu.sa.cer`.

2. Import into Local Machine Trusted Root (pilot PC only):

```powershell
cd C:\QehcAI\QKA-Next\scripts\iis-agent-https   # if repo present
# OR copy Trust-QkaAgentPilotCertOnClient.ps1 + CER together

.\Trust-QkaAgentPilotCertOnClient.ps1 `
  -CerPath 'C:\Temp\qka-agent.qehc.edu.sa.cer' `
  -IConfirmPilotPcOnly
```

Equivalent one-liner:

```powershell
Import-Certificate -FilePath 'C:\Temp\qka-agent.qehc.edu.sa.cer' -CertStoreLocation Cert:\LocalMachine\Root
```

3. Validate **without** TLS bypass:

```powershell
Invoke-WebRequest https://qka-agent.qehc.edu.sa/health -UseBasicParsing
# Expect HTTP 200 and body containing "ok"
```

4. Only after step 3 succeeds on the pilot PC (and on the QKA server if used for admin checks), an admin may set:

```text
QKA_AGENT_PUBLIC_BASE_URL=https://qka-agent.qehc.edu.sa
```

in `C:\QehcAI\QKA-Next\.env` and restart the API.

## Do not

- Use `-SkipCertificateCheck` / `QKA_AGENT_VERIFY_TLS=false` for the pilot
- Distribute the CER domain-wide
- Commit the CER/PFX to git
- Trust this cert on unrelated machines
