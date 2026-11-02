SELECT "DomainUsername", "PrimaryEmail", "LinkStatus"
FROM external_identity
ORDER BY "DomainUsername" NULLS LAST;

SELECT "DisplayName", "IsSuperAdmin", "LoginEnabled"
FROM qka_user
ORDER BY "DisplayName";
