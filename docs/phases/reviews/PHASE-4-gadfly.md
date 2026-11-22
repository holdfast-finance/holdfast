# Gadfly critique: Phase 4 wiring contract

- Target: `docs/phases/PHASE-4-contract.md` and ADRs 0024 and 0026–0029 at
  `0fab87fc95c25d942f39a21952609cedb7d78239` (branch `docs/phase4-wiring-contract`, PR #59)
- Inputs:
  - the docstrings of `domain/rules.py`, `recurring.py`, `transfers.py` and `services/transfer_pairing.py`
  - the gate comments on PRs #50, #52, #53 and #58
  - PLAN 8.2–8.6 and 13 Phase 4
  - ROADMAP E4
  - ADRs 0008, 0016 and 0022
- Verdict: **REWORK.** Four holes would become code as written:
  - the cross-owner default breaks acceptance criterion 2 and double-counts money
  - supersession undoes people's corrections
  - the regex gate sits in the API only
  - C7 has no evidence plan

Severity: **blocking** means fix it before merge. **Should** means fix it in this revision. **Minor**
means the builder's PR can settle it, as long as the contract names the answer.

## Blocking

**B1. Treating a move between two people's accounts as two single legs breaks acceptance criterion 2
and double-counts (owner decision 2, D7, ADR 0027 points 1–2, §5.3, §2.4).**
- **Joint versus owned counts as different owners** (§2.4 `_suggest_reason`). So the most common
  household card payment, joint checking paying one person's card, is **never auto-paired**. It's only
  suggested. Statement-only data has no Plaid PFC to fall back on, so until someone clicks, the
  checking outflow counts as spending *on top of* the card's purchases. That's exactly what acceptance
  criterion 2 forbids.
- **Private to private is never shown to anyone.** Suggestions need both legs detail-visible to the
  person (ADR 0026 point 4). If A's private account pays into B's private one, neither person sees both
  legs, so no suggestion exists. Both legs stay counted for good: household spending goes up by X and
  income goes up by X.
- **A half-done cross-owner accept hides the other half.** Accept marks only the legs the clicker may
  write (§5.3). The other person's copy of the suggestion is then filtered out, because §5.3 hides
  suggestions whose "legs are not transfers" and ADR 0029 hides them once a leg "has become a
  transfer". So the second leg is orphaned. One leg stays counted, which puts net cash flow off by X.
  This contradicts ADR 0027 point 2's "each leg's writers can mark their own leg".
- **Unmarking a single leg writes a permanent `leg` rejection** (§4, ADR 0027 point 3). Cross-owner
  moves are now always single legs, so correcting a mistake there removes the row from matching for
  good.

Fix:
- In `ledger.refresh` step 1, a household-wide pass marks **both legs** of a confirmed, hinted
  cross-owner edge as single-leg transfers with `job_run_id`. That uses the same evidence bar as
  auto-pairing, and each person can still undo their own leg.
- Record a `counterpart_id`, visible only when both legs are visible. Unmarking then writes a `pair`
  rejection, not a `leg` one.
- Hide a review card only when every leg the reader can write is already a transfer.
- State the small leak (B learns that an equal outflow exists somewhere) and accept it in ADR 0027.
- The C7 holdout must include joint checking paying a personally owned card.

**B2. Supersession breaks "ids are stable" (ADR 0027, Alternatives; §4 "Rows a person has touched").**
ADR 0017 supersession already exists (`superseded_by_id`, `_require_live`), and I4's "Plaid wins" makes
it routine. When a statement row is superseded, everything a person attached to it stays on the
superseded row:
- a user category or a release
- splits
- a transfer mark or group
- a `transfer_rejection`
- a recurring `confirmed`, `dismissed` or `label`

The live survivor starts clean, so a pair someone unmarked gets **re-paired** and their categories
disappear. The contract never mentions this.
Fix: in the same unit of work as the supersede, `LedgerWriter` carries these to the survivor:
- user-source category and release state
- transfer state
- rejections, with their ids rewritten
- recurring membership and user state

Add a test: unmark, then supersede, then refresh, and nothing is re-paired.

**B3. The regex gate exists only in the API, and `re` is still reachable (§2.2, §5.2, ADR 0024 point
7).**
- C3c moves the C3a validator into `rule_regex.py` "unchanged", so `compile_pattern` keeps compiling
  with Python `re` until C3d.
- `DescriptionCondition` regex mode calls it. So does the ingest path (C3e), and so does
  `conditions_from_json` on any stored row.
- The DB CHECK (§3.1) doesn't forbid `mode: "regex"`. Any non-API writer can store one: a CLI, a
  backfill, or Phase 8's agent-authored rules.
- §5.2 doesn't say whether `POST /rules/preview` rejects regex. Preview runs the pattern over about
  50,000 rows, at about 0.95 s per row for the PR #53 pattern.

Fix:
- In C3c, `compile_pattern` **raises unconditionally**. No `re` import is allowed in `rule_regex.py`,
  enforced by a test.
- State that the 422 covers preview, create and PATCH.
- The ADR 0024 fallback ("stored but skipped and flagged") needs a real column or code path.

**B4. Acceptance criteria 1 and 3 have no evidence plan (Phase 4 acceptance; §7 C7 row).**
- C7 is one line ("the four acceptance numbers on the holdout seeds").
- The C1 "synthetic generator with hidden holdout seeds" isn't in the repo. The only holdouts cited are
  reviewers' own (PRs #52 and #58).
- The ML classifier is cut and statement rows carry no Plaid PFC. So "90% after about 200 labels"
  depends entirely on how a simulated person turns labels into rules, and nothing defines that.
- Also undefined:
  - the denominator: are transfers, pending, superseded and split rows included?
  - precision: is it slug equality?
  - who holds the seeds
- Counting both recurring tiers, with no cap on false series, inflates recovery. It also makes §8's
  PR #52 trigger ("a detector follow-up only if C7 misses 95%") effectively unreachable.

Fix: add a §7.2 "C7 evidence" that specifies:
- the generator's module and seed custody
- the labelling simulation: 200 labels, with a "make a rule" offer per label, accepted under a stated
  policy
- the denominators and precision
- the share of Plaid-sourced rows (zero today)
- the account owners in the holdout (B1)
- recovery for the `bill` tier only, alongside the combined figure
- a false-series cap per tier

## Should

**S1. Owner decision 1 leaks private merchants and leaves the other person stuck (D2, ADR 0026 point
3, §6.2).**
- An `any` rule that A makes "from this transaction" on a private row shows its merchant key, pattern
  and amount band to B. That reveals what A buys even though B can't see the row.
- `any` actually means "joint plus mine", but the name doesn't say so.
- `joint` and `own` need listed account ids, so accounts added later are silently left out. The UI's
  "joint" and "mine" choices suggest the opposite.
- B can read A's rule but can't stop it recategorising joint rows on every import.

Fix:
- A rule made from a private-account row defaults to `own`.
- An empty list means "all current and future accounts of that kind".
- Rename `any` to "joint and mine".
- Let any writer of a joint account turn off a rule's effect on that account.

**S2. The slice order and slice sizes (§7 "Soonest visible", §7 table).**
- C6a doesn't need C3b. Set and release exist today, only `category_rule_id` needs M1, and C6a needs
  only `merchant.py` from C3c.
- The shortest path is `merchant.py` (a tiny pure slice), then C6a without `category_rule_id`, then
  W1. C3b runs in parallel, and `category_rule_id` ships in C3e. That takes the heaviest Bulwark RLS
  gate off the first visible path.
- Say plainly that after W1 every statement row shows "Needs category". The value is manual
  categorisation only.
- **C4c** (migration, writer, pairing, two endpoint blocks and a new service) and **C5b** (migration,
  service, job step, process pool and endpoints) are each too big for one PR. Split each into a db/writer
  PR and an API PR.

**S3. Reviewer preconditions that are dropped or appear only in the table (§8).**
- **PR #58 is missing from §8 entirely.** Unanswered: "an annual pair's typical amount should be the
  newer charge", "annual pairs that change by about 20% are missed", and the 11 ad-hoc purchases
  admitted as members.
- The PR #50 grep-guard tightening exists only as a table row and a C4d cell. Name the allowed files.

**S4. `merchant_key` is versioned for recurring but not for rules (D13, §2.1, §2.3).** A
`MERCHANT_KEY_VERSION` bump silently changes what every stored `merchant_key` rule matches. Fix: put
`merchant_key_version` in the conditions JSON, and make a bump ship a rewrite migration.

**S5. A retroactive apply can't be undone and keeps no history (§4 `apply_rule_bulk`, §10).** The audit
row records only `{rule_id, count}`, so the Plaid or rule categories it overwrote are lost. Fix: store
each row's prior `(category_id, category_source, category_rule_id)` in an apply record. That makes undo
possible later.

**S6. Contradictions with the spec.**
- **Savings.** ADR 0022 metrics rule 3 counts a `savings`-kind outflow as saving. Auto-pairing sets
  `is_transfer`, and rule 1 then excludes it, so a paired checking-to-savings move drops out of the
  savings rate. Decide which wins.
- **Rules versus transfers.** PLAN 8.2 says user rules come before transfer detection ("first hit
  wins"). D5 and ADR 0027 point 4 still auto-pair rows a rule categorised. Record that deviation.
- **Recurring grouping.** Series are per account (the detector keys on account, and so do
  `recurring_member` FKs). PLAN 8.4 groups by merchant. A reissued card or a bill that moves accounts
  splits the series and loses the person's confirmation and label.

**S7. Places a builder would have to guess.**
- **PATCH.** If conditions or category change, rows the rule set keep a stale `category_rule_id`, so
  "Set by rule X" shows the wrong reason.
- **Same category, different source.** If a row's category already matches but its source differs
  (`plaid` to `rule`), does that count as a change?
- **Disabled rules.** Can a disabled rule be applied?
- **Refresh coalescing.** Keys are per document, so uploading 30 statements queues 30 full-history
  runs.
- **Check again.** `refresh:manual` is a no-op for up to 5 minutes after a successful run.
- **Rule limits.** Is the 200-rule cap per author or per household, and do disabled rules count?

## RE2 (question 6)

The single binding is acceptable, given B3's domain-level gate. The exit isn't defined, though:
- Name an owner (Harbor) and the triggers:
  - a Python bump with no wheel
  - a `pip-audit` finding with no fix
  - 18 months with no release
- The cheapest exit is an `any_of` literal-list mode. It covers the motivating example
  (`AMZN MKTP|AMAZON\.COM`) with no engine at all.

## Weakest decision

**D7 / owner decision 2.** Its default fails acceptance criterion 2 for the most common household
layout, joint checking paying a personal card. It also leaves private-to-private moves counted twice,
as both spending and income, with no list showing them to anyone.
