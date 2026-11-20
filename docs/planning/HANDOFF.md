# Handoff queue: Anvil (Codex) and Helm (Grok)

Rules are in `AGENTS.md` and ADR 0012. **Dispatch is suspended (Keel, 2026-11-20): nothing here starts
until the machine-user isolation exists** (the `holdfast-agents` OS user and the Read-role GitHub machine user,
`docs/process/EXTERNAL-AGENTS-SETUP.md`, an owner-queue item in `docs/STATUS.md`). Take the first story in **your** column that has no open
PR. An open (draft) PR is the claim. Briefs are in `docs/planning/briefs/`. Keel reviews and merges
on its next window and updates this file.

| # | Anvil (Codex) | Helm (Grok) |
|---|---|---|
| 1 | **C4a** Transfer pairing, pure core · `feat/C4a-transfer-core` · S | **T1** Tax config TY2025 + TY2026 · `feat/T1-tax-config` · M |
| 2 | **C5a** Recurring detection, pure core · `feat/C5a-recurring-core` · S | **D1** User docs, Phases 0–1 · `docs/D1-user-docs` · S |

Keel-maintained status:

| Story | Agent | PR | Gate |
|---|---|---|---|
| C4a | Anvil | | Ledger |
| C5a | Anvil | | Ledger |
| T1 | Helm | | Tally |
| D1 | Helm | | Keel |

## Do not take (Claude team)
F3 OpenAPI types, A1 redaction, F2 job framework, F5 secrets, F6 schema-gap migration, F7, F8b, F9,
F11, F12, and all of Phase 2. Anything touching a forbidden path in `AGENTS.md`.

## Migration token
Held by: nobody. Anvil and Helm don't hold it in this queue.

## Starting a session (owner, as the `holdfast-agents` user, in its clone)

Do not pass `-s` / `--sandbox`. `sandbox_mode` must be unset in every Codex config layer,
including the user config. If either is set, Codex ignores `default_permissions` and the
`holdfast` denials never apply. Before the first run, check that the active policy is
`holdfast`.

Start sessions only through `scripts/external-agent-launch.sh`. It refuses unless `id -un` is
`holdfast-agents` and `gh api repos/holdfast-finance/holdfast --jq .permissions.push` prints `false`, it
rejects sandbox-override flags, and it starts the CLI from the repo root. Nothing else is a supported launch.

```sh
scripts/external-agent-launch.sh codex exec "You are Anvil. Read AGENTS.md and docs/planning/HANDOFF.md, then take and finish your next story."
scripts/external-agent-launch.sh grok --permission-mode acceptEdits "You are Helm. Read AGENTS.md and docs/planning/HANDOFF.md, then take and finish your next story."
```
