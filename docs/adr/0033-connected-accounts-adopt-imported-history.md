# 0033. A linked account adopts the imported account it matches

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect). Ledger and Bulwark review the P4a migration.

## Context
Statement import creates accounts with `source = 'import'`, keyed by `(household_id,
external_account_id)` under a partial unique index (`services/statement_import.py`, migration 0006).
A Plaid or SimpleFIN link would create **new** account rows keyed by `(bank_connection_id,
external_account_id)`. The STATUS carry-over rule dedupes Plaid against imported rows "by
`(account_id, posted_date, amount)`". That works only when both rows sit in **the same account**.

The owner imports statements first (ADR 0019) and links banks afterwards. Without adoption, every
overlap month would show twice, and net worth would count the account twice. ADR 0017 makes a row's
account final once it joins an import run, so imported rows can't be moved to the linked account
later. The fix has to happen at link time.

## Decision
1. **When a link completes, the person maps each connector account to an existing imported account
   or to "new".**
   - The exchange returns candidates: same household, `source = 'import'`, not already adopted,
     same `type`, and the same `mask` when both have one. The candidates are filtered to accounts
     the person may write (ADR 0008).
   - Nothing is adopted automatically, even when there is exactly one candidate.
   - The connection stays `pending_setup` and does not sync until the mapping is confirmed.
2. **Adoption keeps the account row.** One unit of work sets `source` to the provider, sets
   `bank_connection_id`, and sets `external_account_id` to the connector's id. Account id,
   transactions, import runs, balances, visibility and owner are untouched.
3. **Statement import keys accounts on a new column, `account.statement_key`.**
   - The P4a migration backfills it from `external_account_id` for every `source = 'import'` row.
   - It replaces the partial unique index with `UNIQUE (household_id, statement_key) WHERE
     statement_key IS NOT NULL`.
   - `_account()` in statement import looks up and inserts by `statement_key`. A later statement
     for an adopted account therefore lands on the same row.
4. **A trigger guards adoption.** `source` may change only from `import` to `plaid` or `simplefin`,
   only in the same statement that sets a non-null `bank_connection_id`, and never back. It is not
   a second visibility rule, so ADR 0008's owner and visibility trigger still applies.
5. **Un-adopting is out of scope.** Disconnecting a connection sets the account's
   `bank_connection_id` to NULL (the existing FK action) and leaves its history. A later re-link
   offers the same row as a candidate, because the candidate query also accepts
   `source IN (plaid, simplefin) AND bank_connection_id IS NULL`.

## Alternatives considered
- **Auto-adopt on a unique mask match:** a wrong merge can't be undone (ADR 0017 finality), and
  masks repeat across a household, for example two cards ending in the same digits at different
  banks.
- **Two accounts plus `merged_into_id`:** every metric, view and RLS predicate would need to fold
  accounts. That is permanent complexity, against one link-time choice.
- **Keep `source = 'import'` on adopted accounts and add a `provider_account_id`:** `source` would
  stop meaning where an account's data comes from, and the connector lookup would need a second key.

## Consequences
- P4a owns the migration and the `_account()` switch, which is the Tide lane, in one PR, so
  statement import never runs against a half-migrated key.
- The exchange flow gains a confirm step (`POST /api/connections/{id}/accounts`). P9b renders it.
- Dedupe across sources (P2b) can rely on "same account" and stays a simple multiset match.
- Hidden accounts (another person's private imported account) are never offered, so a person can't
  adopt, and so can't learn about, an account they can't see.
