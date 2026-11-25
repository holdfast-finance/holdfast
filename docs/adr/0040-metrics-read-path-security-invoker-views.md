# 0040. Metrics read through security-invoker views, not materialized views

- Status: Accepted (Keel, 2026-11-24)
- Date: 2026-11-24
- Deciders: Lattice (architect); Keel

## Context
PLAN 5 lists `budget_period_actual` as a materialized view, and ROADMAP B2 asks for "metrics behind
`v_*` views + matview refresh job". Two constraints from earlier decisions make a materialized view
the wrong tool here:

- **RLS can't attach to a materialized view.** A matview stores rows computed at refresh time and
  has no policies. Every reader would see every row the refresh saw, so per-person visibility
  (PLAN 20.3, ADR 0008: `private`, `shared`, `summary_only`) would have to be rebuilt by hand in a
  wrapper, outside the one place it lives today (the base-table policies).
- **A refresh can't see more than one household.** Migrations and refreshes run as
  `holdfast_owner`, which FORCE RLS binds (ADR 0007, 0008). With no `app.household_id` set, every
  base table returns zero rows, so a global `REFRESH MATERIALIZED VIEW` would store nothing. Making
  it work needs a BYPASSRLS role, which ADR 0007 rules out.

The Ledger memo (roundtable 04, Risks) already flagged refresh cost on every sync and suggested a
plain view with a spike at 100k rows. Data volume is small: one household, about 50k live rows over
five years.

## Decision
1. **Every Phase 5 `v_*` view is a plain view created `WITH (security_invoker = true)`.** The
   reader's own RLS applies to every base table the view reads, so visibility stays defined once,
   in the base-table policies. `summary_only` falls out of ADR 0008 unchanged: such a reader sees
   the account and its balance anchors, but none of its transactions.
2. **One view holds the money rules.** `v_ledger_lines` is the only place that applies
   `COALESCE(split, parent)`, drops superseded rows, drops transfers (`is_transfer` and the
   `transfer`/`excluded` kinds, ADR 0022 rule 1) and classifies each line into `income`,
   `spending` or `saving` (ADR 0022 rules 2–4). Every other metric reads it, or reads the balance
   views, and never `transaction` directly. The one exception is the forecast's liquid-cash loader
   (ADR 0041), which needs transfer legs.
3. **Balances come from anchors plus roll-forward.** `v_balance_anchors` unions
   `balance_snapshot.current` and the closing balance of reconciled or accepted statements
   (`import_run`). The snapshot wins on the same day. The balance on day *d* is the nearest anchor at
   or before *d* (or the earliest after it), plus the live posted transactions between the anchor
   and *d*. Statement-only accounts get a balance history without writing new snapshot rows.
4. **Grants are SELECT only.** Default privileges give `holdfast_app` write grants on new
   relations, and Postgres can update simple views, so each migration revokes INSERT, UPDATE,
   DELETE and TRUNCATE on every view. A view lint asserts that every `v_*` view has
   `security_invoker=true`, grants only SELECT to the app and appears in the agent whitelist.
5. **No refresh job for views.** Budget status, cash flow and net worth are computed when read, so
   a committed sync or a recategorisation shows up on the next request. "A new transaction updates
   the budget within one sync cycle" holds as soon as the sync commits.
6. **A fallback is specified but not built.** If the B2b benchmark (100k synthetic rows) misses its
   budget, the fallback is a per-household rollup **table** (`ledger_daily_rollup`, with RLS like a
   detail table), refreshed by an F2 job inside the household context. It is never a materialized
   view.

## Alternatives considered
- **A materialized view with a definer wrapper that filters by `app_household_id()` and
  `app_account_details_visible()`:** this duplicates the visibility rules outside the policies, and
  the refresh still sees zero rows without BYPASSRLS.
- **A BYPASSRLS refresh role:** this breaks ADR 0007's role split, for a performance problem we
  don't have.
- **Definer views (the Postgres default):** the base tables would be read as the owner. That works
  only because the GUCs are per session, and it hides the trust boundary. Rejected for invoker
  semantics.
- **Per-person summary tables written by a job:** the numbers would be stale between runs, and the
  storage would add RLS surface.

## Consequences
- PLAN 5's `budget_period_actual` matview isn't built. Budget actuals come from `v_ledger_lines`
  through the budget service. `v_budget_status` for the agent is deferred to Phase 8, because
  rollover lives in `app/domain/budgets.py`.
- Two people see different totals for the same budget or month. That's intended, and the API
  reports `hidden_detail_accounts` so the UI can say why.
- Query cost is paid on every read. B2b carries a recorded benchmark and a bounded-query-count test.
- The views read `current_date` only to bound the daily series, so they follow the session time
  zone (UTC). The Phase 5 contract (OQ-5) settles on UTC for now.
