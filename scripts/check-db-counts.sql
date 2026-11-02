SELECT current_database() AS db,
       (SELECT count(*) FROM synology_file_inventory) AS inventory_all,
       (SELECT count(*) FROM synology_file_inventory WHERE "IsPresent") AS inventory_present,
       (SELECT count(*) FROM synology_document_content) AS contents;
