SELECT column_name, is_nullable, data_type
FROM information_schema.columns
WHERE table_name = 'external_identity'
ORDER BY ordinal_position;
