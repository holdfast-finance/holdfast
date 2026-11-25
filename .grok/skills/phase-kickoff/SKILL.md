---
name: phase-kickoff
description: >
  Start a Holdfast build phase under the current SDLC. Use when someone says
  "start Phase N", "kick off phase", "next phase", or approves moving on.
  Reads PLAN.md, checks the previous phase, writes the contract and story
  briefs, and dispatches lane agents. Does not open a long-lived phase branch
  and does not ask the owner for a merge. Trigger: /phase-kickoff.
---

# Phase kickoff

Arguments: phase number (`0`, `2`, `8.5`, `10a`).

Follow `docs/process/SDLC.md`. Phases are milestones. Stories are short-lived branches off `origin/main`. Only a session explicitly seated as Keel merges.

1. **Gate.** Read `docs/STATUS.md`. The previous phase must be `PASS`, or `PASS-WITH-BLOCKED` with the go-ahead already noted. If it is not, stop and say why.
2. **Read the spec.** PLAN.md Section 13 for this phase, every section it references, and the Section 20 amendments for this phase. Also read the relevant ADRs and the matching `.claude/rules/` files. Use line ranges. PLAN.md is long.
3. **Contract.** If `docs/phases/PHASE-<n>-contract.md` is missing or stale, spawn `lattice` to write it. For a new epic, spawn `gadfly` on that contract before build runs start.
4. **Stories.** Break the phase into S or M stories. Write each brief to `docs/planning/briefs/<id>.md` (files, tests, lane, acceptance). L stories get split before they are scheduled. Flag owner-queue items (keys, real documents, hardware) as blocked up front and design the rest around sandbox data.
5. **Decisions.** If the PLAN leaves a choice open, decide it and write an ADR with the `adr` skill. Ask only when the action is irreversible or costs money. Those items go to the owner queue.
6. **STATUS.** Set the phase to `IN PROGRESS` with today's date. The branch column stays `trunk`. Commit: `chore(status): start phase <n>`.
7. **Dispatch.** Spawn the lane agent for the first ready story on its own branch. Create worktrees with `scripts/wt-new.sh` when that script exists. Do not open `phase/<n>-<slug>`.
