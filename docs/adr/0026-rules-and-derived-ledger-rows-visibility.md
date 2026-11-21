# 0026. Who sees and who writes rules, transfer suggestions and recurring series

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect); Keel. Amends ADR 0008 (adds per-person policies for `rule` and
  four new tables).

## Context
Phase 4 adds rows that are computed from transactions: transfer suggestions, "not a transfer"
corrections, recurring series and their members. It also makes `rule` a real feature. ADR 0008
gives transactions per-person visibility through their account, but `rule` has household
isolation only. Reviewers set preconditions: a rule's category and accounts must belong to the
author's household and visible accounts (PR #53); suggestions must be stored per person with an
RLS check, and a household-wide matcher report must never reach an API (PR #50).

Two leaks are easy to build by accident:
- A derived row computed household-wide can reveal private rows by its existence or its contents:
  a suggestion marked "ambiguous" tells a viewer that a hidden equal-amount row exists.
- A stored counter on a shared row (`rule.hit_count`) counts rows on private accounts.

## Decision
1. **Two SQL helpers hold the account predicates** for every new policy (the first Phase 4 migration, M1 in the contract):
   `app_account_details_visible(account_id uuid)` and `app_account_writable(account_id uuid)`,
   `STABLE`, `SECURITY INVOKER`, `search_path = pg_catalog, public, pg_temp`. Their bodies are
   ADR 0008's `ACCOUNT_DETAIL_VISIBLE` and `ACCOUNT_DETAIL_WRITABLE` predicates (the `account`
   subquery stays under account RLS). A test runs both helpers and the `transaction` policies for
   every person/account combination in the RLS fixture and requires them to agree. Existing
   policies are not rewritten.
2. **A derived row is visible only if every transaction it references is visible.** Derived rows
   carry the `account_id` of each referenced transaction, tied to it by a composite FK
   `(transaction_id, account_id) -> transaction(id, account_id)` (that unique key exists), so the
   policy checks the accounts directly and cannot be lied to. Read policies are RESTRICTIVE and
   use only the helpers.
3. **Rules are household-readable, author-writable, and only touch rows their author may write.**
   - `rule.author_person_id` (composite FK to `person`, `ON DELETE CASCADE`; never `SET NULL`,
     which would turn a personal rule into nobody's).
   - `rule.account_scope` is `any` (no account filter), `joint` (only joint accounts) or `own`
     (only the author's own accounts). The filter lives in `rule_account` with composite FKs to
     `rule` and `account`. A trigger rejects a `rule_account` row whose account does not fit the
     scope, and `any` has none.
   - Read: `app_household_wide() OR author_person_id = app_person_id() OR account_scope IN
     ('any','joint')`. An `own` rule names the author's accounts, which may be private, so only
     its author sees it. Joint accounts are visible to everyone, so `joint` rules are safe to show.
   - Write (INSERT, UPDATE, DELETE): `app_household_wide() OR author_person_id = app_person_id()`.
   - **Effect:** a rule applies to a transaction only if the transaction's account is joint or
     owned by the rule's author. This is enforced in the pure engine (`app/domain/rules.py`,
     `rule_may_touch`), so the household-wide import path and a person's preview make the same
     decision. Because of the scope rule, every rule that can touch a row a person may write is
     also readable by that person, so the person's preview sees the same rule set the job uses.
   - **No stored hit counter.** `rule.hit_count` is dropped. `transaction.category_rule_id`
     records which rule set a category, and the hit count is `count(*)` under the reader's RLS.
4. **Transfer suggestions are computed per person, from that person's rows, in that person's
   session**, and stored with `person_id`. Read: own `person_id` (or household-wide) **and** both
   legs' accounts detail-visible. A person's suggestion can therefore never be shaped by a row
   that person cannot see (no "ambiguous because of a hidden row"). The household-wide matcher
   pass only auto-pairs; its report is never stored or returned.
5. **Corrections and series are visible through their accounts.** `transfer_rejection` is
   readable when both legs are detail-visible and writable when either leg's account is writable.
   `recurring_series` and `recurring_member` are readable when the series account is
   detail-visible. Detection columns are written only household-wide; a person who may write the
   account may change only `user_state` and `label` (a guard trigger, since both writers are
   `holdfast_app` and column grants cannot tell them apart).
6. **Summary-only viewers see none of these rows**, the same as transactions.

## Alternatives considered
- **Personal rules (author-only read):** a person previewing a joint account could not see the
  other person's rules that also apply there, so the preview count would differ from what the
  ingest path applies.
- **Fully shared rules with household-wide effect:** one person's rule would rewrite another
  person's private rows, which ADR 0008 makes read-only to everyone but the owner.
- **Household-level suggestions filtered in the UI:** the reason and the grouping still leak
  hidden rows, and the brief forbids UI-only filtering.
- **Keeping `hit_count` and updating it on apply:** leaks private row counts and is a hot row.
- **Rewriting the 0002 policies to call the helpers:** one definition, but it touches every
  detail-table policy in a protected migration for no behaviour change. The equivalence test
  gives the single source of truth at lower risk.

## Consequences
- ADR 0008's table gains: `rule` (read and write as above), `rule_account`, `transfer_suggestion`,
  `transfer_rejection`, `recurring_series`, `recurring_member`.
- An account that changes owner (a joint account claimed) can leave a `joint` rule naming a now
  private account. The rule stops touching it (the engine checks the owner at match time) and
  its author may stop seeing that `rule_account` row. Accepted. The future claim path should
  offer to edit such rules.
- Job handlers that store per-person rows open extra `scoped_session(household, person)` sessions
  from `JobContext.factory`. ADR 0016's "person-scoped jobs" are still not needed.
