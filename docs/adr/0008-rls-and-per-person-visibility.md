# 0008. Row-level security and per-person visibility

- Status: Accepted (amended 2026-11-19 after the Phase 1 security and money review; extended by ADR 0025 for rules and derived ledger rows)
- Date: 2026-11-19
- Deciders: project engineer (Phase 1, data-model workstream)

## Context
PLAN 17.4 and 20.3 require tenant isolation and per-person account visibility (`private`, `shared`, `summary_only`) enforced by Postgres, not app code. ADR 0007 makes the runtime role (`holdfast_app`) unable to bypass RLS. The Phase 1 contract fixes the GUC names and the overall policy shape, and leaves two choices to the data model: how child tables get a household (denormalized `household_id` or EXISTS on the parent), and how visibility reaches detail tables.

## Decision
**Context.** `app.db_context` sets three transaction-local GUCs at the start of every transaction a session opens (an `after_begin` hook, so a mid-unit `commit()` keeps the context): `app.household_id`, `app.person_id` and `app.household_wide`. The STABLE SQL helpers `app_household_id()`, `app_person_id()` and `app_household_wide()` read them and return NULL / false when unset, so missing context matches nothing.

**Denormalized `household_id` on every household table, child tables included.** All 27 household tables have `household_id NOT NULL`, an index whose leading column is `household_id`, ENABLE + FORCE RLS, and one `household_isolation` policy (`household_id = app_household_id()`, USING and WITH CHECK). We use no EXISTS-on-parent policies for isolation. Reasons: one uniform policy, cheap index-backed checks, and no policy chains on hot paths. A parent can't disagree with its child: parents expose `UNIQUE (id, household_id)`, and children use a composite FK `(parent_id, household_id) → parent(id, household_id)`. FK checks bypass RLS, so this is also what stops a guessed id from another household being referenced. Optional references use `ON DELETE SET NULL (col)` (Postgres 15+), which clears only the reference and never `household_id`.

**Reading and writing are separate policies.** The first version used one `FOR ALL` policy with the visibility predicate as both USING and WITH CHECK, and claimed that stopped a person from rewriting someone else's account. It didn't: anyone who could *see* an account (shared or summary_only) could also UPDATE it, so Sam could set `visibility = 'shared'` on Alex's summary_only account, set `owner_person_id` to himself or to NULL (joint), or make it private to himself, and so gain or steal access. Now each per-person table has four RESTRICTIVE policies: `person_read` (FOR SELECT, the visibility predicate) and `person_insert` / `person_update` / `person_delete` (the write predicate). For UPDATE the write predicate is both USING (the existing row) and WITH CHECK (the new row).

| Write predicate ("writable") | Meaning |
|---|---|
| account | `owner_person_id IS NULL OR owner_person_id = app_person_id() OR app_household_wide()` |
| transaction, holding, investment_txn, liability, balance_snapshot | `app_household_wide()` OR EXISTS on `account` where joint or mine |
| transaction_split | `app_household_wide()` OR EXISTS on `transaction` JOIN `account` where joint or mine |

