---
name: tax-year-config
description: "Create or update a Holdfast tax-year config (backend/app/tax/config/YEAR.yaml) or relief_catalog.yaml entries with sourced values from official IRS/state publications. Use for \"add tax year\", \"update limits for YEAR\", \"new deduction in the catalog\", or Phase 7 config work."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# Tax-year config

1. Start from the previous year's file (or the schema in PLAN.md 9.2) and keep every key. Set every value to `TODO_VERIFY`, so nothing is silently carried forward.
2. For each key, find the **primary** source for that tax year:
   - Brackets, standard deduction, many thresholds: the annual inflation-adjustment Rev. Proc. (irs.gov/irb).
   - Retirement limits (401(k)/403(b)/457(b), IRA, catch-up): the IRS annual COLA notice.
   - HSA limits: the annual Rev. Proc. for HSA/HDHP.
   - Law changes (e.g. OBBBA provisions): IRS guidance pages, notices and the IRC section.
   - State: the state revenue department (e.g. dor.sc.gov) for the state module.
3. Fill each value with a `source:` (document id + URL) and an `as_of:` date. If you can't confirm a value from a primary source, leave `TODO_VERIFY`.
4. Relief catalog entries need: `id`, `title`, `years_effective` (start/end), `eligibility` (data predicates), `needs_data`, `savings_formula` (config keys only), `source`, `confidence`, `risk` (🟢🟡🟠🔴).
5. Run the `tax-config-verifier` agent on the changed files and fix every MISMATCH or UNSOURCED value.
6. Run `cd backend && uv run pytest -q -k tax`. The TODO_VERIFY gate test must pass for the target year, or stay red on purpose with the remaining keys listed in the commit body.
7. Update `docs/tax-sources.md` with the sources used. Commit: `feat(tax): <year> config` (or `fix(tax): …`).
