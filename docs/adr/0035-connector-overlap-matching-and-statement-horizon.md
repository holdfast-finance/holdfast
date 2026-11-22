# 0035. Connector rows match imported rows by amount and a 3-day date window, after a statement horizon

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on Gadfly's B1. Ledger reviews P2b.
- Supersedes the exact `(account_id, posted_date, amount)` key in the STATUS carry-over list and in
  the first draft of the Phase 2 contract (D7).

## Context
- **What a statement row stores.** Statement import stores one date per row in `posted_date`
  (`authorized_date` stays NULL). The merged card profile `navy-federal-card.yaml` captures the
  **first** of two printed dates and drops the second (lines 20 and 23). Interest and fee rows have
  no printed date and take the period end (line 3). The profile doesn't record which printed column
  is the transaction date, so this ADR doesn't depend on it.
- **What a Plaid row carries.** Plaid gives a posted row `date` (the posting date) and, often,
  `authorized_date` (when the purchase was made). Card purchases routinely post one to three days
  after they are made.
- **Why the old key fails.** An exact date key would miss a large share of real card overlaps, and
  one initial sync with `days_requested = 730` would write both copies into live data (Gadfly B1).

## Decision

### 1. Statement horizon
- When a connector account adopts an imported account (ADR 0033), the server stores
  `account.connector_since = H + 1 day` once, in the adoption unit of work. `H` is the latest
  `period_end` over that account's import runs with `reconcile_status IN ('reconciled',
  'accepted')`. With no such run, `connector_since` is NULL.
- `connector_since` never moves afterwards. Importing a later statement does not move it.
- For an account with `connector_since = S`, a **posted** connector row whose `posted_date < S` is
  not inserted and not updated, in `added` and `modified` alike. It is counted as
  `skipped_before_horizon`. A `removed` entry for such a row is a no-op.
- Pending connector rows are never filtered by the horizon.
- So imported history up to `H` stays authoritative, and the first sync doesn't touch it.

### 2. Eligibility for matching
A pair `(i, c)` is a **candidate** when all of these hold:
- same `account_id`
- `i.source = 'import'`, live (`superseded_by_id IS NULL`), not pending
- `c.source IN ('plaid', 'simplefin')`, live, not pending, and its connection has
  `env = 'production'`
- `i.amount = c.amount` (exact `Decimal`)
- `d(i, c) <= 3`, where `d(i, c) = min(|i.posted_date - c.posted_date|,
  |i.posted_date - c.authorized_date|)` in whole days. A NULL `authorized_date` term is ignored.

The window constant is `OVERLAP_WINDOW_DAYS = 3` in `app/domain/supersede.py`.

### 3. Assignment
Assignment is deterministic and greedy over every candidate edge. It runs per account after each
ingest batch and after each statement block insert:
1. Sort all candidate edges by the tuple `(d, i.posted_date, i.import_fingerprint, i.id,
   c.posted_date, c.external_id)`, ascending.
2. Walk the list. Accept an edge when neither `i` nor `c` is already matched in this pass or is
   already a loser or winner of an existing supersession.
3. Every accepted edge becomes `supersede(loser = i, winner = c)`, using the Phase 2 contract §3.3
   clause.

The greedy pass is maximal: no candidate edge with both ends unmatched survives. The result
doesn't depend on input order.

### 4. Same-day, equal-amount rows (the two-coffees case)
- Two import rows and two connector rows with the same amount and the same day are four edges with
  `d = 0`. The sort order pairs them by fingerprint (which carries the same-day ordinal), then by
  `external_id`. That gives two supersessions and no live duplicate.
- With two import rows and one connector row, one pair forms and one import row stays live. It is
  counted as `unmatched_import_in_window`, which the M-live audit reports.
- Rows that differ by a cent never match.

### 5. Residual risk, accepted
Two genuinely different purchases with the same amount, within 3 days, where each source has only
one of them, would be wrongly paired. The pairing keeps both rows (the loser is history, not
deleted), and the M-live audit counts unmatched rows on each side.

### 6. Out of scope here
Description similarity, a wider window, and sending ambiguous cases to a review queue. That is I4,
later.

## Alternatives considered
- **Exact `(account_id, posted_date, amount)`:** fails on posting lag (B1).
- **±3 days with "ties go to review":** with no transaction review queue in Phase 2, a tie would
  leave both rows live (a duplicate) until a later phase. Keel ruled for a deterministic tie-break.
- **No horizon (match everything):** the first sync would try to pair about 24 months of rows, and
  every miss would be a duplicate in data the owner already reconciled.
- **Recompute the horizon on each statement import:** connector rows already inserted would fall
  behind a moving horizon and look like duplicates of the new statement.

## Consequences
- P4a adds `account.connector_since date NULL`.
- P2b implements §2 to §4 as pure code with property tests, including:
  - a fixture of sanitized real-layout card rows (`make sanitize`, ADR 0019) paired with synthetic
    connector rows posted 0 to 3 days later, with `authorized_date` equal to the statement date
  - the two-coffees table
  - a check that the result doesn't depend on input order
  - an interest row dated at period end
- The P4 dry-run preview (ADR 0033) runs the same functions on fetched rows held in memory.
