$ErrorActionPreference = "Stop"
$pg = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
$db = "qka_next_dev"
if ($db -in @("qka_014_frontend_it", "qka_ad_sync_test")) {
  throw "Refusing to overlay the original QKA database."
}
& $pg -h 127.0.0.1 -p 55432 -U qka_test -d $db -f "C:\QehcAI\QKA-Next\scripts\overlay-copy-ad-sid.sql"
