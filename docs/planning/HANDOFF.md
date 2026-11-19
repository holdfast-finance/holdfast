# Handoff queue: Anvil (Codex) and Flint (Grok)

Rules are in `AGENTS.md` and ADR 0012. Take the first unclaimed story in **your** column. Pushing the
branch is the claim. Briefs are in `docs/planning/briefs/`. Keel reviews and merges on its next window.

| # | Anvil (Codex) | Flint (Grok) |
|---|---|---|
| 1 | **F3** OpenAPI → TS types · `feat/F3-openapi-types` · M | **T1** Tax config TY2025 + TY2026 · `feat/T1-tax-config` · M |
| 2 | **A1** Redaction library · `feat/A1-redaction` · S | **D1** User docs, Phases 0–1 · `docs/D1-user-docs` · S |

Status per story (edit on your own branch; Keel reconciles on merge):

| Story | Agent | Branch pushed | PR | Notes |
|---|---|---|---|---|
| F3 | Anvil | | | |
| A1 | Anvil | | | |
| T1 | Flint | | | |
| D1 | Flint | | | |

## Do not take (Claude team, in flight or next)
F7 merge gate (Harbor), F8 worktrees (Harbor), F9 security lows (Bulwark), F10 brand theme (Prism),
F2 job framework, F5 secrets, F6 schema-gap migration (holds the migration token), F11, F12, all of Phase 2.

## Blocked
_none_

## Migration token
Held by: nobody. Keel assigns it. Anvil and Flint don't hold it in this queue.

## Starting a session (owner)

Run from the repo root. Each agent creates its own worktree as `AGENTS.md` describes.

```sh
# Anvil (Codex), unattended, sandboxed to the workspace
codex exec -s workspace-write -C . "You are Anvil. Read AGENTS.md and docs/planning/HANDOFF.md, then take and finish your next story."

# Flint (Grok), interactive with edits auto-accepted
grok --permission-mode acceptEdits "You are Flint. Read AGENTS.md and docs/planning/HANDOFF.md, then take and finish your next story."
```

Running DB tests needs network access to the local stack, and `gh pr create` needs GitHub. If the
sandbox blocks either, the agent pushes the branch and opens the PR as a draft, or notes it under "Blocked".
Configure each CLI to deny reads of `.env`, `data/` and `secrets/`.
