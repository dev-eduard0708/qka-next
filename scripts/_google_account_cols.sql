SELECT column_name, is_nullable
FROM information_schema.columns
WHERE table_name = 'google_workspace_account'
ORDER BY ordinal_position;

SELECT count(*) AS google_identities
FROM external_identity
WHERE "Provider" = 1;
