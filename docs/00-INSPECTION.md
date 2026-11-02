# Existing QKA inspection (read-only)

Inspected 2026-11-01 against `C:\QehcAI\App`. QKA-Next does not modify that tree.

## Runtime (verified)

| Process | Path / port |
| --- | --- |
| React Admin | `C:\QehcAI\App\web` Vite admin config, `127.0.0.1:5185` |
| React sample | Vite default `127.0.0.1:5173` |
| ASP.NET Core API | `Qec.Knowledge.Api`, `http://127.0.0.1:5255` |
| Isolated PostgreSQL | `127.0.0.1:55432`, databases include `qka_014_frontend_it` (running API) |
| Host PostgreSQL | `127.0.0.1:5432` |

## Data the new stack can reuse (read)

Tables in `qka_014_frontend_it` (31 public tables). Document path:

- `synology_approved_share`, `synology_file_inventory`, `synology_document_content` (+ `search_vector` tsvector)
- `qka_user`, `external_identity`, `google_workspace_account`, `allowed_email`
- `access_policy`, `department*`, `ad_security_group`, `ad_group_membership`

Observed counts at inspection: 5340 inventory rows, 3 extracted contents, 1 allowlist (`eduard@qehc.edu.sa`), 2 users.

EF Core maps **snake_case tables** and **PascalCase quoted columns** (`"Id"`, `"ExtractedText"`). SQLAlchemy models match that convention. Alembic is configured never to run against `qka_014_frontend_it`.

## Must be reimplemented in Python (not copied from .NET)

Authorization (allowlist, Synology ACL snapshot, AccessDecision), session auth, search/fetch HTTP, workers, Quasar UI.

## Must stay on .NET until a later task

MCP Streamable HTTP, OAuth 2.1 / Google OIDC, Synology SMB inventory, AD sync, Admin CRUD, ChatGPT Business plugin.
