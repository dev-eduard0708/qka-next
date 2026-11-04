# AD Workstation Management

| Field | Value |
| --- | --- |
| UI | Sources → Employee Computers → **Workstations** |
| API | `/api/v1/admin/workstations` (Super Admin) |
| Source | Existing Windows Integrated / LDAPS AD connection |

## Behaviour

- Discovers `objectClass=computer` via LDAP; excludes servers/DCs by default (`operatingSystem` contains Server or UAC server-trust).
- Sync upserts PostgreSQL inventory; missing computers are marked `is_present=false` (history kept).
- DNS + ICMP probes run only for selected inventory IDs (no unrestricted scans).
- No ICMP reply → `no_reply` — never treated as powered off.
- Employee assignment is explicit; never inferred from computer name.
- Agent column: **Not Registered / Unknown** until a Desktop Agent device is linked; Deploy Agent stays disabled.
