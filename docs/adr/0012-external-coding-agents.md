# 0012. Coding agents from other vendors (Codex as Anvil, Grok as Helm)

- Status: Accepted. **Dispatch suspended** (Keel, 2026-11-20): no Codex or Grok run starts until the machine-user isolation exists (owner queue). `scripts/external-agent-launch.sh` is the only supported launch and refuses without it.
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
1. Codex works as **Anvil** and Grok works as **Helm**. They get lanes, briefs and a trailer like
   any other team member. They are never credited by vendor or model name.
2. **Their own identity, no write access.** They run as a separate OS user (`holdfast-agents`) in
   its own clone, signed in to GitHub as a machine user with the **Read** role. They push only to the
   machine user's fork and open PRs from there. They can't push to `main`, merge, add labels
   (including `bulwark-approved`) or change settings, whatever their instructions say. This holds even
   while rulesets are unavailable on the free plan. Owner steps: `docs/process/EXTERNAL-AGENTS-SETUP.md`.
3. **Same gate.** Only Keel merges, after `ci-required`, `review-gate` and SHA-bound reviews from the
   Claude-team gates (Bulwark, Ledger, Tally, Prism, Sounding) by path. Cross-vendor review is a
   feature here: the builder and the gate are different models.
4. **Narrow lanes** (`AGENTS.md`). Agent/CI config, build and ops files, tooling config, every
   trust boundary (auth, CSRF, agent and redaction, integrations, crypto), migrations, and ADR/process
   docs are out of bounds. Their first stories are pure domain cores (transfers, recurring), sourced
   tax config and user docs.
5. **Pull-based coordination** through `docs/planning/HANDOFF.md`. An open draft PR is the claim.
6. **Data exposure:** each vendor receives whatever its agent reads or runs: repo files, command
   output, test logs, and anything else readable by `holdfast-agents`. The OS user separation keeps
   the owner's `.env`, SSH keys and `gh` token out of that set. The agents' own `.env` is a throwaway
   from `make keys`. The repo holds no real financial data (hard rule 7) and is going open source
   under AGPL-3.0.

## Consequences
- More throughput when Claude windows are exhausted, at the cost of review load on Keel's next window.
- Claude's own hook files don't run for Anvil or Helm. Their edits go through the shared
  `scripts/agent_guard.py` instead (Codex via `.codex/hooks/patch.py`, Grok via `.grok/hooks/guard-secrets.sh`),
  which fails closed and also blocks seed phrases. There is no post-edit formatting; CI and `make lint`
  enforce it. The pre-commit gitleaks hook, CI and `review-gate` still run.
- `scripts/external-agent-launch.sh` is the only supported way to start either CLI. It refuses unless the
  OS user is `holdfast-agents` and `gh api repos/holdfast-finance/holdfast --jq .permissions.push` prints `false`.
- Keel is a Claude-only seat. There is no Keel seat in `.codex/` or `.grok/`.
- Agents can't run DB-backed tests locally (no docker group). CI is the source of truth for their PRs.
- The owner pays for and configures each CLI. Usage isn't counted in the sprint token log.
- If either agent breaks a lane rule, Keel closes its PRs and removes its column from `HANDOFF.md`.
- The agents' own config (`.codex/`, `.agents/`, `.grok/`) and `AGENTS.md` are protected paths and change only through Keel and Bulwark. The guards and the Codex profile also refuse edits to them.
