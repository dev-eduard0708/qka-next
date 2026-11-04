# QKA-Next ChatGPT Business MCP (staging)

| Field | Value |
| --- | --- |
| Document ID | QKA-NEXT-DOC-06 |
| Scope | QKA-Next FastAPI `:5265/mcp` only |
| Legacy | `.NET :5255` and plugin **unchanged** |

## Endpoint

- MCP Streamable HTTP: `http://127.0.0.1:5265/mcp`
- PRMD: `GET /.well-known/oauth-protected-resource`
- AS: `GET /.well-known/oauth-authorization-server`
- Authorize: `GET /oauth/authorize` → Google Workspace
- Token: `POST /oauth/token`
- DCR: `POST /oauth/register`
- Google callback (MCP): `GET /oauth/google/callback`

Tools: `search`, `fetch`, `get_profile` (read-only, per-user ACL, default-deny).

## Auth rules

- Google Workspace `@qehc.edu.sa` only, verified email, enabled allowlist, linked QKA identity.
- Tokens bind to that employee's QkaUser id.
- Google-only users **never** inherit the legacy pilot BoundUserId / shared Synology ACL.
- Synology document ACL needles come from **AD-linked** identities on that same QkaUser. A Google-only user who is allowlisted can sign in / get_profile, but **search/fetch stay default-deny** until an administrator links their AD identity to the same QkaUser (never merge by email alone).
- Inspector static bearer is DEV-only and must map to **one** individual `QKA_MCP_INSPECTOR_USER_ID`.

## Google Cloud Console (required for MCP OAuth)

Add authorized redirect URI (in addition to the Quasar web callback):

`http://127.0.0.1:5265/oauth/google/callback`

Remote ChatGPT users cannot complete loopback authorize from another PC. For multi-user activation use a public **HTTPS** `QKA_MCP_PUBLIC_BASE_URL` and matching Google redirect URI.

## Existing Secure MCP Tunnel (cutover)

Reuse the Platform tunnel `tunnel_6ae301cc…` / profile `qka-mcp-local` (health `:8080`). Do **not** start a second client against the same tunnel.

```powershell
# Restore / keep alive the SAME tunnel (preferred day-to-day):
.\Start-ExistingTunnelTo5265.ps1
.\Register-TunnelAutostart.ps1
# After backup (see docs/07-MCP-TUNNEL-CUTOVER.md) if retargeting:
.\Cutover-ExistingTunnelTo5265.ps1
# Rollback:
.\Rollback-ExistingTunnelTo5255.ps1
```

Confirm `http://127.0.0.1:8080/readyz` with MCP target `http://127.0.0.1:5265/mcp`.
ChatGPT error `Tunnel-client has not been seen for 300 seconds` means the local `tunnel-client` daemon is down — start it with `Start-ExistingTunnelTo5265.ps1` (do not create a new Platform tunnel).

## ChatGPT Business admin — existing plugin

1. Confirm QKA-Next API on `:5265` with `QKA_MCP_OAUTH_ENABLED=true` and Google redirect `http://127.0.0.1:5265/oauth/google/callback`.
2. Keep the existing ChatGPT plugin (**QKA Synology Pilot**) bound to tunnel **QKA-MCP-Pilot**.
3. Because OAuth issuer moved `:5255` → `:5265`, **reconnect OAuth** on the plugin (refresh tools if prompted). Do not recreate the plugin unless reconnect fails.
4. Live Verified appears only after a ChatGPT-originated MCP audit row (`client=chatgpt%`). Local inspector tests never set Live Verified.

## Dashboard

Super Admin → Dashboard → System & Integrations. **Test MCP connection** exercises metadata/auth/ACL locally and never marks ChatGPT Connected by itself.
