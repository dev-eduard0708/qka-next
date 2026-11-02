# PostgreSQL mapping

## Original (do not migrate, do not drop)

- Host `127.0.0.1:55432`
- Database `qka_014_frontend_it` used by the running .NET API
- QKA-Next never connects here in default `.env`

## Verified copy

- `qka_next_dev` — `pg_dump` / `pg_restore` of `qka_014_frontend_it`
- Overlay (copy only): MCP pilot identity `PrimaryEmail = eduard@qehc.edu.sa` so DEV allowlist can match
- Original identity email remains empty

## Test database

- `qka_next_test` — schema-only clone, tests seed and delete their own rows

## EF Core vs SQLAlchemy

| Topic | EF Core | Python |
| --- | --- | --- |
| Table names | `ToTable("qka_user")` | `__tablename__ = "qka_user"` |
| Columns | unquoted property names → `"Id"` | `mapped_column("Id", ...)` |
| FTS | extra `search_vector` column + GIN | raw SQL `plainto_tsquery` |
| Enums | C# enums stored as int | Python ints matching EF |
| Alembic | n/a | blocked from original DB names |

`default_transaction_read_only=on` is set for the QKA-Next API against `qka_next_dev`.
