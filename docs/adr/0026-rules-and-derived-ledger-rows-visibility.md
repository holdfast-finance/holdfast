# 0026. Who sees and who writes rules, transfer suggestions, links and recurring series

- Status: Accepted
- Date: 2026-11-21 (revised three times the same day after the Gadfly and Bulwark reviews of PR #59)
- Deciders: Lattice (architect); Keel. Amends ADR 0008 (adds per-person policies for `rule` and
  five new tables).

## Context
Phase 4 adds rows that are computed from transactions:
- transfer suggestions
- "not a transfer" corrections
- links between the two single legs of a move between owners
- recurring series and their members

It also makes `rule` a real feature. ADR 0008 gives transactions per-person visibility through
their account, but `rule` has household isolation only.

Reviewers set preconditions:
- A rule's category and accounts must belong to the author's household and visible accounts
  (PR #53).
- Suggestions must be stored per person with an RLS check, and a household-wide matcher report must
  never reach an API (PR #50).

Two leaks are easy to build by accident:
- A derived row computed household-wide can reveal private rows by its existence or its contents.
  A suggestion marked "ambiguous" tells a viewer that a hidden equal-amount row exists.
- A stored counter on a shared row (`rule.hit_count`) counts rows on private accounts. So does an
  audit row with a count.

## Decision
1. **Two SQL helpers hold the account predicates** for every new policy (M1 in the contract):
   - `public.app_account_details_visible(uuid)`
   - `public.app_account_writable(uuid)`

   Their form (Bulwark):
   - `LANGUAGE sql STABLE SECURITY INVOKER`, with a SQL-standard body (`RETURN EXISTS (...)`)
   - schema-qualified names
   - **no `SET` clause**

   They bind when created, stay inlinable into the policies that call them, and can't be hijacked
   through `search_path`. Their bodies are ADR 0008's `ACCOUNT_DETAIL_VISIBLE` and
   `ACCOUNT_DETAIL_WRITABLE` predicates, and the `account` subquery stays under account RLS.

   Tests:
   - Both helpers and the `transaction` policies run for every person/account combination in the
     RLS fixture, and they must agree.
   - `prosecdef = false` for both.

   Existing policies aren't rewritten.
2. **A derived row is visible only if every transaction it references is visible.**
   - Each derived row carries the `account_id` of each referenced transaction.
   - The account id is tied to the transaction by a composite FK `(transaction_id, account_id) ->
     transaction(id, account_id)` (unique since 0005), and to the household by `(account_id,
     household_id) -> account(id, household_id)`.
   - So the policy checks the accounts directly and can't be lied to.
   - Read policies are RESTRICTIVE and use only the helpers.
3. **Rules are readable by scope, writable by their author, and only touch rows their scope
   allows.**
   - `rule.author_person_id`: a composite FK to `person`, `ON DELETE CASCADE`. Never `SET NULL`,
     which would turn a personal rule into nobody's. The author is fixed for the rule's life (a
     trigger).
   - `rule.account_scope`:
     - `any` ("joint and my accounts"): touches rows on joint accounts and the author's own
     - `joint`: joint accounts only
     - `own`: the author's own accounts only

     **There are no per-rule account lists in Phase 4.** Scope alone filters, so an account added
     later is covered by its kind.
   - **A rule made from a row on a non-joint account defaults to `own`**, and so does a rule made
     from Settings. A rule made from a joint row defaults to `any`. The rule sheet always states who
     can see the rule.
   - Read: `app_household_wide() OR author_person_id = app_person_id() OR account_scope IN
     ('any','joint')`.
   - Write (INSERT, UPDATE, DELETE): `app_household_wide() OR author_person_id = app_person_id()`.
   - **Effect:** a rule applies to a transaction only if `rule_may_touch` allows it, which checks
     the account's owner at match time.
     - It lives in the pure engine (`app/domain/rules.py`), so the household-wide import path and a
       person's preview make the same decision.
     - Every rule that can touch a row a person may write is also readable by that person: the
       scope rule allows only joint rows and the author's rows. So the person's preview sees the
       same rule set the ingest path uses.
   - **No stored hit counter.** `rule.hit_count` is dropped. `transaction.category_rule_id` records
     which rule set a category, and the hit count is `count(*)` under the reader's RLS.
   - **No counts in shared audit rows.** `rule.applied` records `{rule_id}` only. An `any` or `own`
     rule touches the author's private rows, and `audit_log` is household-readable. `rule.*` details
     carry ids and changed field names only.
4. **Transfer suggestions are computed per person, from that person's rows, in that person's
   session.** They're stored with `person_id`.
   - Read: the person's own `person_id` (or household-wide) **and** both legs' accounts
     detail-visible.
   - A suggestion can't be shaped by the *presence* of a row the person can't see in the person's
     own pass: no "ambiguous because of a hidden row".
   - **Step 2 never writes a pair or a link.** An edge that looks certain in a person's view, but
     that household-wide step 1 didn't take, is stored as a `likely` suggestion (Gadfly).
   - The household-wide pass only pairs and links. Its report is never stored or returned.
   - **Accepted leftover inferences** (Bulwark):
     - (a) A `likely` suggestion between two accounts the person can see, both hinted, tells them
       that step 1 found a competitor they can't see: an equal-amount row on a hidden account.
     - (b) A leg that step 1 marked as a transfer with no counterpart the person can see (a hidden
       link) tells them money moved to or from an account hidden from them. So does a **leg offer**
       ("another household member marked the other side"): the second person's half of a move the
       first accepted.
     - (c) A shared leg's `transfer_group_id` tells another member the same (ADR 0027 point 1).
     - (d) The household-wide cap of 25 enabled pattern rules (ADR 0024 point 8) is counted across
       people. A 409 at the cap lets one person learn how many enabled pattern rules exist in total,
       including another person's `own` ones. It never shows their names, patterns or effects
       (Bulwark).

     (a)–(c) each reveal that an equal amount exists somewhere, never which account, row, date or
     description, and the amount is already on the visible row. (d) reveals a count. We accept
     these. The alternative is leaving moves between owners counted twice, or a per-person cap that
     doubles the worst case.
   - **Leg offers** (`transfer_leg_offer`) are rows for one person that name only that person's own
     leg, which they can write. Step 0 creates them household-wide, so the person never reads the
     link or the other leg.
5. **Corrections, links and series are visible through their accounts.**
   - `transfer_rejection` and `transfer_link` are readable when both legs are detail-visible, and
     insertable when that holds and either leg's account is writable.
   - A rejection can be deleted under the same rule. A link can be deleted only household-wide.
   - A person may change one thing about a link: its `origin`, from `job` to `person` (a guard
     trigger).
   - `recurring_series` and `recurring_member` are readable when the series account is
     detail-visible.
   - Detection columns are written only household-wide. A person who may write the account may
     change only `user_state` and `label`. A guard trigger enforces this, because both writers are
     `holdfast_app` and column grants can't tell them apart. The UPDATE policy has both USING and
     WITH CHECK.
   - Some trigger work must see rows the caller can't: the supersede re-point, the `rule_caps`
     count, the account owner guard, the orphan-pair check. These triggers follow the 0002/0005 pattern: they switch
     `app.household_wide` on for their own fixed statements and restore it. Those statements touch
     only rows named by the triggering row.
6. **Summary-only viewers see none of these rows**, the same as transactions.

## Alternatives considered
- **Personal rules (author-only read):** a person previewing a joint account couldn't see the
  other person's rules that also apply there, so the preview count would differ from what the
  ingest path applies.
- **Fully shared rules with household-wide effect:** one person's rule would rewrite another
  person's private rows, which ADR 0008 makes read-only to everyone but the owner.
- **Per-rule account lists (`rule_account`):** precise, but it's one more table and two scope
  triggers. Accounts added later would be silently left out, and no real data needs it yet. It's an
  additive migration if it's ever needed.
- **Household-level suggestions filtered in the UI:** the reason and the grouping still leak
  hidden rows, and the brief forbids UI-only filtering.
- **Writing the per-person pass's confident pairs:** a pair would rest on one person's partial
  view. That's exactly what household-wide pairing exists to avoid.
- **Keeping `hit_count` and updating it on apply:** it leaks private row counts and is a hot row.
- **Rewriting the 0002 policies to call the helpers:** one definition, but it touches every
  detail-table policy in a protected migration for no change in behaviour. The equivalence test
  gives a single source of truth at lower risk.
- **`SECURITY DEFINER` helpers with a `SET search_path`:** they'd run as the owner, aren't
  inlinable, and a later edit could widen them. Invoker helpers with a SQL-standard body bind at
  creation.

## Consequences
- ADR 0008's table gains `rule` (read and write as above), `transfer_suggestion`,
  `transfer_rejection`, `transfer_link`, `recurring_series` and `recurring_member`.
- **`any` and `joint` rules, their name and conditions included, are readable by every household
  member.** A merchant key or a pattern copied from a person's row shows what they buy. That's why
  a rule made from a private row starts as `own`, and why the sheet says who can see it.
- An account that changes owner (A1) can stop matching a rule's scope. The rule just stops touching
  it: the engine checks the owner at match time. Nothing needs rewriting.
- Job handlers that store per-person rows open extra `scoped_session(household, person)` sessions
  from `JobContext.factory`. ADR 0016's "person-scoped jobs" are still not needed.
