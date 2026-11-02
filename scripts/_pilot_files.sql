SELECT count(*) AS under_prefix
FROM synology_file_inventory
WHERE "RelativePathNormalized" LIKE 'EDUARDTESTCLOUD%';

SELECT "FileName", "RelativePathNormalized", "IsPresent"
FROM synology_file_inventory
WHERE "FileName" ILIKE '%Assets%'
LIMIT 20;
