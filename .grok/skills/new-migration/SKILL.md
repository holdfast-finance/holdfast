---
name: new-migration
description: >
  Create a Holdfast Alembic migration safely (models, RLS, indexes, downgrade,
  up/down test). Use whenever a model in backend/app/models changes or a table,
  view, index, or RLS policy is added. Trigger phrases: "new migration",
  "add a table", /new-migration.
---

# New migration

Only one run holds the migration token. If another run holds it, stop.

1. Change the SQLAlchemy models first. Typed `Mapped[]`. Money is `Numeric(14, 2)`. Household data has `household_id`.
2. `cd backend && uv run alembic revision --autogenerate -m "<imperative summary>"`.
3. Hand-edit the generated file. Autogenerate misses these, so add them yourself:
   - `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and the household policy on new household tables
   - per-person visibility policies for account-scoped data (PLAN 20.3)
   - `v_*` views, materialized views, trigram indexes, and enum changes
   - a `downgrade()` that reverses every step
4. Test up, down one, up again against the dev database as `holdfast_owner`, then `uv run pytest -q`.
5. If you added a `v_*` view the advisor may read, add it to the whitelist in `app/agent/tools.py` with a docstring.
6. Commit the model change and the migration together: `feat(db): <summary>`. Ledger and Bulwark review the diff. Keel merges.
