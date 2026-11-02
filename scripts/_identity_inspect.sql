SELECT "Provider", "StableExternalId", "DomainUsername", "Upn", "PrimaryEmail", "LinkStatus", "AccountStatus", "QkaUserId"
FROM external_identity;
SELECT g."GoogleUserId", g."PrimaryEmail"
FROM google_workspace_account g;
