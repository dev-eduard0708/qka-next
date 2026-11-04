# QKA MCP tunnel cutover (:5255 → :5265)

| Field | Value |
| --- | --- |
| Tunnel id | `tunnel_6ae301cc1e4481919c627fc8ef5d9d64` (reused) |
| Profile | `qka-mcp-local` |
| Previous target | `http://127.0.0.1:5255/mcp` |
| New target | `http://127.0.0.1:5265/mcp` |
| Health | `http://127.0.0.1:8080/readyz` |
| Backup | `%LOCALAPPDATA%\QkaMcpTunnel\backups\pre-cutover-5265-*` |

## Scripts

- Cutover: `QKA-Next/scripts/mcp-tunnel/Cutover-ExistingTunnelTo5265.ps1`
- Rollback: `QKA-Next/scripts/mcp-tunnel/Rollback-ExistingTunnelTo5255.ps1`
- Start (same tunnel): `QKA-Next/scripts/mcp-tunnel/Start-ExistingTunnelTo5265.ps1`
- Autostart + 5‑min watchdog: `QKA-Next/scripts/mcp-tunnel/Register-TunnelAutostart.ps1`

`Start-ExistingTunnelTo5265.ps1` reuses profile `qka-mcp-local` / tunnel `tunnel_6ae301cc…` only. Runtime key comes from process env, DPAPI vault (`%LOCALAPPDATA%\QkaMcpTunnel\control-plane-api-key.dpapi`), or `.env` `CONTROL_PLANE_API_KEY` / `OPENAI_API_KEY` — never committed.

## Rollback

1. Stop all `tunnel-client.exe`.
2. Run `Rollback-ExistingTunnelTo5255.ps1` with `CONTROL_PLANE_API_KEY` set.
3. Confirm `/readyz` and ChatGPT against legacy `:5255`.

## ChatGPT plugin

The Platform tunnel **QKA-MCP-Pilot** is unchanged. Local MCP target changed only.

OAuth issuer/resource moved from `http://127.0.0.1:5255` to `http://127.0.0.1:5265`, so ChatGPT must **re-authorize OAuth** (and refresh tools if prompted). Do not recreate the plugin/tunnel unless reconnect fails.

Google Cloud must include redirect URI:

`http://127.0.0.1:5265/oauth/google/callback`

## Live Verified

Dashboard Live Verified is set only from MCP audit rows with `client=chatgpt%`. Local inspector tests do not count.
