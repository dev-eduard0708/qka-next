# One-PC enrollment + disposable read-only test

Do **not** install remotely / via GPO. Do **not** index real employee profile folders yet.

## Enrollment (install → register → assign)

1. Super Admin → **Employee Computers → Agent devices** → **Issue device registration code** (no employee).
2. On the pilot PC (after MSI), set the one-time code **without logging it**:
   - Preferred: write `%ProgramData%\QKA\DesktopAgent\agent.config.json`:

```json
{
  "enrollmentCode": "<paste-once>",
  "verifyTls": true
}
```

   - Or machine env `QKA_AGENT_ENROLLMENT_CODE` for one boot only.
3. Start/restart service `QkaDesktopAgent`.
4. Confirm UI: **Connected · Employee: Unassigned · Indexing: Disabled**.
5. Delete `enrollmentCode` from JSON if still present (agent clears it after successful enroll).
6. Super Admin **Assign** your AD-linked employee.
7. Create a **disposable** folder e.g. `C:\QKA-Pilot-Docs` with a few sample PDF/TXT files.
8. Grant **NETWORK SERVICE** (or the agent account) **Read & execute / List folder** only — no Write/Modify/Delete.
9. Browse/approve **only** that disposable folder → **Start** indexing.
10. Confirm read-only verified, counts in UI, restart service, incremental change.

## Heartbeat check

After enroll, UI **Last heartbeat** should update ~every poll interval. Server policy returns `command=pause` while unassigned.

## Negative cases

- Wrong enrollment code → enroll fails; no device key written.
- Untrusted TLS → agent must fail closed (`verifyTls=true`); fix trust, do not disable verification.
