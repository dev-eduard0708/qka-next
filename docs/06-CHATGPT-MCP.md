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

## Separate Secure MCP Tunnel (do not overwrite legacy)

1. Create a **new** Platform tunnel for staging (do not reuse the :5255 tunnel id).
2. Set `QKA_NEXT_CONTROL_PLANE_TUNNEL_ID` and `CONTROL_PLANE_API_KEY`.
3. From `QKA-Next/scripts/mcp-tunnel`:

```powershell
.\Init-QkaNextMcpTunnel.ps1
.\Run-QkaNextMcpTunnel.ps1
```

4. Confirm `http://127.0.0.1:8081/readyz` (legacy health stays on `:8080`).

## ChatGPT Business admin — staging plugin

1. Confirm QKA-Next API on `:5265` with `QKA_MCP_OAUTH_ENABLED=true` and Google client configured.
2. Start the **staging** tunnel (`qka-next-mcp-staging`).
3. As ChatGPT Business admin: Plugins → Add custom MCP server.
4. Name: `QKA-Next DEV – QEC Knowledge AI`.
5. Connection: Tunnel → select the **staging** tunnel (not the production/pilot :5255 tunnel).
6. Authentication: OAuth (CIMD preferred).
7. Install plugin → new chat → `@QKA-Next DEV – QEC Knowledge AI` → search then fetch.
8. Only after a successful live ChatGPT tool call with matching QKA-Next audit evidence, set `QKA_MCP_CHATGPT_LIVE_VERIFIED_AT` (ISO timestamp). Dashboard must stay **Not verified** until then.

## Optional cutover (requires explicit approval)

Do **not** cut over the existing QKA plugin without written approval. When approved: dual-run staging, freeze legacy writes, switch ChatGPT plugin tunnel target, verify, then retire legacy carefully.

## Dashboard

Super Admin → Dashboard → System & Integrations. **Test MCP connection** exercises metadata/auth/ACL locally and never marks ChatGPT Connected by itself.
