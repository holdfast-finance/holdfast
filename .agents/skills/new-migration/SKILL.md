---
name: new-migration
description: "Create a Holdfast Alembic migration safely (models + RLS + indexes + downgrade + up/down test). Use whenever a model in backend/app/models changes or a table, view, index or RLS policy is added."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# New migration

Confirm Keel assigned the migration token before editing schema files.

1. Change the SQLAlchemy models first (typed `Mapped[]`, money = `Numeric(14, 2)`, `household_id` on household data).
2. `cd backend && uv run alembic revision --autogenerate -m "<imperative summary>"`.
3. Hand-edit the generated file. Autogenerate misses these, so add them yourself:
   - `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + the household policy for new household tables,
   - per-person visibility policies for `account`-scoped data (PLAN 20.3),
   - `v_*` views / materialized views (`op.execute`), trigram indexes (`pg_trgm`), enum changes,
   - a correct `downgrade()` that reverses everything.
4. Test: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` against a disposable PostgreSQL database as `holdfast_owner`. Then `uv run pytest -q`.
5. If you added a new `v_*` view the agent should read, add it to the whitelist in `app/agent/tools.py` with a docstring.
6. Commit the model change and its migration together: `feat(db): <summary>`.
