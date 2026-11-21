# 0026. Transfer pairs need one owner, and "not a transfer" is a stored correction

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect); Keel

## Context
C4 (PR #50) merged the matcher and `pair_transfers`, with a guard test that keeps both away from
jobs and the API because:
1. Nothing records a person's rejection, so a pair someone undoes is re-paired on the next run.
2. A pair between a private account and a joint one cannot be undone by the other joint member:
   `unmark_transfer` needs both legs writable (ADR 0008), and the private leg is not.
3. Rows a person categorised or split are protected (never auto-paired), but a person who
   categorised a row as a transfer is also treated as "treated as spending".

## Decision
1. **A transfer pair (a shared `transfer_group_id`) only joins two accounts with the same
   `owner_person_id`**: both joint, or both owned by one person. Then anyone who can write one leg
   can write the other, and anyone who can see one leg's details can see the other's, so either
   leg's writers can always dissolve the pair. `LedgerWriter._check_pair` enforces it for manual
   and automatic pairs (`TransferPairError("different owners")`), and the pure matcher treats
   joint versus owned as different owners (`different_owners`, suggest only).
2. **A move between owners is two single-leg transfers.** Accepting a cross-owner suggestion marks
   each leg the person may write as a one-legged transfer (`mark_transfer_single`, no group).
   Metrics exclude both legs the same way (`is_transfer`), and each leg's writers can unmark their
   own leg. Nobody is left looking at a pair they cannot inspect or undo.
3. **`transfer_rejection` records "not a transfer"**, with two kinds:
   - `pair`: these two legs are not one transfer. Written when a person rejects a suggestion and
     when a person unmarks a pair (automatic or manual) through `LedgerWriter.unmark_transfer`,
     in the same unit of work.
   - `leg`: this row is never a transfer. Written when a person unmarks a one-legged transfer, or
     picks "Never a transfer" on a review card.
   The matcher drops rejected edges before ranking, and drops leg-rejected rows from the pool
   entirely (not even as competitors). A manual pair of the same two legs deletes their pair
   rejection; marking a leg-rejected row as a transfer deletes its leg rejection. Rejections are
   removed when a leg is deleted (`ON DELETE CASCADE`).
4. **A person's transfer-kind category is not "spending".** A row is protected only when its
   `category_source = 'user'` and the category's kind is not `transfer` or `excluded`, or when it
   is split.
5. **Pairing never changes categories.** `is_transfer` alone excludes a row from metrics (ADR 0022,
   rule 1), so unmarking restores the row exactly.
6. **Scheduled pairing needs provenance.** `pair_transfers` raises when both `user_id` and
   `job_run_id` are missing; the audit detail of every automatic pair carries `job_run_id`.

## Alternatives considered
- **A `SECURITY DEFINER` "dissolve" function** that lets one leg's writer clear the other leg:
  works, but adds a privileged path that writes rows the caller cannot see, and the other person
  still sees a pair they can't inspect.
- **Per-person pair visibility:** a pair is one fact about two rows; showing it to one person and
  not the other makes metrics disagree.
- **Rejections in the audit log:** append-only and household-visible, so not queryable per person
  under RLS, and the matcher would scan the log.
- **Rejecting by amount and dates instead of row ids:** survives re-imports that change ids, but
  ids are stable here (fingerprints, external ids), and a rule that broad could hide real transfers.

## Consequences
- `account_guard_ownership` allows claiming a joint account (ADR 0008). A claim would turn an
  existing joint/joint pair into a cross-owner one. The claim path, when built, must dissolve
  those pairs into single legs in the same unit of work. Until then nothing in the API changes
  ownership.
- Cross-owner moves have no `transfer_group_id`, so a "show the other side" link is not
  available for them. Accepted.
