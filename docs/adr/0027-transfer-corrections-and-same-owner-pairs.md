# 0027. Transfer pairs need one owner, moves between owners are links, and "not a transfer" is stored

- Status: Accepted
- Date: 2026-11-21 (revised the same day after the Gadfly and Bulwark reviews of PR #59)
- Deciders: Lattice (architect); Keel

## Context
C4 (PR #50) merged the matcher and `pair_transfers`, with a guard test that keeps both away from
jobs and the API, because:
1. Nothing records a person's rejection, so a pair someone undoes is re-paired on the next run.
2. The other joint member can't undo a pair between a private account and a joint one:
   `unmark_transfer` needs both legs writable (ADR 0008), and the private leg isn't.
3. Rows a person categorised or split are protected (never auto-paired). But a person who
   categorised a row *as a transfer* is also treated as having "treated it as spending".

Review of the first draft added these points:
- The most common household layout is joint checking paying one person's card. It must be excluded
  on both legs without a click (acceptance criterion 2). Statement rows have no Plaid category to
  fall back on.
- Nothing can change an account's owner, but the database allows it. The first change would leave
  pairs the design says can't exist.
- Deleting or superseding a paired leg corrupts its partner.
- A job that pairs while a person rejects can lose the rejection.

## Decision
1. **A transfer pair (a shared `transfer_group_id`) only joins two accounts with the same
   `owner_person_id`.** That means both joint, or both owned by one person. NULL (joint) is
   compared as a value, so joint versus owned differs.
   - So anyone who can **write** one leg can write the other, and either leg's writers can always
     dissolve the pair.
   - **Detail visibility can still differ.** One leg may be on a `shared` account and the other on
     a `private` one, both owned by the same person. Another member who sees the shared leg sees
     its `transfer_group_id` but can't open the other leg. That tells them money moved to an
     account hidden from them. Accepted: the amount and date are already on the visible leg, and
     `transfer_counterpart_id` is NULL for them. (This corrects the first draft, which said either
     leg's viewers could see the other.)
   - `LedgerWriter._check_pair` enforces the owner rule for manual and automatic pairs
     (`TransferPairError("different owners")`).
2. **A move between owners is two single legs joined by a `transfer_link`.**
   - Each leg is `is_transfer` with no group, so metrics exclude both the same way.
   - The link records the pair of legs. It's readable when both legs are detail-visible (ADR 0026
     point 5).
   - It has an `origin`:
     - `job`: step 1 found a cross-owner edge that meets the same evidence bar as auto-pairing
       (both legs hinted, or a mask hint), and marked both legs.
     - `person`: someone accepted a cross-owner suggestion, marking the legs they may write.
   - Each leg's writers can unmark their own leg.
   - **Undoing a leg:**
     - A person who can see the link and unmarks a leg writes a `pair` rejection for the edge.
     - A person who can't see the link writes nothing.
     - At the next refresh, a `job` link with an unmarked leg becomes a `pair` rejection, and the
       job undoes its own mark on the other leg.

     The rule: **the job never keeps a mark of its own whose evidence was rejected or deleted, and
     never changes a mark a person made.**
   - A `person` link with one leg still unmarked is a half-accepted move. The person who can write
     that leg is offered it (`other_side_marked`), and the review list hides a card only when every
     leg the reader can write is already a transfer.
   - **Residual, stated:** an *unhinted* move between two accounts that no single person can see
     both of (two private accounts) is never shown to anyone. Both legs stay counted until each
     owner marks their side. C7 reports how many of these exist.
3. **`transfer_rejection` records "not a transfer"**, with two kinds:
   - `pair`: these two legs aren't one transfer. It's written when:
     - a person rejects a suggestion
     - a person unmarks a pair
     - a person unmarks a linked leg whose link they can see
     - step 0 finds a `job` link with an unmarked leg (`rejected_by_person_id` NULL, `job_run_id` in
       the audit)
   - `leg`: this row is never a transfer. It's written only when a person picks "Never a
     transfer". Plain unmarking of a single leg no longer writes one, because that made one wrong
     click permanent.

   The matcher drops rejected edges before ranking, and drops leg-rejected rows from the pool
   entirely, not even as competitors. A manual pair or link of the same two legs deletes their pair
   rejection. Marking a leg-rejected row as a transfer deletes its leg rejection.
4. **A person's transfer-kind category isn't "spending".** A row is protected only when its
   `category_source = 'user'` and the category's kind isn't `transfer` or `excluded`, or when it's
   split.
5. **Pairing and linking never change categories.** `is_transfer` alone excludes a row from income
   and spending (ADR 0022 rule 1), so unmarking restores the row exactly.
6. **Scheduled pairing needs provenance.** `pair_transfers`, `apply_transfer_pair` and
   `apply_transfer_link` raise unless `user_id` is set, or the session is household-wide **and**
   `job_run_id` is set. The audit detail of every automatic pair or link carries `job_run_id`.
7. **Locks decide races.**
   - Every writer of a rejection or a link first locks the transactions it names that the session
     can write (`_lock_group`, id order).
   - The automatic writers lock both legs, then re-check under the lock: live, not pending, not
     already a transfer, and no `pair` rejection for the edge or `leg` rejection for either leg.
   - Under READ COMMITTED the re-check sees any rejection that committed while the job waited.
   - A rejection that arrives later finds the legs already paired. It returns 409 and the person
     unmarks.
8. **Ownership changes dissolve first** (A1).
   - `PATCH /accounts/{id}` dissolves every pair touching the account in the same unit of work, and
     only then changes the owner. Each pair becomes two single legs, both still excluded, with no
     rejection. Once links exist, the pair also becomes a `person` link, so the counterpart
     survives.
   - A trigger, `account_guard_transfer_owner`, refuses an owner change while any row on the
     account still has a `transfer_group_id`. So the invariant holds in the database, not only in
     the service.
   - Claiming a joint account for one person needs the household-owner role, because it takes
     write access away from the other members.
9. **Deleting or superseding a leg keeps its partner whole.**
   - An `AFTER DELETE` trigger clears a deleted leg's partner (`is_transfer`, `transfer_group_id`).
     That's safe because both legs have one owner. The trigger refuses the delete if the partner
     survives the update. `unmark_transfer` accepts a group with one surviving leg.
   - A link's leg column is set NULL when its row is deleted, and the next refresh undoes a `job`
     mark on the survivor.
   - Superseding (`LedgerWriter.supersede_transaction`, the only writer of `superseded_by_id`)
     moves transfer state, a user category and splits to the successor. An `AFTER UPDATE` trigger
     re-points rejections, links and recurring members to it in the same unit of work, even ones
     the superseding session can't see. Supersession never leaves an account (0005), so
     visibility doesn't change.

## Alternatives considered
- **Cross-owner pairs with a shared group id:** the other member would see a pair they can't
  inspect or undo, which is the problem this ADR started from.
- **A `SECURITY DEFINER` "dissolve" function** that lets one leg's writer clear the other leg: it
  works, but it adds a privileged path that writes rows the caller can't see.
- **Only suggesting cross-owner moves (the first draft):** the most common household layout would
  count the card payment as spending until someone clicks, which fails acceptance.
- **Per-person pair visibility:** a pair is one fact about two rows. Showing it to one person and
  not the other makes metrics disagree.
- **Rejections in the audit log:** append-only and household-visible, so they can't be queried per
  person under RLS, and the matcher would have to scan the log.
- **Rejecting by amount and dates instead of row ids:** this survives re-imports that change ids.
  But ids are stable here (fingerprints, external ids), supersession re-points them, and a rule
  that broad could hide real transfers.
- **A `leg` rejection on every unmark (the first draft):** one wrong click would remove a row from
  matching for good.

## Consequences
- Links add one table (`transfer_link`) and two code paths: step 0's cleanup, and the
  `other_side_marked` suggestion.
- `TransactionOut.transfer_counterpart_id` shows the other leg of a pair or link only when the
  reader can see it. That's the "show the other side" link the first draft gave up for moves
  between owners.
- An account delete endpoint, when one is built, must unmark the other side of each of the
  account's links first, because the link row cascades away with the account.
- Savings: ADR 0022 rule 1 applies to income and spending. Rule 3 reads `savings`-kind outflows
  whatever their `is_transfer`. Pairing never changes categories, so a paired move into savings
  still counts as saving.
