# 0022. Default category tree, system vs user categories, and the Plaid PFC mapping as data

- Status: Accepted
- Date: 2026-11-21
- Deciders: Keel; Ledger

## Context
Categories exist as a table but nothing seeds them. Budgets, reports and Plaid categorisation all
need a shared vocabulary that households can still reshape. Plaid sends a Personal Finance
Category (PFC): 16 primary and 104 detailed codes in its published taxonomy.

## Decision
1. **Two levels, stable slugs.** `category` gains `slug` (unique per household), `origin`
   (`system` or `user`), `is_hidden`, `counts_toward_spending` and `sort_order`. A group is a row
   with no parent; its slug is `food`; a category is `food.groceries`. The slug never changes when
   a household renames a category, so code and mappings key on it, never on a name or an id.
   `kind` is `income`, `expense`, `transfer`, `savings` or `excluded`.
2. **Seeding keys on the slug and never overwrites.** `INSERT ... ON CONFLICT (household_id, slug)
   DO NOTHING`. It runs at first-run setup, and migration 0008 runs it for every existing
   household. A household's rename, hide, kind change or own category is left alone, and a later
   release can add a default slug and run the same statement again. A deleted default comes back
   on the next seed, so the UI hides instead of deleting. User-added categories use
   `origin='user'` and must not use a slug that starts with a default group.
3. **The PFC mapping is a YAML file in the repo**, `backend/app/domain/plaid_pfc_map.yaml`, next to
   a transcription of Plaid's taxonomy CSV (`plaid_pfc_taxonomy.yaml`) that records the URL, the
   retrieval date and the CSV's SHA-256.
   It is reference data, identical for every household, and it maps to slugs (not row ids), so a
   table would need a household column for nothing. Files review as a diff, ship with the code,
   and a test can compare them to the taxonomy offline. Every primary and detailed code is mapped
   or listed under `unmapped` with a reason; a test fails on a gap, a code in both, or a slug that
   does not exist. A detailed code resolves first; its primary is the fallback for codes Plaid adds
   later.
4. **A user's category is never overwritten by an automated source.** `LedgerWriter.apply_category`
   skips a row whose `category_source` is `user` and returns whether it applied. (The writer class
   is renamed from `LedgerService`; the read service in `services/ledger.py` keeps that name.)

## Consequences
- Updating the taxonomy means regenerating the taxonomy file from the new CSV, updating its hash, and fixing whatever the test
  reports.
- Migration 0008 imports the current default tree. It is idempotent, so that is safe, but an old
  checkout of the migration seeds whatever the tree says today.
- Writes to categories (rename, hide, add) are a later story; this one is read only.
