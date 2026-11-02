SELECT column_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_name IN ('synology_approved_share','synology_file_inventory','external_identity','qka_user','allowed_email','synology_document_content')
  AND is_nullable = 'NO'
ORDER BY table_name, ordinal_position;