So shared and summary_only accounts, and their details, are **read-only** to everyone but their owner. Joint accounts are writable by everyone in the household. A person may insert only accounts that are theirs or joint. Moving a detail row to another account (`UPDATE transaction SET account_id = …`) needs write access to both, because USING checks the old account and WITH CHECK the new one. Handing an account to another person (`owner_person_id = <someone else>`) fails WITH CHECK, so reassignment is a household-wide operation (an owner/admin service path, not a request handler). Under these rules a joint account can be claimed (made someone's own) by any person in the household; that's the definition of joint, and the API must audit-log it like any visibility change.

**Ownership guard trigger.** `account_guard_ownership` (BEFORE UPDATE on `account`) rejects any change to `visibility` or `owner_person_id` unless the old row is joint, is the current person's, or the session is household-wide. NULL person counts as "not the owner". The policies already make this unreachable; the trigger is defense in depth against a future policy edit. A test proves it on its own by lifting RLS inside a rolled-back transaction.

| Table | Isolation | Extra policy |
|---|---|---|
| household | `id = app_household_id()` | no INSERT or DELETE grant. Rows come only from `create_household()` |
| person, household_setting, bank_connection, category, rule, budget, budget_line, credit_report, tradeline, credit_inquiry, credit_score_entry, document, parsed_field, tax_year, tax_item, tax_scenario, estimate_snapshot | denormalized `household_id` | none yet (see Consequences) |
| account | denormalized | read: `visibility IN ('shared','summary_only') OR owner_person_id IS NULL OR owner_person_id = app_person_id() OR app_household_wide()`. Write: see above |
| transaction, holding, investment_txn, liability | denormalized | read: EXISTS on `account` where `visibility = 'shared'` OR joint OR mine OR household-wide. Write: see above |
| transaction_split | denormalized | read: EXISTS on `transaction` (inherits its visibility). Write: see above |
| balance_snapshot | denormalized | read: EXISTS on `account` (summary_only viewers see balances). Write: see above |
| agent_conversation, agent_finding | denormalized | RESTRICTIVE FOR ALL: `person_id IS NULL OR person_id = app_person_id() OR app_household_wide()` |
| agent_message | denormalized | RESTRICTIVE FOR ALL: EXISTS on `agent_conversation` (inherits its scope) |
| audit_log | `household_id` NULL allowed, no FKs | SELECT `household_id = app_household_id()`. INSERT `household_id IS NULL OR = app_household_id()`. No UPDATE/DELETE grant |
| app_user, webauthn_credential, auth_session, totp_recovery_code | none (pre-login) | only `app/services/auth*` touches them |
| institution, security | none (shared reference data) | no DELETE grant |

**Visibility uses EXISTS.** Visibility is a property of the account, which can change at any time (`shared` → `private`), so it isn't copied onto detail rows. The EXISTS subqueries on `account` are themselves filtered by account's RLS. All per-person policies are RESTRICTIVE, so they can only narrow household isolation, never widen it.

**Agent output inherits the run's visibility.** A run for a person can read that person's private accounts, so anything it produces could quote them. `agent_conversation` and `agent_finding` carry a nullable `person_id` (composite FK to `person`); a row with a person is visible only to that person or a household-wide job, and messages follow their conversation. `person_id` NULL is a household-level run, which must only use household-visible data (the agent tool layer, Phase 8, sets `person_id` whenever a run has a person context). The FK is `ON DELETE CASCADE`, not `SET NULL`: clearing `person_id` would silently publish private output to the whole household.

**`app.household_wide`** is an explicit opt-in for background jobs (bank sync, imports) that must write to every account in a household whatever its owner. It widens person visibility only. The household boundary still holds. Request handlers never set it; `tests/models/test_household_wide_guard.py` fails if `household_wide=True` appears under `backend/app/api/`.

**Split balance.** `transaction_split_balance` is a `DEFERRABLE INITIALLY DEFERRED` constraint trigger on `transaction_split` (insert / update / delete) and on `transaction` (update of `amount`). At commit it checks, per affected parent that has splits, that the splits sum exactly to the parent's amount and every split has the parent's sign (no zero or opposite-sign parts). The function runs as the invoker but turns `app.household_wide` on for its own queries and restores the caller's value, so it counts every split whoever is viewing; Postgres reserves function-level `SET` of custom placeholders to superusers, and a raised error rolls the GUC back with the (sub)transaction. A missing parent means it was deleted, so there's nothing to check. Error messages name the transaction id, never amounts.

**Other database-enforced invariants.** `transfer_group_id IS NULL OR is_transfer`. The rest of the transfer rule (a group is exactly two legs, opposite signs, different accounts, same household) is enforced by the Phase 2 matching service, because one-legged transfers to unlinked accounts are legitimate. Magnitude columns carry `>= 0` CHECKs and Ratio columns `BETWEEN 0 AND 1`; sign conventions are column comments in the schema (`\d+ <table>`). Masks hold at most four digits (`regexp_replace(col, '\D', '', 'g') ~ '^\d{0,4}$'`, mirrored by `app.models._common.is_valid_mask`). `parsed_field.value` and `tax_year.dependents::text` reject SSN-shaped numbers (`ddd-dd-dddd`, `ddd dd dddd` or nine bare digits; ZIP+4 and phone numbers pass). `household.name` and `person.display_name` are 1–200 characters.

**`create_household(name)`** is `SECURITY DEFINER`, owned by `holdfast_owner`, with `search_path = pg_catalog, pg_temp`, fully qualified object names, EXECUTE revoked from PUBLIC and granted to `holdfast_app`. Because `household` has FORCE RLS, which binds the owner too, the function sets `app.household_id` to the new id for its INSERT and then restores the caller's value.

**Grants.** `00-roles.sh` default privileges give `holdfast_app` SELECT/INSERT/UPDATE/DELETE on every owner-created table. Migration 0002 only narrows that: `household` (SELECT, UPDATE), `audit_log` (SELECT, INSERT), `institution`/`security` (no DELETE). Tests assert the exact privilege set per table.

**The audit log outlives what it describes.** `audit_log.household_id` and `user_id` are plain uuids with no foreign keys. FK actions run as referential-integrity triggers that bypass RLS and table grants, so a `CASCADE` from household or a `SET NULL` from app_user would delete or rewrite audit rows behind the append-only grants. For the same reason the app role can't `DELETE FROM household` (that cascade would wipe all household data in one statement): deleting a household will be an owner-run path (a `SECURITY DEFINER` function with re-auth, or an admin CLI) added with the export / delete-my-data feature.

**A person who owns accounts can't be deleted** (`account.owner_person_id` FK is `NO ACTION`). Otherwise their private accounts would silently turn joint (owner NULL) and become visible to everyone. Household deletes still cascade.

## Alternatives considered
- **EXISTS-on-parent isolation for child tables:** no extra column, but every child query pays a join inside the policy, and chains (split → transaction → account) compound. Rejected for isolation. Kept only for visibility, where the data really does live on the parent.
- **Copying `visibility` / `owner_person_id` onto detail rows:** fast, but every visibility change becomes a mass update, and a missed row leaks. Rejected.
- **Treating "no person" as household-wide:** convenient for jobs, but a request handler that forgets the person would see private accounts. Rejected in favor of the explicit `household_wide` flag.
- **Letting viewers of a shared account edit its details (recategorize, add notes):** friendlier, but it makes "shared" mean "co-owned". Joint accounts already cover that. Rejected; revisit with a per-account `editors` list if households ask for it.
- **Split balance in the service layer only:** one bug and budgets double-count. A deferred trigger costs one indexed query per touched parent at commit. Kept in the database.
- **Global uniqueness for `transaction.import_fingerprint` / Plaid ids (PLAN 5):** a unique violation would reveal that another household holds the same row. Scoped to `(account_id, …)` instead. `bank_connection (provider, external_id)` stays global per the contract (Plaid item ids are globally unique anyway).

## Consequences
- Migrations run as `holdfast_owner`, which is also bound by FORCE RLS. Schema-only migrations are unaffected, but a future data migration must set a context (or `household_wide`) per household, or temporarily `NO FORCE` inside the migration.
- INSERT … RETURNING on `audit_log` fails for pre-auth rows (RETURNING must pass the SELECT policy). The `AuditLog` model generates its id client-side and disables eager defaults. Raw SQL must not use RETURNING there.
- Writers of splits must write all of a transaction's splits (and any amount change) in one database transaction; the error surfaces at COMMIT, so services should flush and commit inside their error handling.
- Seed scripts and sync jobs that create accounts owned by a person must run household-wide (or as that person).
- Per-person visibility doesn't yet cover `document`, `parsed_field` or `credit_*` (they're household-visible). They get person-scoped policies, keyed on their `person_id`, when those features land: documents and parsed fields in Phase 3, credit reports in Phase 6. Agent tables are already person-scoped (above).
- Postgres 15+ is required (`ON DELETE SET NULL (col)`). The stack pins 16.
