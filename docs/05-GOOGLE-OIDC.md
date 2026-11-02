# QKA-Next Google Workspace OpenID Connect

This is independent of the existing .NET MCP OAuth on port 5255. Do not reuse ChatGPT plugin client IDs or change the tunnel.

QKA-Next does **not** merge Google accounts onto existing AD users by email. Super Admin must first add the `@qehc.edu.sa` address to the allowlist. An allowlisted Google `sub` may then sign in; a new QKA user is created for that `sub` if it is not already linked. AD source ACL is resolved from that user's Active Directory identity only. The shared MCP pilot SID is never applied to other employees.

Live Google login is **not verified** until an administrator completes the Cloud Console steps below and a real `@qehc.edu.sa` sign-in succeeds.

## Google Cloud Console

1. Open [Google Cloud Console](https://console.cloud.google.com/) in a project that owns the `qehc.edu.sa` Workspace.
2. APIs & Services → Credentials → Create credentials → OAuth client ID.
3. Application type: **Web application**.
4. Name: `QKA-Next` (do not reuse the ChatGPT MCP client).
5. Authorized JavaScript origins:
   - `http://127.0.0.1:5186`
   - `http://127.0.0.1:5265`
6. Authorized redirect URIs (must match `QKA_GOOGLE_REDIRECT_URI` exactly). The QKA-Next Web DEV client currently registers:
   - `http://127.0.0.1:5265/auth/google/callback` (FastAPI, used for live DEV)
   Also add when using the Vite proxy as the OAuth redirect:
   - `http://127.0.0.1:5186/api/v1/auth/google/callback`
   Production: HTTPS origin + `/api/v1/auth/google/callback` when UI and API share one origin.
7. Copy the client ID. Copy the client secret into the server `.env` only. Never put either value in the Quasar app or git.
8. OAuth consent screen: Internal (Workspace). Scopes: `openid`, `email`, `profile`.
9. Optionally restrict the OAuth client to the `qehc.edu.sa` organization. QKA-Next still validates `hd`, `email_verified`, and exact `@qehc.edu.sa`.

## QKA-Next `.env` (server only)

```
QKA_ENVIRONMENT=development
QKA_DEV_SESSION_ENABLED=false
QKA_COOKIE_SECURE=false
QKA_FRONTEND_ORIGIN=http://127.0.0.1:5186
QKA_GOOGLE_CLIENT_ID=
QKA_GOOGLE_CLIENT_SECRET=
QKA_GOOGLE_REDIRECT_URI=http://127.0.0.1:5186/api/v1/auth/google/callback
QKA_GOOGLE_HOSTED_DOMAIN=qehc.edu.sa
```

Set `QKA_ENVIRONMENT=production` and `QKA_COOKIE_SECURE=true` on HTTPS. DEV email login cannot be enabled in production.

## Administrator identity link

On the **DEV copy** (never the original production database):

1. Sign in as Super Admin with the local username/password form.
2. Add the user's `@qehc.edu.sa` address on Access Management. That is the only Google login grant.
3. The user signs in with Google Workspace. QKA-Next binds the Google `sub`. It does not attach that `sub` to another person because the email strings match.
4. Document text still requires that user's own Active Directory identity in the Synology ACL snapshot. Missing AD mapping is a deny.

## What this does not do

- It does not change `.NET` MCP OAuth, ChatGPT Business, or the production tunnel.
- It does not claim a live Google login until credentials exist and a real callback is tested.
- It does not grant the MCP pilot SID `S-1-5-21-4108582196-371218740-2140778505-3104` to every employee.
