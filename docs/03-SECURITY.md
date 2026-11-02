# Security notes

- Default deny: missing ACL, failed allowlist, inactive user, or unverified identity → no document text.
- Session cookie `qka_next_session` is HMAC-signed and HttpOnly. User id is taken from the cookie, never from query/body/headers.
- CSRF: mutating POSTs require `X-CSRF-Token` matching the `qka_next_csrf` cookie (set at session issue).
- Super Admin local login is username/password. The password is stored as PBKDF2 on the QKA-Next copy only (`qka_next_local_login`). Original PostgreSQL is unchanged.
- Super Admin adds `@qehc.edu.sa` emails to `allowed_email`. Only those enabled addresses may sign in with Google Workspace.
- Google Workspace sign-in is authorization-code + PKCE. ID tokens are checked for signature, issuer, audience, expiry, nonce, `email_verified`, and hosted domain `qehc.edu.sa`. Personal Gmail is denied.
- Binding is by Google `sub` only. Email text never merges onto an existing AD user. An allowlisted first login provisions a new QKA user for that `sub`.
- Source ACL uses that user's Active Directory identity (username / SID / UPN). The MCP pilot SID is not applied to other employees.
- DEV `/api/v1/auth/dev-session` is off by default and cannot be enabled when `QKA_ENVIRONMENT=production`.
- OpenAI keys are not used. Google client secrets stay on the server.
- MCP and ChatGPT OAuth parity are **not** claimed. Live Google login is not claimed until Cloud Console credentials exist and a real callback is tested.
- Original Synology files are not streamed. Only already-extracted text in PostgreSQL is returned, after ACL checks.
