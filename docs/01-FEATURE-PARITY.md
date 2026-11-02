# Feature parity checklist

| Area | Legacy | QKA-Next foundation | Reuse DB? |
| --- | --- | --- | --- |
| Health/readiness | yes | yes | n/a |
| Document FTS search | MCP + service | FastAPI `/api/v1/search` | yes, copy |
| Document fetch (extracted text) | MCP fetch | paginated `/api/v1/documents/{id}` | yes, copy |
| Synology inventory metadata | Admin + MCP | read-only list | yes, copy |
| Allowlist enforcement | .NET | Python port | yes |
| Source ACL default-deny | .NET | Python port | yes |
| Access policies | .NET | Python port (grants/restricts) | yes |
| Super Admin console | React 5185 | not ported | n/a |
| Allowed email CRUD | React `#access/emails` | UI placeholder | later |
| AD / Synology connection mgmt | React | placeholder | later |
| Identity match reviews | .NET | pending | later |
| Google OIDC / QKA OAuth | .NET 5255 | **not claimed** | later |
| MCP search/fetch/get_profile | .NET `/mcp` | **not claimed** | later |
| ChatGPT Business plugin | tunnel + OAuth | unchanged on legacy | n/a |
| OpenAI streaming chat | none | UI shell only | later |
| Excel/Word/PDF/PPT extraction | .NET worker-ish extract | worker process stub | later |
| Arabic/RTL | React i18n | architecture + keys | n/a |
| Light/dark | React | Quasar Dark | n/a |
