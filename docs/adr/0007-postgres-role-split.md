# 0007. Postgres role split (admin / owner / app)

- Status: Accepted
- Date: 2026-11-19
- Deciders: project engineer (from the Phase 0 security review, finding H1)

## Context
The initial scaffold connected the app as the Postgres bootstrap superuser. Superusers bypass row-level security, so Phase 1's RLS policies (ADR 0006, PLAN 20.3) would have been silently ignored. Any missing `household_id` filter would then leak data across households and people, and tests running as the same role couldn't catch it.

## Decision
Three roles, created by `infra/postgres/init/00-roles.sh` on first boot (the same script bootstraps CI):
- `holdfast_admin`: the bootstrap superuser, used only by the Postgres entrypoint. The app never sees its password.
- `holdfast_owner`: owns the database and schema `public`, and runs Alembic through the one-off `migrate` service (`MIGRATIONS_DATABASE_URL`). Not a superuser. May create trusted extensions.
- `holdfast_app`: the runtime role for api and worker (`DATABASE_URL`). NOSUPERUSER, NOBYPASSRLS, no DDL. DML on owner-created tables through default privileges.

`backend/tests/test_db_roles.py` asserts the runtime role isn't a superuser and can't bypass RLS or create objects. It runs, rather than skips, in CI.

## Alternatives considered
- Single owner role with `FORCE ROW LEVEL SECURITY`: the owner still has DDL and can disable RLS, so the blast radius is larger.
- Docker secrets for the role passwords: better, deferred to Phase 9 (the db container's env currently holds all three).

## Consequences
New `.env` keys (`POSTGRES_OWNER_PASSWORD`, `POSTGRES_APP_PASSWORD`, `MIGRATIONS_DATABASE_URL`). Existing dev volumes must be recreated, because the init script runs only on an empty data dir. Phase 1 RLS policies are enforced for real from the first table.
