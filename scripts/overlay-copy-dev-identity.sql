-- COPY DATABASE ONLY (qka_next_dev). Never run against qka_014_frontend_it.
-- Attaches the existing allowlisted address to the existing MCP pilot identity
-- so DEV search can demonstrate allowlist + ACL with current inventory.
UPDATE external_identity
SET "PrimaryEmail" = 'eduard@qehc.edu.sa',
    "UpdatedAt" = NOW()
WHERE "Id" = 'aaaaaaaa-2222-4222-8222-222222222222'
  AND ("PrimaryEmail" IS NULL OR btrim("PrimaryEmail") = '');
