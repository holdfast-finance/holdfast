# 0033. A linked account adopts the imported account the owner picks after a dry-run preview

- Status: Accepted (revision 3, after Gadfly round 2 (R2-B4, R2-B7, R2-S3) and Bulwark's must-fix
  3 on PR #62)
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on B2 and B5. Ledger and Bulwark review P4a,
  P4-preview and P4-map.

## Context
Statement import creates accounts with `source = 'import'`, keyed by `profile:last4:block`. A link
would otherwise create new account rows, so overlap months would show twice. ADR 0017 makes a
row's account final once it joins a run, so the fix has to happen at link time.

The match must be:
- checked by the owner before it happens, on evidence
- undoable, with the history restored
- impossible for sandbox data

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
- An unseen `external_account_id` inserts `pending` and ingests nothing. Nothing is auto-created.
- `status_code` holds per-account connector status, for example SimpleFIN `con.auth` per `conn_id`.

### 2. Dry-run preview, read-only
`POST /api/connections/{id}/preview` enqueues `connections.preview`, an F2 job with
`requested_by` = the caller. The job:
1. Fetches the connector's history into memory with a null cursor, and writes nothing to the
   ledger or the connection. Rows held in memory are capped at 50,000. Past the cap the preview
   fails with `preview_too_large`.
2. Lists candidates. They are computed in `scoped_session(factory, household_id,
   ctx.requested_by)`, never in the job's household-wide session. A preview run with no
   `requested_by` fails. Every imported account the requester may write is ranked, for every
   connector account.
3. Runs ADR 0035's `coverage()` and `match()` for each candidate pairing.

Evidence per pairing:
- **Counts:** coverage intervals, `matched_in_overlap`, `connector_only_in_overlap`,
  `import_only_in_overlap` (overlap = covered periods inside Plaid's range), `would_insert`
  (gaps and after coverage), `would_supersede`, `held`, `history_status`.
- **Rows:** the unmatched and held rows (date, amount, description), for the owner's eyes (R2-S3).

Ranking:
1. match ratio in the overlap, descending
2. mask equal
3. institution equal
4. type equal
5. id

Storage and access:
- The result is stored in Redis for 30 minutes under `(connection_id, requested_by)`. Counts are
  plain. The rows are sealed `hf2`, bound to that key (F5d).
- `GET /api/connections/{id}/preview` returns it only to that person.
- The preview can be re-run at any time and never touches Plaid's cursor. It runs on a held Item
  before ingest exists (M-preview).

### 3. An explicit choice per connector account
`POST /api/connections/{id}/accounts` takes one decision per connector account, plus an optional
`close: [account_id]` for twins:
- `adopt` with an `account_id`
- `new` (adopt nothing), with `owner_person_id` and `visibility`
- `ignore`

Decisions, `close` and un-adopt run in the request's person-scoped session. They never run in a
job and never household-wide, so the account write policy decides. An account the requester can't
write gives `404`.

`adopt` is refused (`409`) unless all of these hold:
- The requester has a preview listing that pairing from the last 30 minutes (`preview_required`).
- `history_status = HISTORICAL_UPDATE_COMPLETE` (`history_incomplete`).
- The candidate has at least one `reconciled` run (`no_reconciled_statement`). Without one, use
  `new`.
- `held = 0`, or the request carries `confirm_held` equal to the held count.
- The connection has `env = 'production'`.
- The account holds no live rows from another connection (`account_has_other_connection`).

A `new` decision may name another person as owner only if that person has a login. Their consent
is not collected in Phase 2; the decision is audited. P4-map's brief records this as a known gap.

### 4. Adoption keeps the account row
One unit of work:
- sets `source` to the provider, `bank_connection_id`, `external_account_id`, and the new column
  `adopted_at`
- writes `account_coverage` (ADR 0035 §1) and the link row

Account id, rows, runs, visibility and owner are untouched. Statement import keys accounts on
`account.statement_key` through one resolver (`app/services/statement_accounts.py`), which I2b
uses too.

### 5. Un-adopt restores the history
`POST /api/connections/{id}/accounts/{external_account_id}/unadopt` takes the owner, step-up, and
`confirm_lost` equal to the dry-run's `user_edited_rows_lost`. A GET first returns
`{rows_removed, user_edited_rows_lost}`. In one person-scoped unit of work it:
1. removes every connector-sourced row in the account through `LedgerWriter.remove_external`.
   That runs `unsupersede_transaction` first (contract §3.3), so carried state and Phase 4's
   references move back and the import rows become live.
   - A winner whose own transfer partner is in an account the requester can't write makes
     Phase 4's `transaction_dissolve_orphan` raise `42501`. Un-adopt is then refused with
     `409 unadopt_blocked` and nothing changes.
   - ADR 0027 pairs only same-owner accounts, so this is rare.
2. deletes only the connector-sourced `balance_snapshot` rows on the account dated on or after
   `adopted_at`. `balance_snapshot` has no source column. Statement import writes none, and M1
   refuses manual balances on an adopted account, so every snapshot dated on or after
   `adopted_at` is connector-sourced.
3. deletes `account_coverage` for the account.
4. sets `source = 'import'`, `external_account_id = statement_key`, and NULL for
   `bank_connection_id` and `adopted_at`.
5. sets the link row back to `pending`.
6. sets the connection's `tx_cursor = NULL`, so a re-adoption replays the full history. Replay is
   idempotent through `upsert_external` and coverage.

The test un-adopts, re-adopts and syncs, and asserts the same live multiset and balances as the
first adoption.

### 6. Database guards (P4a trigger on `account`)
`source` may change in exactly two ways:
- `import` → `plaid`/`simplefin`: requires a non-null `bank_connection_id` in the same UPDATE, and
  that connection must have `env = 'production'`.
- back to `import`: only when no rows with a connector source remain, with `bank_connection_id`
  set NULL in the same UPDATE.

**As built in P4a (migration `0011_connector_link`, trigger `account_guard_source`).** The rule
is tightened so the database holds the whole adoption shape, not just `source`:
- Adoption also needs `adopted_at` in the same UPDATE, and the connection's `provider` must equal
  the new `source`.
- Un-adopt applies only to an adopted account (`adopted_at` was set). The same UPDATE clears
  `adopted_at` and sets `external_account_id = statement_key`. A native connector account never
  becomes `import`.
- `adopted_at` changes only together with `source`. CHECK `adopted_is_connected`: an adopted
  account has a statement key and a connector source.
- `statement_key` is final once set. `UNIQUE (household_id, statement_key)` replaces the 0006
  partial index on `external_account_id`, and the backfill copies `external_account_id` for every
  `import` account. The database does not force `import` accounts to carry a key, because a few
  test helpers insert keyless ones. The resolver (`app/services/statement_accounts.py`) always
  writes both, and an account without a key can't be adopted.
- `connector_account_link.account_id` has no ON DELETE action. An account under a mapped link
  can't be deleted until the link is un-mapped. Deleting the connection deletes its links.
- `balance_snapshot.bank_connection_id` (`SET NULL`) marks connector snapshots (Bulwark, #62).
  Un-adopt can then delete exactly those.

### 7. Sandbox and real data never share a household (R2-B7, Bulwark)
"Real data" means any account, import run, manual entry or production connection. The rule is
enforced in services, with a test for each direction:
- A sandbox Link token is refused on a household with real data (`409 sandbox_on_real_data`).
- A production Link token, a statement or CSV import, and a manual account are refused on a
  household with any sandbox connection (`409 sandbox_connection_present`). The fix is to remove
  the sandbox connection, which deletes its accounts and rows.

Each check runs in the writing transaction, under one `pg_advisory_xact_lock` on the household
shared by link-token, exchange, `ImportService` and M1. The exchange repeats the link-token check.
Accounts and rows that belong to a sandbox connection are not real data.

So a fresh real instance can't keep fake data after its first real import or link, and
`PLAID_ENV` keeps its `sandbox` default (hard rule 4).

## Alternatives considered
- **Auto-adopt on a unique mask match:** masks repeat, and a reissued card splits one account.
- **Un-adopt household-wide (Gadfly R2-B1 item 3):** rejected. Bulwark requires the account write
  policy to decide, and the `42501` case is refused cleanly instead.
- **An `INSTANCE_KIND=real` startup flag:** not adopted. The data rule above closes the gap
  without touching `PLAID_ENV`'s default, as Keel ruled.

## Consequences
- P4a: `connector_account_link`, `account.statement_key`, `account.adopted_at`,
  `account_coverage`, the trigger, the resolver.
- P4-preview: the job, Redis storage, person-scoped candidates.
- P4-link: the coexistence rule (links and uploads). M1 adds it for manual accounts.
- P4-map: decisions, `close` and un-adopt.
- The owner sees "47 of 52 match" and the rows behind any mismatch before choosing, and can undo a
  choice without spending a slot.
