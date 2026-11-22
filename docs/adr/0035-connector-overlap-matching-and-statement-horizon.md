# 0035. Connector rows match imported rows: coverage intervals, then order-preserving matching per amount

- Status: Accepted (revision 2, after Gadfly round 2: R2-B2, R2-B3, R2-S2)
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on B1 and R2-B2. Ledger reviews P2m and P2b.
- Supersedes the exact `(account_id, posted_date, amount)` key in the STATUS carry-over list.

## Context
Statement import stores one date per row in `posted_date`. The merged card layout
(`navy-federal-card.yaml`, lines 20 and 23) captures the first of two printed dates and drops the
second. Interest and fee rows take the period end (line 3). Deposit layouts print one date. Plaid
gives a posted row `date` (posting) and often `authorized_date`.

Revision 1 failed in two ways:
- It used a single horizon `max(period_end) + 1`. That dropped every gap month before it for good,
  and let unreconciled `accepted` runs set it.
- It matched greedily by date distance first, which is not a maximum matching. Four equal daily
  charges with a one-day lag and no `authorized_date` left a duplicate (R2-B2).

## Decision

### 1. Coverage intervals, not a horizon
When an account is adopted (ADR 0033), the server writes `account_coverage(household_id,
account_id, start_date, end_date, source_import_run_id)` rows in the adoption unit of work:
- One interval per import run with `reconcile_status = 'reconciled'` on the adopted account.
  `accepted` runs are not coverage: they are unreconciled by definition.
- `start_date` = the run's `period_start` when present, otherwise the minimum `posted_date` of the
  run's rows. `end_date` = `period_end`.
- **Twins listed in `close`** (ADR 0033 §3) add one interval per run with rows, whatever its
  reconcile status. Their rows sit in another account, where supersession can't reach. Any period
  a twin has rows for must therefore stay import-authoritative.
- Overlapping intervals are merged. Coverage is fixed at adoption. Un-adopt deletes it, and
  re-adopt recomputes it.
- **Policy:** forced RLS: `household_isolation`, plus the restrictive person policies of the
  account detail tables. Read requires that the reader can see the account's details. Insert,
  update and delete require that the reader can write the account, or a household-wide session.
  So a person can neither read another person's coverage nor add coverage that hides their
  connector rows.

A **posted** connector row whose `posted_date` falls inside coverage (inclusive) is not inserted or
updated. It is counted as `skipped_covered`, and a `removed` entry for it is a no-op. Rows in gaps,
and rows after coverage, are ingested and go through §2. Pending rows are never filtered.

### 2. Order-preserving matching per `(account, amount)` group
**Domain.** Live, non-pending rows, from one account and with exactly equal `amount`:
- import rows with `source = 'import'` that are outside coverage or arrived after adoption
- connector rows from a connection with `env = 'production'`

**Effective dates.** `e(i) = i.posted_date`. `e(c) = c.authorized_date` when not NULL, otherwise
`c.posted_date`.

**Window.** `|e(c) - e(i)| <= 3` days (`OVERLAP_WINDOW_DAYS = 3`). It is symmetric: a deposit
layout prints posting dates while Plaid's `authorized_date` is earlier, so the lag can be negative.

**Algorithm.** Two pointers over the group's **unmatched** rows:
1. Sort imports by `(e, import_fingerprint, id)` and connectors by `(e, posted_date, external_id)`.
2. With `p` on the imports and `q` on the connectors:
   - if `|e(i_p) - e(c_q)| <= 3`: pair them, then advance both
   - else if `e(i_p) < e(c_q)`: advance `p`
   - else: advance `q`

For a symmetric window on sorted dates, this greedy walk is a maximum-cardinality matching. It
leaves **no** unmatched import row and unmatched connector row within 3 days of each other in a
group.

### 3. Held pairs (R2-S2)
A pair is **held** instead of superseded when both of these are true:
- either row has more than one candidate within the window in its group
- the rows share no description token

Description tokens come from `normalize_description`, minus tokens containing a digit. P2b swaps
in Phase 4's `merchant_key` once C3c merges; that is the one swap point.

This stops a round-amount ATM withdrawal from taking a transfer leg's state. Held pairs stay live
and are counted as `held`:
- In the preview, the owner sees them row by row (ADR 0033 §2). Adoption needs `held = 0`, or the
  owner types the held count to confirm them. Confirmed held pairs are then superseded like any
  other pair.
- After adoption, held pairs are counted on `sync_run.rows_held` and reported by V1 and, later,
  Sync Health.

### 4. When matching runs
- Matching runs after each committed ingest batch and after each statement block insert.
- For a connection whose Plaid history is not complete (`transactions_update_status` is not
  `HISTORICAL_UPDATE_COMPLETE`), matching is **deferred** until it is. Plaid fills history newest
  first, and pairs made earlier are never re-paired.
- Adoption itself requires complete history (ADR 0033 §3). So at M-live the first sync is one
  batch, and every group gets one maximum matching.

### 5. Properties (P2m tests)
- **Single pass:** for any group, the pair count equals the maximum matching size. Brute force
  checks this on small random groups.
- **No residue:** after any interleaving of batches and statement blocks, no group holds an
  unmatched import row and an unmatched connector row within the window.
- **Batch equivalence:** with history complete before the first pass, any split of the connector
  rows into batches gives the same live multiset per account. The pairs themselves may differ, and
  that is not asserted.
- **Determinism:** the result doesn't depend on input order.
- **Named cases:**
  - Gadfly's four daily charges (imports days 1–4, connector posted 2–5), with and without
    `authorized_date`: four pairs.
  - The two-coffees case (two plus two on one day): two pairs. Two imports against one connector
    row: one pair and one unmatched import row.
  - A one-cent difference never matches.
  - An interest row at period end.
  - A gap month: connector rows inside it are inserted; rows inside coverage are skipped.
  - A twin's period stays covered.
- **Fixture gate:** sanitized real-layout card rows (`make sanitize`, ADR 0019) against synthetic
  connector rows posted 0 to 3 days later, with `authorized_date` equal to the statement date. It
  must leave zero live duplicates before P2b merges.

### 6. Residual risk, accepted
- Two genuinely different same-amount purchases within 3 days, each seen by only one source, are
  paired. That is visible as a pair and reversible by un-adopt.
- Duplicates with a posting lag over 3 days are not matched. V1's per-day counts (contract §6.4,
  measure 3b) surface them.

## Alternatives considered
- **Greedy by date distance (revision 1):** not maximum (R2-B2).
- **Min-cost maximum matching per group:** also maximum, with a merchant cost. Keel ruled for the
  order-preserving walk, which is simpler and easy to verify by brute force. The merchant signal
  becomes the held rule.
- **An asymmetric window `[-1, +3]`:** misses deposit layouts with earlier `authorized_date`.
- **A single horizon (revision 1):** loses gap months (R2-B3).

## Consequences
- P4a adds `account_coverage` and `sync_run.rows_skipped_covered` and `rows_held`.
- P2m is pure (`app/domain/supersede.py`: `coverage()`, `match()`, `is_held()`) and suits Codex.
  P4-preview and P2b call it.
