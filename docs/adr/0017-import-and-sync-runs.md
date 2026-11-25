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
and the id of the run that created it. Every imported row has a run (CHECK), so no
imported row sits outside a reconciliation. A row joins a run only when it is
inserted, only while the run is `running` with reconciliation `pending`, and only in
the run's account when the run has one. After that the link and the row's account
are final, and a run with rows keeps its account and document. The run cannot be
deleted while those rows exist.

A run that moves to `failed` while it has rows and is still `pending` becomes
`needs_review` in the same statement, so its document latches. We chose this over
refusing the move. A crashed import is marked failed by whatever finds it (the job
retry path, an operator, a later cleanup). If the database refused that move, the
run would stay `running` with its rows unflagged, which is the outcome hard rule 6
forbids. Forcing `needs_review` means every path that records the failure also flags
the rows. A run that fails before landing a row stays `pending` and flags nothing.

The trigger that admits a row reads the run in the writer's own RLS context, not
household-wide, and locks it `FOR SHARE`. That lock needs the run's read and update
policies, so a run the writer can't write is "not found". This is the one guard that
must see less than the household: reading household-wide would let one person add
rows to another person's run, and a run it can't see is refused, so it fails closed.
A hidden run and a missing one raise the same error and SQLSTATE before the foreign
key runs, so the key can't tell anyone a hidden run exists. The lock also makes a
concurrent reconcile or fail wait for the row, so a row can't join a run that was
just closed.

Undo deletes a run's rows under the person's RLS, then the run. Because a person can
only add rows to runs they may write, another person's undo can't delete their rows,
and their rows can't block someone else's undo. An account-less run (a multi-account
export) can still hold rows in several accounts. If a household-wide job puts a row
in a private account, a person's undo of that run fails on the foreign key and
deletes nothing. The service undoes those runs household-wide. `superseded_by_id`
points at the row that replaced this one. The database keeps that link inside one
account, one-to-one, and forbids chains. The check locks the winner, and any row
this one already replaces, before it looks for a chain. Deleting the winner clears
the link. Metrics exclude superseded rows.

`sync_run` is one connector sync. It stores the cursor it started from and, only
when it succeeds, the cursor it committed. Cursors are opaque paging tokens, not
access tokens. `bank_connection.tx_cursor` remains the committed cursor and is
written in the same transaction as the batch it covers.

Both tables have `household_id`, forced RLS, and an index on `household_id`.
An import run is visible only with its document, and follows its account the way a
balance snapshot does. A run with no account holds no balances and no transaction
sum. Writing a run (starting it, flagging it, accepting it, adding rows) also needs
its document to be the writer's or the household's (`person_id` NULL), unless it is
a household-wide job. Otherwise one person could latch or accept another person's
statement through an account-less run, or a run on a joint account. A sync run is
household-visible, like the connection it belongs to.

`reviewed_by_user_id` is advisory. The database has no way to know which user owns
the session, so it checks only that an accepted run names someone and a time. The
audit log is the record of who accepted. Any accept service must take the id from
the authenticated session, never from the request body. `error_class` stores an
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
- Refuse `running` to `failed` while unreconciled rows exist: a crashed run could then
  never be closed, and its rows would stay unflagged.
- Require every run with rows to have an account: it would break multi-account
  exports. Requiring write access to the run closes the same hole.

## Consequences

- Ingest and import services must exclude `superseded_by_id IS NOT NULL` from every
  metric and must write `tx_cursor` only after the batch commits.
- Undoing an import deletes its transactions first, then the run.
- The import service creates or selects the account before inserting rows for a
  single-account statement and starts the run (`running`) before its first row.
- Documents themselves are household-visible and household-writable. Person-scoped
  document policies are a separate decision. If they are added, runs inherit them
  through the policies above.
- Phase 2 and Phase 3 read this schema. They do not add another run table.
