---
name: phase-review
description: Close out a Holdfast build phase. Use when phase work looks done, or the owner asks "is phase N done", "wrap up the phase" or "phase report". Runs reviewers and acceptance checks, writes the phase report, updates STATUS and stops for owner review.
---

# Phase review

1. **Self-check.** Working tree clean, all phase tasks done, and no `TODO`/`FIXME` added this phase without an issue note.
2. **Reviewers (in parallel).** Launch the ones that apply to what changed (`git diff --stat main...HEAD`):
   - `security-reviewer`: always.
   - `money-invariants-reviewer`: backend domain, importers, connectors, metrics, or any amounts in the UI.
   - `tax-config-verifier`: any tax YAML.
   - `crypto-custody-auditor`: any crypto path.
   - `ui-brand-reviewer`: any `web/` or `site/` change.
   Fix critical and high findings, then rerun the affected reviewer. Record medium and low ones in the report.
3. **Acceptance.** Run the `phase-acceptance-checker` agent for the phase. It must come back PASS or PASS-WITH-BLOCKED.
4. **Report.** Write `docs/phases/PHASE-<n>.md`:
   - Summary (what was built, in 3–6 bullets)
   - Acceptance table (from the checker, with real evidence)
   - Reviewer findings and dispositions
   - ADRs written this phase
   - BLOCKED items and exactly what the owner must do
   - Known gaps / follow-ups
   - How to demo it (commands)
5. **STATUS.** Update `docs/STATUS.md` (phase → `PASS` / `PASS-WITH-BLOCKED`, date). Commit: `docs(phase-<n>): phase report`.
6. **Stop.** Tell the owner the verdict, the demo commands and the blocked items. Ask for approval to merge to `main` (`git merge --no-ff`, tag `phase-<n>`). Don't merge or start the next phase without it.
