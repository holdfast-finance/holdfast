---
name: phase-review
description: "Close out a Holdfast build phase. Use when phase work looks done, or the owner asks \"is phase N done\", \"wrap up the phase\" or \"phase report\". Runs reviewers and acceptance checks, writes the phase report, prepares evidence for Keel."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# Phase review

1. Identify the exact SHA and phase scope. Compare changes with PLAN sections 13 and 20, the phase contract, and the story briefs.
2. Request independent reviews through the available Codex subagent workflow. Use `security-reviewer` for every phase. Add `money-invariants-reviewer` for amounts, connectors, imports, or domain logic, `tax-config-verifier` for tax configuration, `crypto-custody-auditor` for crypto, and `ui-brand-reviewer` for frontend changes.
3. Use `phase-acceptance-checker` for the final acceptance run. A check without evidence is unverified. Capture the commands, results, and scope of each review at the same SHA.
4. Prepare the phase report for Keel with the acceptance table, findings and dispositions, demo commands, ADRs, and blocked items. Give a proposed PASS, PASS-WITH-BLOCKED, or FAIL verdict. Keel records canonical status and owns squash merges and milestone tags.
