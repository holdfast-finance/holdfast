# 0028. Recurring series keep their identity across runs; status is computed when read

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect); Keel

## Context
`detect_recurring` (C5a, PR #52) is pure and returns fresh series on every call. It notes that a
schedule that resumes after two misses forms a new series, and that callers must ignore or merge
the older, lapsed duplicate. People will confirm, dismiss and rename series, and those choices
must survive the next import. PLAN 8.4 gives three statuses (`active`, `late`,
`possibly_cancelled` once 1.5 intervals pass with no charge). The review on PR #52 asks consumers
to distrust confidence at or below 0.60 (annual pairs) and leaves open whether a regular weekly
shop is a bill.

## Decision
1. **Stored series have stable ids; a run reconciles detection with what is stored**, through a
   pure function `reconcile_series(stored, detected)` (`app/domain/recurring_series.py`):
   - A detected series matches the stored series it shares the most member transactions with
     (one to one, greedy by overlap, ties by stored id). The stored row keeps its id, `user_state`
     and `label`; detection columns and members are replaced by the detection.
   - An unmatched detection whose `(account, merchant_key, direction, cadence)` equals a stored,
     unmatched, **lapsed** series that ended before it starts is the resumed schedule: it merges
     into that series (the latest-ending one, then lowest id). Members are the union, so history
     stays in one place.
   - A stored series nothing matched: `auto` ones are deleted; `confirmed` and `dismissed` ones
     stay with their old detection columns, so a person's choice is never lost. Any of their
     members claimed by a detection move to that detection.
   - Member sets are disjoint across series (`UNIQUE (transaction_id)` on `recurring_member`).
2. **Status is not stored.** `series_status(cadence, next_expected_date, as_of)` computes it at
   read time: `active` up to the cadence's date tolerance past the expected date (2 days for
   weekly and biweekly, 3 for calendar cadences); `possibly_cancelled` once half a nominal
   interval has passed beyond the expected date (1.5 intervals after the last charge, as PLAN 8.4
   says; nominal intervals 7, 14, 15, 30, 91 and 365 days); `late` in between. Nothing has to run
   at night for a bill to become late.
3. **Two tiers.** `bill` when `user_state = 'confirmed'`, or when confidence is above 0.60 and the
   series is not a variable weekly, biweekly or semi-monthly one. Otherwise `possible`. The list
   shows both, with `possible` collapsed under "Maybe recurring", so an annual bill with two
   payments can still be confirmed and a weekly grocery run does not pose as a bill. Phase 5 (B3)
   forecasts from both tiers as it sees fit.
4. **`dismissed` is sticky**: the series is still reconciled (so its members are not offered as a
   new series), but it is hidden unless asked for.
5. **Merchant keys are versioned** (`MERCHANT_KEY_VERSION` in `app/domain/merchant.py`). Matching
   is by member overlap, so a key change does not orphan series; it can only merge or split them.

## Alternatives considered
- **A natural key `UNIQUE (account, merchant_key, direction, cadence)`:** the detector can return
  two series for one group (two plans billed on one card), and a key change would orphan rows.
- **Replace all series on every run:** loses confirmations, dismissals and labels.
- **Store status and refresh it nightly:** another job and a stale window, for a value that is a
  pure function of three stored fields and today.
- **Use the detector's `lapsed` flag (two missed intervals) as `possibly_cancelled`:** simpler, but
  later than PLAN's 1.5 intervals. `lapsed` is kept for the merge rule only.

## Consequences
- Upcoming-bill and forecast consumers read `next_expected_date`, `status` and `tier` from one
  service, never from the detector directly.
- `as_of` is the UTC date until households have a time-zone setting; a bill can show late up to a
  day early or late near midnight. Recorded as a follow-up for Phase 5.
