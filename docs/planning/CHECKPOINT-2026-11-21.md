# Keel checkpoint · 2026-11-21 (evening)

`main` is at `733132e`. Only Keel merges. No open PRs from this session.
Owner instruction this session: conduct Codex and Grok through `firstchair` alongside the Claude team; merge when the gates pass; the three new engines must become usable in the app.

## Merged this session (each: named reviewer sign-off bound to the SHA, `ci-required` green)

| PR | Story | Built by | Gate |
|---|---|---|---|
| #47 | C2 category tree | Ledger, Anvil (one fix) | Bulwark, Ledger review |
| #48 | I1b import date and duplicate fixes | Tide, Anvil (fix round) | Ledger review |
| #49 | I5 import screens | Prism, Anvil (two fix rounds) | Prism review |
| #54 | Web test waits raised to 5 s | Keel | Prism review |
| #55 | I1c stored review reason, migration 0009 | Tide | Bulwark, Ledger review |
| #51 | ADR 0021 revision 4 (Proposed) + PA0 spike results | Lattice, Harbor | Gadfly |
| #52 | C5a recurring detection (pure) | Anvil | Ledger review |
| #53 | C3a rules matcher (pure) | Anvil | Bulwark, Ledger review |
| #50 | C4 transfer pairing (not wired) | Ledger | Bulwark, Ledger review |
| #58 | C5a-2 recurring detection gaps | Anvil | Ledger review |
| #57 | I5c queue shows the stored reason | Anvil | Prism review |

