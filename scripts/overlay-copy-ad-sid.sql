-- Copy-only: map the qkai AD identity to the Synology ACL SID it actually owns.
-- Never run against qka_014_frontend_it or production.
UPDATE external_identity
SET "StableExternalId" = 'S-1-5-21-4108582196-371218740-2140778505-3104',
    "UpdatedAt" = NOW()
WHERE "Provider" = 0
  AND "DomainUsername" = 'qkai'
  AND "QkaUserId" = 'aaaaaaaa-1111-4111-8111-111111111111';
