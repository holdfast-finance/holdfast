# 0008. Row-level security and per-person visibility

- Status: Accepted
- Date: 2026-11-19
- Deciders: project engineer (Phase 1, data-model workstream)

## Context
PLAN 17.4 and 20.3 require tenant isolation and per-person account visibility (`private`, `shared`, `summary_only`) enforced by Postgres, not app code. ADR 0007 makes the runtime role (`holdfast_app`) unable to bypass RLS. The Phase 1 contract fixes the GUC names and the overall policy shape, and leaves two choices to the data model: how child tables get a household (denormalized `household_id` or EXISTS on the parent), and how visibility reaches detail tables.

## Decision
**Context.** `app.db_context` sets three transaction-local GUCs at the start of every transaction a session opens (an `after_begin` hook, so a mid-unit `commit()` keeps the context): `app.household_id`, `app.person_id` and `app.household_wide`. The STABLE SQL helpers `app_household_id()`, `app_person_id()` and `app_household_wide()` read them and return NULL / false when unset, so missing context matches nothing.

**Denormalized `household_id` on every household table, child tables included.** All 27 household tables have `household_id NOT NULL`, an index whose leading column is `household_id`, ENABLE + FORCE RLS, and one `household_isolation` policy (`household_id = app_household_id()`, USING and WITH CHECK). We use no EXISTS-on-parent policies for isolation. Reasons: one uniform policy, cheap index-backed checks, and no policy chains on hot paths. A parent can't disagree with its child: parents expose `UNIQUE (id, household_id)`, and children use a composite FK `(parent_id, household_id) → parent(id, household_id)`. FK checks bypass RLS, so this is also what stops a guessed id from another household being referenced. Optional references use `ON DELETE SET NULL (col)` (Postgres 15+), which clears only the reference and never `household_id`.

| Table | Isolation | Extra policy |
|---|---|---|
| household | `id = app_household_id()` | no INSERT grant. Rows come only from `create_household()` |
| person, household_setting, bank_connection, category, rule, budget, budget_line, credit_report, tradeline, credit_inquiry, credit_score_entry, document, parsed_field, tax_year, tax_item, tax_scenario, estimate_snapshot, agent_conversation, agent_message, agent_finding | denormalized `household_id` | none |
| account | denormalized | RESTRICTIVE `person_visibility`: `visibility IN ('shared','summary_only') OR owner_person_id IS NULL OR owner_person_id = app_person_id() OR app_household_wide()` |
| transaction, holding, investment_txn, liability | denormalized | RESTRICTIVE: EXISTS on `account` where `visibility = 'shared'` OR joint OR mine OR household-wide |
| transaction_split | denormalized | RESTRICTIVE: EXISTS on `transaction` (inherits the transaction's visibility) |
| balance_snapshot | denormalized | RESTRICTIVE: EXISTS on `account` (summary_only viewers see balances) |
| audit_log | `household_id` NULL allowed | SELECT `household_id = app_household_id()`. INSERT `household_id IS NULL OR = app_household_id()`. No UPDATE/DELETE grant |
| app_user, webauthn_credential, auth_session, totp_recovery_code | none (pre-login) | only `app/services/auth*` touches them |
| institution, security | none (shared reference data) | no DELETE grant |

**Visibility uses EXISTS.** Visibility is a property of the account, which can change at any time (`shared` → `private`), so it isn't copied onto detail rows. The EXISTS subqueries on `account` are themselves filtered by account's RLS. Visibility policies are RESTRICTIVE, so they can only narrow household isolation, never widen it. WITH CHECK uses the same predicate: a person can't write details into someone else's private or summary-only account, and can't flip someone else's shared account to private (the new row would vanish from them).

**`app.household_wide`** is an explicit opt-in for background jobs (bank sync, imports) that must write to every account in a household whatever its owner. It widens person visibility only. The household boundary still holds. Request handlers never set it.

**`create_household(name)`** is `SECURITY DEFINER`, owned by `holdfast_owner`, with `search_path = pg_catalog, pg_temp`, fully qualified object names, EXECUTE revoked from PUBLIC and granted to `holdfast_app`. Because `household` has FORCE RLS, which binds the owner too, the function sets `app.household_id` to the new id for its INSERT and then restores the caller's value.

**Grants.** `00-roles.sh` default privileges give `holdfast_app` SELECT/INSERT/UPDATE/DELETE on every owner-created table. Migration 0002 only narrows that: `household` (no INSERT), `audit_log` (SELECT, INSERT), `institution`/`security` (no DELETE). Tests assert the exact privilege set per table.

**A person who owns accounts can't be deleted** (`account.owner_person_id` FK is `NO ACTION`). Otherwise their private accounts would silently turn joint (owner NULL) and become visible to everyone. Household deletes still cascade.

## Alternatives considered
- **EXISTS-on-parent isolation for child tables:** no extra column, but every child query pays a join inside the policy, and chains (split → transaction → account) compound. Rejected for isolation. Kept only for visibility, where the data really does live on the parent.
- **Copying `visibility` / `owner_person_id` onto detail rows:** fast, but every visibility change becomes a mass update, and a missed row leaks. Rejected.
- **Treating "no person" as household-wide:** convenient for jobs, but a request handler that forgets the person would see private accounts. Rejected in favor of the explicit `household_wide` flag.
- **Global uniqueness for `transaction.import_fingerprint` / Plaid ids (PLAN 5):** a unique violation would reveal that another household holds the same row. Scoped to `(account_id, …)` instead. `bank_connection (provider, external_id)` stays global per the contract (Plaid item ids are globally unique anyway).

## Consequences
- Migrations run as `holdfast_owner`, which is also bound by FORCE RLS. Schema-only migrations are unaffected, but a future data migration must set a context (or `household_wide`) per household, or temporarily `NO FORCE` inside the migration.
- INSERT … RETURNING on `audit_log` fails for pre-auth rows (RETURNING must pass the SELECT policy). The `AuditLog` model generates its id client-side and disables eager defaults. Raw SQL must not use RETURNING there.
- Per-person visibility doesn't yet cover `document`, `credit_*` or `agent_*` (they're household-visible). Revisit when those features land (Phases 3, 6 and 8).
- Postgres 15+ is required (`ON DELETE SET NULL (col)`). The stack pins 16.