## In flight
- **Phase 4 wiring contract** (Lattice, own worktree, branch `docs/phase4-wiring-contract`, no PR yet): schema and migration order, who runs each engine, API, screens, story slices, and an ADR for the rules regex engine. ADR numbers start at 0024 (0023 belongs to PR #56). When it lands: Gadfly challenges it, Bulwark reads the RLS and regex parts, then build the slices in its order.

## Other lane (not Keel's)
A second session ("holdfast-fa") owns the Plaid lane by owner assignment: PR #56 (`feat/P3-plaid-client-preflight`, ADR 0023), a stacked local branch `feat/P9a-plaid-setup-page`, and a design round on in-app Plaid key entry that may amend ADR 0015 and touch PR #31 (F5c). Keel does not merge #56 or decide that question until that lane says so. Told it: the migration token is free; nobody here drives #31. Coordinate before either side takes a migration number.

## The three engines cannot run yet (owner priority)
- `app/domain/rules.py`: no storage, API or screen. **No user-written pattern may be saved or run** until an ADR picks a linear-time regex engine: a validated pattern still takes about 0.95 s per match (`A?A?A?A?A*AAAA…AB`). C3b must also check category and account ownership and run in the author's person-scoped session.
- `app/domain/recurring.py`: no storage, job or screen. Consumers must treat variable weekly, biweekly and semi-monthly series as spending cadence, not bills, and require confidence above 0.60 for annual pairs. About 7 s per 50,000 rows: off the event loop.
- `app/domain/transfers.py` + `app/services/transfer_pairing.py`: a guard test fails if anything under `app/api`, `app/jobs`, `app/worker`, `app/agent` or `main.py` calls it. C6 must first store suggestions and ambiguous legs per person (RLS), add a pair-level "not a transfer" record, require `job_run_id` when there is no user, and let either owner dissolve a pair touching their leg.

## Follow-ups queued
- Import: freeze `review_reason` in `import_run_guard` (own migration); tell the caller when a different profile was ignored; an audited "re-import this document" action (a document first imported under the wrong profile cannot be repaired today); the other-profile check when every earlier run is hidden from the caller; balance-only documents stay latched with nothing that shows or clears them; year ambiguity in periods of 11 months or more; wire the CSV and OFX parsers (I2a) into the import service.
- Web: a balanced run with no reason reads "From the figures: No reason could be determined"; the result card shows an unknown code verbatim; Retry in the Needs review section shows no error text beside it; the shell remounts a page across the desktop/mobile breakpoint and drops the import result card.
- Guards: fail on any use of the string `app.household_wide` outside `db_context.py` and migrations, and on `household_wide=` with anything but `False` under `app/api/`.
- ADR 0021 before Accepted: fresh-node sign-in can stall (allow sign-in when no profile exists, `up --reset`, extend U-11); a tripwire should latch, not log the node out; §9 and §2 contradictions; Bulwark review.
- Docs are stale (Grok audit, `<keel-state-dir>`): `docs/STATUS.md` says Phase 1.5 is not started; it is in progress (blocked on F5 and the free-tier half of F7), Phases 3 and 4 are in progress on trunk, Phase 2 has not started. `docs/planning/SPRINTS.md` still describes the 2026-11-19 checkpoint. Dependencies with no ADR naming them: `pdfplumber`, `python-multipart`, `uqr`, `reportlab`, `hypothesis`. Update both files in one docs PR.
- From earlier: ADR 0020's "one person can never hold both slots"; per-household upload limit before Cloud; killable parse subprocess; orphan-file sweep; `agents@` in `docs/ops/domain-email-pages.md`.

## Owner decisions waiting
- **Live instance:** still on the pre-#47 build. Bringing it to `main` rebuilds the stack and applies migrations 0008 and 0009 to the database holding real data. Keel asked for a go-ahead (dump to the private directory first); no answer yet. Do not do it without one.
- ADR 0021 owner checks: node re-auth and removal in the Tailscale console (check 6), "HTTPS disabled" text on a fresh account (check 7), Safari and Edge passkeys (check 10). Steps are in `docs/adr/reviews/0021-pa0-spike.md`.

## Housekeeping
- Spike leftovers: containers `hfspike-ts`, `hfspike-up`; volumes `hfspike_flag`, `hfspike_sock`, `hfspike_ts_state`, `hfspike_up`; network `hfspike-net`; image `hfspike-tcpdump:latest`. Remove once the owner checks no longer need the test node.
- Worktrees of merged branches that can go: `ledger-c2`, `tide-i1b`, `prism-i5`, `tide-i5b`, `keel-i2a`, `ledger-c1`, `u1-transactions`, `keel-apphost`, `keel-c5a`, `keel-c3a`, `keel-c5c`, `keel-w1`, `keel-flake`, `lattice-access`, `agent-a1f084e5e5564964c`, `agent-ab135e721aad5440e`. No `firstchair` delegate worktrees remain.
- Briefs, patches, worker reports and `land.sh` are in `<keel-state-dir>` (the session scratchpad does not survive a reboot).

## Tooling notes
- `firstchair delegate` dispatches only Codex and Grok and bases the worktree on the checkout it runs from: pass `--repo .claude/worktrees/<branch-worktree>` for work on a PR branch. `finish` needs the same `--repo`, and `--session <id>` (placed before `finish`) when the job was started by an earlier session. Do not delete `.firstchair/` in that checkout first.
- Codex cannot run database tests or `pnpm`. Keel applies the patch (`git -C <delegate worktree> diff | git apply --index`) and runs the suite. Its self-reported test scores were perfect three times and wrong each time against an independent holdout: always have the reviewer rerun its own data. Review its diff for size and for names main has since changed.
- Gate sign-offs come from the project's own reviewers. A Grok review is a second opinion and does not satisfy `review-gate`. Grok's read-only recon was accurate (web test timeouts, the flaky import test, the status audit).
- A new worktree has no `web/node_modules`: run `pnpm install --frozen-lockfile` in `web/` before `make test` or `make lint`, or both fail on missing tools.
- Tests: `ln -s <repo>/.env .env` (never read; remove after), `COMPOSE_PROJECT_NAME=holdfast make TEST_DB=holdfast_test_<name> test-locked`, `make lint`. The local suite now runs the Alembic drift check.
- Landing: `<keel-state-dir> <pr> <worktree> <branch> <reviewed-sha> <subject> <body-file>` merges main, proves the PR diff equals the reviewed diff, scans, pushes, waits for CI and squash-merges. If the merge conflicts, resolve by hand, compare added and removed lines with the reviewed diff, rerun the suite, and re-add `bulwark-approved` after the push.
- Measured this session: Codex ran 10 build tasks (about 6.4M input tokens, mostly cached re-sends, 71k output). Claude builders 65k to 175k each; reviews 18k to 146k each. Codex-built stories needed 2 to 3 review rounds each; so did Claude-built C4.
