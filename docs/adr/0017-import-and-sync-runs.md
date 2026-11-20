# 0017. Import runs, sync runs, and supersession

- Status: Accepted
- Date: 2026-11-20
- Deciders: Lattice (Holdfast). The owner sees it in the weekly digest.

## Context

Phase 3 imports and Phase 2 sync both land transactions, and they overlap. PLAN 8.1
requires a statement to reconcile (opening + the sum of its transactions = closing,
within one cent) or be flagged `needs_review`, and a re-import must insert nothing.
When the same purchase arrives from a statement and from Plaid, Plaid wins and the
imported row stays so its fingerprint still blocks a duplicate (PLAN 8.1 step 5).
F2 already runs one job at a time per household (ADR 0016). The schema for those
runs was the remaining Phase 1.5 gap.

## Decision

`import_run` is one attempt to read one document. Balances use Holdfast's sign
(assets positive, owed negative), so the same equation works for cards.
`balance_diff` is a stored generated column, `closing - opening - txn_sum`.
A run may be `reconciled` only when all three numbers are present and the absolute
difference is at most `0.01`. Anything else is `needs_review`. Accepting a flagged
run requires a user id and a timestamp, and the database refuses to rewrite a
finished reconciliation. A succeeded run has left `pending`: its reconcile status
is `reconciled`, `needs_review`, or `accepted`. A flagged run sets the document's
parse status to `needs_review` and latches that flag on the document. The latch
survives deletion of the run. It clears only when a run is accepted and no sibling
run still needs review.

An imported transaction carries a 64-character hex fingerprint, unique per account,
and the id of the run that created it. Once set, that id cannot be changed or
cleared. The run cannot be deleted while those rows exist. `superseded_by_id`
points at the row that replaced this one. The database keeps that link inside one
account, one-to-one, and forbids chains. The check locks the winner, and any row
this one already replaces, before it looks for a chain. Deleting the winner clears
the link. Metrics exclude superseded rows.

`sync_run` is one connector sync. It stores the cursor it started from and, only
when it succeeds, the cursor it committed. Cursors are opaque paging tokens, not
access tokens. `bank_connection.tx_cursor` remains the committed cursor and is
written in the same transaction as the batch it covers.

Both tables have `household_id`, forced RLS, and an index on `household_id`.
An import run follows its account the way a balance snapshot does. A run with no
account holds no balances and no transaction sum, and is visible to the household.
A sync run is
household-visible, like the connection it belongs to. `error_class` stores an
exception class name, never a message.

`document.storage_path` is relative to `DOCUMENT_STORE` (no leading slash, no `.`
or `..` segment). `document.size_bytes` is required. The table is empty before
this migration, so the new NOT NULL column has no backfill.

## Alternatives considered

- Recompute the reconciliation difference in the service only: a writer could mark
  a run reconciled while the numbers disagree.
- Delete the imported row when Plaid wins: the next import would insert it again.
- Store Plaid cursors encrypted: they are paging tokens, and encryption would not
  match how `tx_cursor` is already stored.

## Consequences

- Ingest and import services must exclude `superseded_by_id IS NOT NULL` from every
  metric and must write `tx_cursor` only after the batch commits.
- Undoing an import deletes its transactions first, then the run.
- Phase 2 and Phase 3 read this schema. They do not add another run table.
