---
name: phase-review
description: >
  Close out a Holdfast build phase under the current SDLC. Use when phase work
  looks done, or someone asks "is phase N done", "wrap up the phase", or
  "phase report". Runs the path reviewers and Sounding, writes the phase
  report, updates STATUS, and hands the packet to Keel. Does not merge and
  does not ask the owner for approval. Trigger: /phase-review.
---

# Phase review

1. **Self-check.** Working tree clean. Phase stories merged or listed as blocked. No new `TODO` or `FIXME` without an issue note.
2. **Reviewers, in parallel.** Spawn the ones that apply to `git diff --stat origin/main...HEAD`. Give each the head SHA. Spawn them so they cannot edit (capability `execute`, or `/holdfast-review`).
   - `security-reviewer`: always
   - `money-invariants-reviewer`: domain, importers, connectors, metrics, or amounts in the UI
   - `tax-config-verifier`: any tax YAML
   - `crypto-custody-auditor`: any crypto path
   - `ui-brand-reviewer`: any `web/` or `site/` change
   Fix critical and high findings on a new commit, then rerun the affected reviewer. The new SHA invalidates the old sign-off. Record medium and low findings in the report.
3. **Acceptance.** Spawn `phase-acceptance-checker` for this phase. It must return PASS or PASS-WITH-BLOCKED, with command evidence.
4. **Report.** Write `docs/phases/PHASE-<n>.md`:
   - what was built, in 3–6 bullets
   - the acceptance table, with the checker's evidence
   - reviewer findings and what was done with them, each bound to a SHA
   - ADRs written this phase
   - blocked items and the exact owner-queue action
   - known gaps
   - demo commands
5. **STATUS.** Set the phase to `PASS` or `PASS-WITH-BLOCKED` with today's date. Commit: `docs(phase-<n>): phase report`.
6. **Hand off.** Tell Keel the verdict, the demo commands, the blocked items, and the commit to tag. Do not merge. Do not push a tag. Do not ask the owner to approve the merge. A session explicitly seated as Keel may squash-merge remaining story PRs and, after Sounding's PASS, tag `phase-<n>` on that green `main` commit.
