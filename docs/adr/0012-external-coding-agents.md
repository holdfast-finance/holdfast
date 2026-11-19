# 0012. Coding agents from other vendors (Codex as Anvil, Grok as Flint)

- Status: Accepted
- Date: 2026-11-19
- Deciders: Keel; owner request ("prepare Codex and Grok to work while you're gone")

## Context
The Claude Code team is capped at about 3 build runs and 1 review per 5-hour usage window
(MODEL-ROUTING.md). When the window runs out, or the owner is away, the critical path stops. The
owner has OpenAI Codex CLI and xAI Grok Build installed on the dev machine and wants them to keep
working in those gaps. Hard rule 9 requires an ADR for any new external service. These are dev-time
tools, not runtime services, but each one sends repo contents to its vendor, so the decision is
recorded here. `AGENTS.md` already said a new agent's lane has to be defined before it starts.

## Decision
1. Codex works as **Anvil** and Grok works as **Flint**. They get lanes, briefs and a trailer like
   any other team member. They are never credited by vendor or model name.
2. **Same gate, no merge rights.** They push branches and open PRs. Only Keel merges, after
   `ci-required` and SHA-bound reviews from the Claude-team gates (Bulwark, Ledger, Tally, Prism,
   Sounding) by path. Cross-vendor review is a feature here: the builder and the gate are different models.
3. **Narrow lanes.** They're forbidden from protected paths, migrations (unless they hold the
   token), live Plaid, secrets and anything on the owner queue (`AGENTS.md`).
4. **Pull-based coordination** through `docs/planning/HANDOFF.md`. Pushing the story branch is the
   claim. One story per agent at a time, each in its own worktree off a fresh `origin/main`.
5. **Data exposure:** only the repo goes to these vendors, and it's going open source under
   AGPL-3.0 anyway. `.env`, `data/` and `secrets/` are never read (guard hooks don't cover these
   tools, so the rule is in `AGENTS.md`, and the owner should configure each CLI's sandbox or deny
   list to match). No real financial data exists in the repo (hard rule 7).

## Consequences
- More throughput when Claude windows are exhausted, at the cost of review load on Keel's next window.
- The Claude hooks (`guard-secrets.sh`, `format.sh`) don't run for Anvil or Flint. The pre-commit
  gitleaks hook and CI still do. Formatting drift gets caught by `ruff format --check` and `pnpm lint`.
- The owner pays for and configures each CLI. Usage isn't counted in the sprint token log.
- If either agent breaks a lane rule, Keel closes its PRs and removes its column from `HANDOFF.md`.
- Revisit when F7 rulesets land, so that the GitHub-side protections are verified for every author.
