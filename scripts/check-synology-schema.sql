SELECT current_database();
SELECT count(*) AS connections FROM synology_connection_settings;
SELECT "Id", "ConnectionName", "HostName", "ConfiguredSharedFolderPath", "PrimaryApprovedShareId", "ConnectionStatus"
FROM synology_connection_settings;
SELECT count(*) AS shares FROM synology_approved_share WHERE "IsActive";
SELECT "Id", "ShareName", "FolderRelativePath", "IsActive" FROM synology_approved_share ORDER BY "ShareName";
SELECT
  (SELECT count(*) FROM external_identity WHERE "Provider"=1 AND "LinkStatus"=2) AS google_linked,
  (SELECT count(*) FROM allowed_email WHERE "Enabled") AS allowlist_enabled,
  (SELECT count(*) FROM google_workspace_account) AS google_accounts;
