#!/usr/bin/env bash
# Holdfast Postgres roles. Runs once from /docker-entrypoint-initdb.d on an empty data dir
# (compose), and directly in CI against the service container. Safe to re-run.
#
#   POSTGRES_USER (holdfast_admin)  bootstrap superuser. Used by the image entrypoint only.
#   holdfast_owner                  owns the database and schema public. Runs Alembic, and may
#                                   CREATE EXTENSION for trusted extensions (pg_trgm, pgcrypto).
#   holdfast_app                    runtime role for api/worker. No superuser, no BYPASSRLS, no
#                                   DDL: RLS policies apply to it (.claude/rules/migrations.md).
#
# Passwords come from POSTGRES_OWNER_PASSWORD / POSTGRES_APP_PASSWORD and reach psql through
# \getenv, so they never appear in argv or on stdout.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is not set}"
: "${POSTGRES_OWNER_PASSWORD:?POSTGRES_OWNER_PASSWORD is not set (run make keys)}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is not set (run make keys)}"
db="${POSTGRES_DB:-holdfast}"

psql --no-psqlrc -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" -v db="$db" <<'SQL'
\getenv owner_pw POSTGRES_OWNER_PASSWORD
\getenv app_pw POSTGRES_APP_PASSWORD

SELECT 'CREATE ROLE holdfast_owner'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'holdfast_owner') \gexec
SELECT 'CREATE ROLE holdfast_app'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'holdfast_app') \gexec

ALTER ROLE holdfast_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'owner_pw';
ALTER ROLE holdfast_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'app_pw';

ALTER DATABASE :"db" OWNER TO holdfast_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO holdfast_app;

ALTER SCHEMA public OWNER TO holdfast_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO holdfast_app;

ALTER DEFAULT PRIVILEGES FOR ROLE holdfast_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO holdfast_app;
ALTER DEFAULT PRIVILEGES FOR ROLE holdfast_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO holdfast_app;
SQL

echo "holdfast roles ready: holdfast_owner, holdfast_app"
