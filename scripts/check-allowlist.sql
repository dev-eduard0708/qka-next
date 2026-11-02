SELECT current_database() AS db;
SELECT "Email", "Enabled", "CreatedAt", "UpdatedAt"
FROM allowed_email
ORDER BY "EmailNormalized";
SELECT count(*) AS allowlist_count FROM allowed_email;
