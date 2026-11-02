# Recreate qka_next_dev from qka_014_frontend_it (read-only dump of original).
$ErrorActionPreference = "Stop"
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
$env:PGPASSWORD = ""
$dump = "C:\QehcAI\Temp\qka_014_frontend_it.dump"
& "$pgBin\pg_dump.exe" -h 127.0.0.1 -p 55432 -U qka_test -Fc -f $dump qka_014_frontend_it
& "$pgBin\psql.exe" -h 127.0.0.1 -p 55432 -U qka_test -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'qka_next_dev' AND pid <> pg_backend_pid();"
& "$pgBin\psql.exe" -h 127.0.0.1 -p 55432 -U qka_test -d postgres -c "DROP DATABASE IF EXISTS qka_next_dev;"
& "$pgBin\psql.exe" -h 127.0.0.1 -p 55432 -U qka_test -d postgres -c "CREATE DATABASE qka_next_dev;"
& "$pgBin\pg_restore.exe" -h 127.0.0.1 -p 55432 -U qka_test -d qka_next_dev --no-owner --no-privileges $dump
& "$pgBin\psql.exe" -h 127.0.0.1 -p 55432 -U qka_test -d qka_next_dev -f (Join-Path $PSScriptRoot "overlay-copy-dev-identity.sql")
& "$pgBin\psql.exe" -h 127.0.0.1 -p 55432 -U qka_test -d qka_next_dev -f (Join-Path $PSScriptRoot "overlay-copy-ad-sid.sql")
