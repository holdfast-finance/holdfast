# 0033. A linked account adopts the imported account the owner picks after a dry-run preview

- Status: Accepted (revision 2, after Gadfly's B2, B5 and S6 in
  `docs/phases/reviews/PHASE-2-contract-gadfly.md`)
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on B2 and B5. Ledger and Bulwark review P4a and P4.

## Context
Statement import creates accounts with `source = 'import'`. Each is keyed by
`profile:last4:block` (`statement_import.external_account_key`) under a partial unique index
(migration 0006). A Plaid or SimpleFIN link would otherwise create new account rows, so overlap
months would show twice and net worth would count the account twice. ADR 0017 makes a row's account
final once it joins an import run, so the fix has to happen at link time.

Revision 1 filtered candidates by type and mask, gave the owner nothing to check, and made adoption
permanent. That fails in three ways:
- A reissued card, or a profile change, splits one real account into two imported accounts.
- A wrong pick could never be undone.
- A sandbox Item could adopt a real account.

## Decision

### 1. Per connector account, a recorded decision
P4a adds `connector_account_link(household_id, bank_connection_id, external_account_id, state,
account_id NULL, decided_by_user_id NULL, decided_at NULL, status_code NULL)`, with
`UNIQUE (bank_connection_id, external_account_id)`, forced RLS and the standard policy.

| `state` | Meaning |
|---|---|
| `pending` | Waiting for the owner's decision |
| `mapped` | Rows land in `account_id` (adopted or new) |
| `ignored` | Never ingested |

`IngestService` resolves accounts only through this table:
- An unseen `external_account_id` inserts a `pending` row and ingests nothing for it. This covers
  accounts that appear on an Item later (S6). Nothing is ever auto-created.
- `status_code` holds per-account connector status, for example SimpleFIN `con.auth` per `conn_id`
  (ADR 0030).

### 2. Dry-run preview before any choice
`POST /api/connections/{id}/preview` enqueues `connections.preview` (an F2 job, `requested_by` =
the owner). The job:
1. Fetches the connector's history into memory with no cursor written. Plaid's `/transactions/sync`
   from a null cursor can be repeated, so nothing is spent.
2. For each connector account, ranks **every** imported account the requester may write (computed
   in a person-scoped session, ADR 0008).
3. For each candidate, computes evidence by running the ADR 0035 horizon and matcher on the rows
   held in memory.

Evidence, counts and dates only:
- `horizon` (the `H` that adoption would store)
- `overlap_days`: the 90 days before `H`
- `matched_before_horizon`, `connector_only_before_horizon`, `import_only_before_horizon`
- `would_insert` (rows after `H`)
- `would_supersede` (imported rows after `H` that a connector row would replace)
- `history_status` (Plaid's `transactions_update_status`)

Ranking:
1. match ratio before the horizon, descending
2. mask equal
3. institution name equal
4. type equal
5. account id

The result is kept in Redis for 30 minutes under the connection id, as counts only.
`GET /api/connections/{id}/preview` returns it. The owner can re-run the preview while Plaid's
history is still filling in.

### 3. An explicit choice per connector account
`POST /api/connections/{id}/accounts` takes one decision per connector account:
- `adopt` with an `account_id`
- `new`, which creates a fresh account and adopts nothing, with `owner_person_id` and `visibility`
- `ignore`

An optional `close: [account_id]` sets `closed_at` on twin imported accounts that the connection
now covers, for example the old number of a reissued card.

Rules:
- Every connector account needs a decision before the connection becomes `active` and syncs.
- `adopt` is refused with `409 preview_required` unless a preview for this connection, taken in the
  last 30 minutes, lists that pairing.
- Adoption is never automatic, not even for a single perfect match.

### 4. Adoption keeps the account row
One unit of work:
- sets `source` to the provider, `bank_connection_id`, `external_account_id` = the connector id,
  and `connector_since` (ADR 0035)
- writes the `connector_account_link` row

Account id, rows, runs, balances, visibility and owner are untouched. Statement import keys accounts
on `account.statement_key`. P4a backfills it from `external_account_id` for `source = 'import'`
and swaps the unique index. A later statement therefore lands on the adopted row.

### 5. Un-adopt, in P4
`POST /api/connections/{id}/accounts/{external_account_id}/unadopt` (owner and step-up). In one
unit of work it:
1. removes every connector-sourced row in the account through `LedgerWriter.remove_external`. That
   runs the reverse of the supersede clause, so carried state moves back and the superseded import
   rows become live again.
2. sets `source = 'import'`, `external_account_id = statement_key`, `bank_connection_id = NULL`
   and `connector_since = NULL`.
3. sets the link row back to `pending`.

Connector balance snapshots stay, because they are dated facts. No Item or slot is involved.

### 6. Database guards (P4a trigger on `account`)
`source` may change in exactly two ways:
- `import` → `plaid`/`simplefin`: requires a non-null `bank_connection_id` set in the same UPDATE,
  and that connection must have `env = 'production'`.
- `plaid`/`simplefin` → `import`: only when no rows with a connector `source` remain in the account,
  with `bank_connection_id` set NULL in the same UPDATE.

Two more rules:
- **A sandbox connection can never adopt.** The trigger enforces it, the candidate query enforces
  it, and a test covers each.
- An account that still holds live connector rows from **another** connection can't be adopted
  (`409 account_has_other_connection`). Item replacement (a new Item for the same login) is defined
  by P8, not here.

## Alternatives considered
- **Auto-adopt on a unique mask match:** masks repeat, and a reissue splits accounts.
- **Filter candidates by mask:** hides the right twin (Gadfly B2).
- **Two accounts plus `merged_into_id`:** every metric and RLS predicate would need to fold accounts.
- **Permanent adoption (revision 1):** a wrong first link would only be fixable with a new Item,
  which costs a slot.

## Consequences
- P4a: `connector_account_link`, `account.statement_key`, `account.connector_since`, the trigger,
  and one shared resolver `app/services/statement_accounts.py` that statement import and I2b both
  use (S3).
- P4: the preview job, decisions, un-adopt and the sandbox refusal. P9b-min renders the preview
  with its evidence.
- The owner sees "47 of 52 rows match" or "0 of 61 match" before choosing, and can undo a choice.
