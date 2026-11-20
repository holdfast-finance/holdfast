---
name: tax-year-config
description: >
  Create or update a Holdfast tax-year config (backend/app/tax/config/<year>.yaml)
  or relief_catalog.yaml entries with sourced values from official IRS or state
  publications. Use for "add tax year", "update limits for YEAR", "new deduction
  in the catalog", or Phase 7 config work. Trigger: /tax-year-config.
---

# Tax-year config

Read `.claude/rules/tax.md` first. Never hardcode a bracket, limit, or rate in Python.

1. Start from the previous year's file, or the schema in PLAN.md 9.2, and keep every key. Set every value to `TODO_VERIFY` so nothing is copied forward silently.
2. For each key, find the primary source for that tax year:
   - brackets, standard deduction, and many thresholds: the annual inflation-adjustment Rev. Proc. on irs.gov
   - retirement limits: the IRS annual COLA notice
   - HSA limits: the annual Rev. Proc. for HSA and HDHP
   - law changes: IRS guidance, notices, and the IRC section
   - state: that state's revenue department
3. Fill each value with `source:` (document id and URL) and `as_of:`. If you cannot confirm a value from a primary source, leave `TODO_VERIFY`.
4. Relief catalog entries need `id`, `title`, `years_effective`, `eligibility`, `needs_data`, `savings_formula` (config keys only), `source`, `confidence`, and `risk` (standard, documentation-heavy, scrutinized, or won't-recommend). Evasion is declined.
5. Spawn `tax-config-verifier` on the changed files and fix every MISMATCH or UNSOURCED value.
6. Run `cd backend && uv run pytest -q -k tax`. The `TODO_VERIFY` gate must pass for the target year, or stay red on purpose with the remaining keys listed in the commit body.
7. Update `docs/tax-sources.md`. Commit: `feat(tax): <year> config` or `fix(tax): …`. Tally does not merge.
