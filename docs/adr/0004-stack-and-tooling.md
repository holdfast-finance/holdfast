# 0004. Stack and tooling

- Status: Accepted
- Date: 2026-11-18
- Deciders: project engineer

## Context
PLAN.md Sections 2 and 11.1 specify the stack. A few tooling choices were left open.

## Decision
Adopt the PLAN stack as written (FastAPI / SQLAlchemy 2 / Alembic / Pydantic v2 / Postgres 16 / Redis + arq / React + Vite + TS strict + Tailwind v4 + shadcn/ui / Docker Compose / Caddy / Tailscale), with these specifics:
- **Python 3.12 via uv** (`uv` manages the interpreter; the host's 3.14 is not used).
- **pnpm** for the frontend and site (lockfile committed), not npm.
- **Tests against real Postgres** (compose service or testcontainers). SQLite is not allowed, because RLS and NUMERIC semantics matter.
- **Git hooks** via `core.hooksPath=.githooks` (gitleaks), so the `pre-commit` framework isn't required. A `.pre-commit-config.yaml` may be added later for CI parity.
- **Monorepo:** `backend/`, `web/`, `site/` (Phase 9+) and `scripts/` in one repo.

## Consequences
Contributors need uv, pnpm, Docker and gitleaks. The Phase 0 README lists them.
