---
name: phase-kickoff
description: Start a Holdfast build phase. Use when the owner says "start Phase N", "kick off phase", "next phase", or approves moving on. Reads the PLAN sections, checks the previous phase passed, creates the phase branch and a task plan.
---

# Phase kickoff

Arguments: phase number (e.g. `0`, `2`, `8.5`, `10a`).

1. **Gate.** Read `docs/STATUS.md`. The previous phase must be `PASS` (or `PASS-WITH-BLOCKED` with the owner's go-ahead noted). If it isn't, stop and say why.
2. **Read the spec.** PLAN.md Section 13 for this phase, every section it references (e.g. Phase 2 → 7, 3.2, 12), and **Section 20.3** amendments for this phase. Also read the relevant ADRs and `.claude/rules/` files.
3. **Branch.** From an up-to-date `main`: `git switch -c phase/<n>-<slug>`. Check for a clean working tree first.
4. **Plan.** Break the phase into ordered tasks (TodoWrite), with tests written alongside each. Flag anything that needs the owner (keys, real documents, hardware) as BLOCKED up front, and design so the rest can proceed with sandbox data and fakes.
5. **Decisions.** If the phase needs a choice the PLAN leaves open (library, layout, trade-off), decide it and write an ADR with the `adr` skill. Don't stop to ask unless it's irreversible or costs money.
6. **Update STATUS.** Set the phase to `IN PROGRESS` with today's date and the branch name. Commit: `chore(status): start phase <n>`.
7. Begin the first task.
