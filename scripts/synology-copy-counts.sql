SELECT current_database();
SELECT "FileTypeCategory", count(*) AS n
FROM synology_file_inventory
WHERE "IsPresent" = TRUE
GROUP BY 1
ORDER BY 1;
SELECT count(*) AS inventory_present FROM synology_file_inventory WHERE "IsPresent" = TRUE;
SELECT count(*) AS inventory_all FROM synology_file_inventory;
SELECT "Status", count(*) AS n FROM synology_document_content GROUP BY 1 ORDER BY 1;
SELECT "HostName", "ConfiguredSharedFolderPath", "AuthMode", "ConnectionStatus",
       ("ServiceAccountSecretProtected" IS NOT NULL) AS has_secret,
       "ServiceAccountUserName"
FROM synology_connection_settings;
SELECT count(*) AS connection_name_col
FROM information_schema.columns
WHERE table_name = 'synology_connection_settings' AND column_name = 'ConnectionName';
