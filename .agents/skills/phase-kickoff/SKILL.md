---
name: phase-kickoff
description: "Start a Holdfast build phase. Use when the owner says \"start Phase N\", \"kick off phase\", \"next phase\", or approves moving on. Reads the PLAN sections, checks the previous phase passed, prepares story briefs and a task plan."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# Phase kickoff

1. Read the phase checkpoint in `docs/STATUS.md`, the roadmap, the current SDLC, and the phase acceptance criteria from PLAN sections 13 and 20. Read referenced ADRs and path rules.
2. Confirm the previous phase's disposition and note any blockers that carry over. Identify owner-queue items and separate them from work that can proceed with synthetic data.
3. Draft ordered story briefs with file ownership, dependencies, acceptance tests, and review gates. Surface any unresolved contract or architecture decision to Keel.
4. Treat the phase as a milestone. An assigned story uses a short-lived branch off `origin/main`. Send the kickoff plan to Keel for assignments and status updates.
