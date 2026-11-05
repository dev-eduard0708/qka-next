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
- Agent column: **Not Registered / Unknown** until a Desktop Agent device is linked.
- Deploy Agent: enabled only when trusted HTTPS + MSI + delegated GPO engine prerequisites pass; requires assigned AD employee + computer SID; confirmation phrase; selected computers only (never entire OU).
- Deployment jobs and machine enrollment tokens persist in PostgreSQL; registration/heartbeat prove install.
- Separate statuses: AD account, network reachability, deploy job, agent connectivity, indexing.
