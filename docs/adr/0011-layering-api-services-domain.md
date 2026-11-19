# 0011. Layering: api (thin) → services → domain (pure), auto-registered routers

- Status: Accepted
- Date: 2026-11-19
- Deciders: Lattice (architect); Keel

## Context
Phase 1 grew `app/api` and `app/services` without a written rule for what goes where. Phase 2 adds
Plaid ingest, the first money writes, and several parallel lanes (roundtable 01, E1.1). Without a
fixed layering, logic drifts into routers, money writes appear in more than one place, and the pure
calculations (categorization, transfers, metrics, forecasts) become hard to test without a database.
`CLAUDE.md` Style already says "routers stay thin, logic goes in `app/domain`" but nothing enforces
it. Separately, every new router meant a hand edit to `main.py`, a hot file for merge collisions
between lanes (roundtable 01, Risks).

## Decision
Three layers, imported only downward:

1. **`app.api`: thin HTTP edge.** Parse and validate (Pydantic schemas), resolve dependencies
   (principal, context), call one service, shape the response. Routers never import `app.models`,
   `app.db` or `app.db_context`: they never open a session, read or write. No exceptions; the audit
   page (`audit.household_page`) and cookie resolution (`sessions.resolve_token`) are service calls.
2. **`app.services`: the unit of work.** A service opens the scoped session (RLS context), loads
   ORM models, calls domain logic, persists, and writes the audit row, all in one transaction.
   Auth, sessions, audit and **every money write** (ingest upserts, transaction edits, transfers,
   budgets) live here. One service owns each write path; for ingest that is `IngestService` (E2.1).
3. **`app.domain`: pure logic.** Plain values in, plain values out (`Decimal`, dataclasses,
   Pydantic schemas). No I/O: no sessions, ORM models, Redis, HTTP, filesystem, clock or env reads,
   and no FastAPI. Testable without Postgres. The rule is restated in `app/domain/__init__.py`.

Infrastructure modules (`app.db`, `app.db_context`, `app.models`, `app.security`, `app.schemas`,
`app.integrations`, `app.worker`) sit beside the layers; the contracts below say who may use them.

**Enforcement:** `import-linter` (dev dependency, `[tool.importlinter]` in `backend/pyproject.toml`),
run by `make lint-imports` and as part of `make lint`. Contracts:
- *layers*: `app.api` → `app.services` → `app.domain` (no upward imports).
- *forbidden*: `app.domain` may not import `app.api`, `app.services`, `app.db`, `app.db_context`,
  `app.models`, `app.integrations`, `app.worker`, `app.config`, `fastapi`, `starlette`,
  `sqlalchemy`, `psycopg`, `redis`, `arq` or `httpx`.
- *forbidden* (direct imports): `app.api` may not import `app.models`, `app.db` or `app.db_context`.

**Router auto-registration:** `create_app` discovers every `app/api/routes_*.py` module (sorted by
name) and mounts its `router: APIRouter` under `/api`. A module without one fails at startup. Only
modules backed by a real `.py` source file are imported; sourceless `.pyc` and native extensions in
the package directory are ignored. Adding a router never touches `main.py`.

The e2e hooks gate (`APP_ENV=test` AND `E2E_HOOKS=true`) is enforced twice, failing closed:
- modules named `routes_test*` are skipped **before import** unless both flags are set, and must
  declare `TEST_ONLY = True` (startup fails otherwise);
- any route module declaring `TEST_ONLY = True` that would mount without both flags makes startup
  fail, so a misnamed test router can never be served.

## Alternatives considered
- Convention only (no linter): drifts the first time a lane is in a hurry.
- Hexagonal ports/adapters with repository interfaces: more indirection than a single-tenant-per-
  request app needs; services using SQLAlchemy directly is enough.
- Entry-point based router plugins: overkill until third-party plugins exist (E2.2 may revisit).
- Explicit router list in a separate `api/__init__.py`: still a shared hot file.

## Consequences
- Domain code is unit-testable without services; money math lives in one reviewed place.
- Routers that need data must go through a service, which adds a small function per endpoint.
- Route order in OpenAPI is now alphabetical by module; paths don't overlap, so behaviour is unchanged.
- Follow-ups: Harbor wires `make lint-imports` into CI (`.github/` is Harbor's lane). Later epics may
  add contracts for `app.agent` (read-only `v_*` views, hard rule 5) and `app.tax` isolation
  (hard rule 14). `app/services/` gained an `__init__.py` so the linter can treat it as a package.
