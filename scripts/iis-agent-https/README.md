# QKA Agent HTTPS (one-PC pilot)

Target: `https://qka-agent.qehc.edu.sa` → IIS SNI site → `http://127.0.0.1:5265/api/v1/agent/desktop/*` only.

## Certificate options (prefer top)

1. **Organization-approved** PFX with SAN `qka-agent.qehc.edu.sa` → `New-QkaAgentHttpsCertificate.ps1 -PfxPath …`
2. **Publicly trusted DNS-01 (Let’s Encrypt)** — public zone `qehc.edu.sa` is on **ns1/ns2.justhost.com**.  
   Public **A NXDOMAIN for `qka-agent` does not block DNS-01**. Create TXT `_acme-challenge.qka-agent.qehc.edu.sa` in the JustHost/Bluehost DNS panel; **do not** publish a public A to the internal host. Keep the service internal-only.
3. **Pilot-only self-signed** (explicit approval) →  
   `New-QkaAgentHttpsCertificate.ps1 -PilotSelfSigned -IApprovePilotSelfSigned`  
   Then trust the exported `.cer` **only** on the approved pilot PC via `Trust-QkaAgentPilotCertOnClient.ps1`.

Never auto-install AD CS. Never disable TLS verification on the agent.

## IIS (authorized administrator, elevated)

Prerequisites on QEC-AI-MCP: **URL Rewrite** + **ARR**. Current host may lack both.

```powershell
# Elevated on QEC-AI-MCP
cd C:\QehcAI\QKA-Next\scripts\iis-agent-https

# A) Org PFX  OR  B) pilot self-signed
.\New-QkaAgentHttpsCertificate.ps1 -PilotSelfSigned -IApprovePilotSelfSigned
$thumb = (Get-Content C:\QehcAI\Temp\qka-agent-https\last-cert.json | ConvertFrom-Json).Thumbprint

.\Install-QkaAgentIisHttps.ps1 -CertificateThumbprint $thumb
```

Preserves existing IIS sites / MCP / OAuth. Site name: `QKA-Agent-HTTPS`.

## After client trust validates

1. Set `QKA_AGENT_PUBLIC_BASE_URL=https://qka-agent.qehc.edu.sa` in server `.env`
2. Restart QKA API (`:5265`)
3. Manual MSI + one-time enrollment on the **one** approved pilot PC (no GPO)

## Do not

- Commit PFX / private keys / enrollment codes
- Broaden reverse-proxy rules beyond `/api/v1/agent/desktop/*` (+ optional `/health`)
- Link the GPO or add computers to `QKA-Agent-Pilot-Computers` until this manual pilot succeeds
