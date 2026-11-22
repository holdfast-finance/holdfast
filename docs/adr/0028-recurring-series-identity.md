# 0028. Recurring series keep their identity across runs; status is computed when read

- Status: Accepted
- Date: 2026-11-21 (revised the same day after the Gadfly and Bulwark reviews of PR #59)
- Deciders: Lattice (architect); Keel

## Context
`detect_recurring` (C5a, PR #52) is pure and returns fresh series on every call. It notes that a
schedule that resumes after two misses forms a new series, and that callers must ignore or merge
the older, lapsed duplicate.

People will confirm, dismiss and rename series, and those choices must survive:
- the next import
- a refresh that runs while they click
- the rows under the series being superseded

PLAN 8.4 gives three statuses: `active`, `late`, and `possibly_cancelled` once 1.5 intervals pass
with no charge. The review on PR #52 asks consumers to distrust confidence at or below 0.60
(annual pairs), and leaves open whether a regular weekly shop is a bill.

## Decision
1. **Stored series have stable ids, and a run reconciles detection with what's stored**, through a
   pure function `reconcile_series(stored, detected)` (`app/domain/recurring_series.py`):
   - **Matching:** a detected series matches the stored series it shares the most member
     transactions with. It's one to one and greedy by overlap. Ties go to a user-stated series
     (`confirmed`, `dismissed` or labelled) over an `auto` one, then to the lowest stored id. The
     stored row keeps its id, `user_state` and `label`. Detection columns and members are replaced
     by the detection.
   - **Lapsed merge:** an unmatched detection is the resumed schedule when its `(account,
     merchant_key, direction, cadence)` equals a stored, unmatched, **lapsed** series that ended
     before it starts. It merges into that series (the latest-ending one, then the lowest id).
     Members are the union, so history stays in one place.
   - **Takeover:** sometimes a user-stated series goes unmatched while one detection took more than
     half of its members, and that detection matched an `auto` stored series. The user-stated
     series then takes the detection: its id, `user_state` and `label` survive, and the `auto`
     series is deleted. This stops a hollow confirmed series sitting next to an `auto` twin.
   - **Unmatched stored series:** an `auto`, unlabelled one is deleted. `confirmed`, `dismissed` and
     labelled ones stay with their old detection columns, so a person's choice is never lost. Any of
     their members claimed by a detection move to that detection.
   - Member sets are disjoint across series (`UNIQUE (transaction_id)` on `recurring_member`).
   - **A label implies `confirmed`** unless the series is `dismissed`. Naming a series is
     confirming it, so "the label sticks" holds.
   - **Supersession keeps identity.** When a member row is superseded, a trigger re-points its
     `recurring_member` row to the successor in the same unit of work. A series whose rows are all
     superseded keeps its id, members and state. Listings count and show live members only.
2. **The job never overwrites a choice made while it runs.**
   - Detection takes seconds, and the stored series loaded before it can be stale. So after
     detection, the job locks the household's stored series (`SELECT … FOR UPDATE`, with their
     members) and re-runs `reconcile_series` on the locked state. The re-plan is pure and cheap.
   - Deletes carry `WHERE user_state = 'auto' AND label IS NULL`.
   - Upserts never write `user_state` or `label`, except the takeover rule, which copies values read
     under the lock.
   - A `PATCH` that arrives during the job's final write waits for it. If the job deleted that
     `auto` series, the PATCH returns 404 and the screen refetches.
3. **Status isn't stored.** `series_status(cadence, next_expected_date, as_of)` computes it at read
   time:
   - `active` up to the cadence's date tolerance past the expected date (2 days for weekly and
     biweekly, 3 for calendar cadences)
   - `possibly_cancelled` once half a nominal interval has passed beyond the expected date. That's
     1.5 intervals after the last charge, as PLAN 8.4 says. Nominal intervals are 7, 14, 15, 30, 91
     and 365 days.
   - `late` in between

   Nothing has to run at night for a bill to become late.
4. **Two tiers.**
   - `bill` when `user_state = 'confirmed'`, or when confidence is above 0.60 and the series isn't a
     variable weekly, biweekly or semi-monthly one. Otherwise `possible`.
   - Confidence is quantized to three places before it's stored, and the tier is always computed
     from the stored value, so a series can't change tier on a round trip.
   - The list shows both, with `possible` collapsed under "Maybe recurring". An annual bill with two
     payments can still be confirmed, and a weekly grocery run doesn't pose as a bill.
   - Phase 5 (B3) forecasts from both tiers as it sees fit.
5. **`dismissed` is sticky.** The series is still reconciled, so its members aren't offered as a
   new series, but it's hidden unless asked for.
6. **Merchant keys are versioned** (`MERCHANT_KEY_VERSION` in `app/domain/merchant.py`). Matching
   is by member overlap, so a key change doesn't orphan series. It can only merge or split them.

## Alternatives considered
- **A natural key `UNIQUE (account, merchant_key, direction, cadence)`:** the detector can return
  two series for one group (two plans billed on one card), and a key change would orphan rows.
- **Replace all series on every run:** it loses confirmations, dismissals and labels.
- **Mapping member ids through `superseded_by_id` inside `reconcile_series`:** it works for
  reconciliation, but every reader would need the same mapping. Re-pointing once, in the same unit
  of work as the supersede, keeps the stored members true.
- **Optimistic concurrency (a version column checked on write):** a conflict would fail the whole
  job attempt and retry 7 s of detection. Locking only for the final write costs nothing.
- **Store status and refresh it nightly:** another job and a stale window, for a value that's a
  pure function of three stored fields and today.
- **Use the detector's `lapsed` flag (two missed intervals) as `possibly_cancelled`:** simpler, but
  later than PLAN's 1.5 intervals. `lapsed` is kept for the merge rule only.

## Consequences
- Upcoming-bill and forecast consumers read `next_expected_date`, `status` and `tier` from one
  service, never from the detector directly.
- `as_of` is the UTC date until households have a time-zone setting, so a bill can show late up to
  a day early or late near midnight. Recorded as a Phase 5 follow-up.
- Series are per account (the detector keys on account, and so do `recurring_member`'s FKs). PLAN
  8.4 groups by merchant. A reissued card, or a bill that moves accounts, starts a new series
  without the person's confirmation or label. Accepted for Phase 4 and recorded as a follow-up.
