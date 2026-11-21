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
   `kind` is `income`, `expense`, `transfer`, `savings` or `excluded`. `counts_toward_spending` is
   true only for `expense`, enforced by a CHECK: an expense that should not count is hidden or
   given kind `excluded`, so the flag never needs to disagree with the kind.
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
4. **Source precedence is user > rule > plaid > ml.** `LedgerWriter.apply_category` never lets a
   lower source overwrite a higher one (it returns `False` and changes nothing); an equal or higher
   one applies. A user's choice, including clearing a category (`set_category(None)` keeps the
   source `user`), holds against all automation until `release_category` (audited) hands the row
   back by clearing the source. (The writer class
   is renamed from `LedgerService`; the read service in `services/ledger.py` keeps that name.)

## Consequences
- Updating the taxonomy means regenerating the taxonomy file from the new CSV, updating its hash, and fixing whatever the test
  reports.
- Migration 0008 carries a frozen snapshot of the tree and its seed SQL and imports no application
  code. `ensure_default_categories` uses the live YAML. A test requires the live tree to stay a
  superset of the snapshot (same parent, kind and spending flag per slug), so changing or removing
  a released slug needs a new migration.
- Downgrading 0008 deletes system categories: transactions and splits lose those assignments (and
  transactions without a category lose their `category_source`, which resets a user's explicit "no
  category" too). It refuses to run while a `budget_line` uses a system category, because that FK
  cascades.
- Writes to categories (rename, hide, add) are a later story; this one is read only.

## Deliberate PFC defaults, and what they cost
- `TRANSFER_IN_DEPOSIT` maps to `transfers.deposit`: a deposited paycheck is not income until the
  household recategorises it, so income looks low until then.
- `TRANSFER_OUT_WITHDRAWAL` maps to `transfers.cash-withdrawal`: cash spending is hidden from
  spending totals until it is categorised.
- `TRANSFER_OUT_SAVINGS` and `TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS` map to `savings`-kind
  categories while the matching inflows map to `transfers.account`, so the two legs of a move
  between the household's own accounts land in different kinds.
- `GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT` maps to `taxes.other` because Plaid's description covers
  income and property tax alike.
These are defaults a user's own choice always overrides.

## Rules for metrics
1. Exclude transfers by `kind IN ('transfer', 'excluded')` as well as `transaction.is_transfer`;
   either one alone misses rows.
2. `uncategorized.needs-review` has kind `expense` but is not assumed to be spending. Classify its
   rows by amount sign: an outflow is unreviewed spending, an inflow is unreviewed income.
3. A `savings`-kind outflow is counted once, as saving, and its inflow leg (a `transfer`-kind
   category) is excluded by rule 1, so the money is not counted twice.
4. Count spending with `counts_toward_spending`, which is true only for `expense`.
