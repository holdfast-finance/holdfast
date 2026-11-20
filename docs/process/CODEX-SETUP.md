# Holdfast in Codex

Anvil is the Codex contributor. **Dispatch is suspended** until the `holdfast-agents` OS user and the Read-role
machine user exist (owner queue, ADR 0012). The only supported launch is `scripts/external-agent-launch.sh codex ...`,
which refuses unless `id -un` is `holdfast-agents` and `gh api repos/holdfast-finance/holdfast --jq .permissions.push`
prints `false`. It starts Codex at the repo root so it can discover the agent definitions and skills. Review the project hook
commands before trusting them with `/hooks`. The hook commands use Git and
Python 3. There is no post-edit formatter for Anvil; CI and `make lint` enforce formatting. See [Codex agent guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and [Codex hooks](https://learn.chatgpt.com/docs/hooks).

| Claude Code setup | Codex setup |
| --- | --- |
| `CLAUDE.md` and nine `.claude/rules/` files | [AGENTS.md](../../AGENTS.md) points to the shared engineering contract and requires reading all matching path rules. Security applies to every task. |
| Fifteen builders, specialists, and reviewer profiles | [Custom agents](../../.codex/agents/) preserve role names, instructions, and effort. Opus maps to GPT-6.2 Astra. Sonnet and Haiku map to GPT-6.2 Sol with the original effort. Keel remains the project coordinator. |
| Six Markdown skills | [Skill entry points](../../.agents/skills/) reuse the source workflows. Phase operations follow the current SDLC and Anvil routes phase decisions to Keel. |
| Pre-edit guard | [`.codex/hooks/patch.py`](../../.codex/hooks.json) hands every `apply_patch` event to the shared [`scripts/agent_guard.py`](../../scripts/agent_guard.py), which fails closed. It resolves each target with realpath (symlinks and `..` included) and rejects paths outside the repo, `.env*`, `data/`, `secrets/`, `exports/`, and the agent and CI config (`.claude/`, `.codex/`, `.agents/`, `.grok/`, `CLAUDE.md`, `AGENTS.md`, `.githooks/`, `.gitleaks.toml`). It scans every string in the patch for secrets, SSNs and 12+ consecutive BIP39 words. Claude's `guard-secrets.sh` does not run for Anvil. |
| Shell approvals and Plaid defaults | [Project config](../../.codex/config.toml) sets `PLAID_ENV=sandbox` for shell commands. [Command rules](../../.codex/rules/holdfast.rules) forbid secret reads, `gh` merge, review, edit, label, release, secret, ruleset, workflow and `api`, pushes to `upstream` and to `main` in the enumerated forms, `git tag`, `git commit -n/--no-verify`, `git -C`/`git -c` and `bash -c`, and prompt for the rest of the Claude approval list. Prefix rules match exact argv prefixes, so the GitHub Read role and the OS user stay the hard boundary. Verify with `codex execpolicy check --rules .codex/rules/holdfast.rules <command>`; `scripts/tests/test_agent_guard.py` does so. |
| Plugin capabilities | Use the project's lint/type commands, review profiles, Git tooling, official library sources, and Playwright. Browser and documentation tools depend on the Codex surface and session. |

The hook and command-rule scopes matter. The hooks cover Codex patch edits.
Trusted local CLI sessions default to the `holdfast` permission profile. The
profile denies reads of matching private files (`data/`, `secrets/`, `exports/`,
`.aws/`, and `.env` files at any depth) and marks `.claude`, `.codex`, `.agents`, `.grok`, `.githooks`,
`CLAUDE.md` and `AGENTS.md` read-only. These use globs so a fresh checkout can launch without those
directories. On Linux the deny globs match files present when a sandboxed
command starts. Workspace-managed sessions apply their own policy. Claude's
shell and domain allowlists have no direct project-wide equivalent here, so
the setup leaves command and network authorization to the session. Reviewer
profiles inherit the parent session's permissions and carry read-only
instructions. Keel decides which SHA-bound reviews satisfy the merge gate.
See [Codex command rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
[Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), and
[Codex permission profiles](https://learn.chatgpt.com/docs/permissions).

| Claude plugin | Codex workflow |
| --- | --- |
| `pyright-lsp`, `typescript-lsp` | Use project mypy and TypeScript checks. Editor language servers are editor settings. |
| `security-guidance`, `pr-review-toolkit` | Use Bulwark and the path-specific reviewers. Record evidence and the reviewed commit SHA for Keel. |
| `frontend-design`, `playwright` | Use the brand and frontend rules, Prism, and the checked-in Playwright suite. Browser tools depend on the session. |
| `commit-commands`, `claude-md-management` | Use the Git commands and attribution in the engineering contract. Maintain shared instructions through `AGENTS.md` and the existing rule files. |
| `context7` | Use official documentation for the dependency version in the lockfile. |

## Repository review

Reviewed the checked-in project at `b2cbfcf` (findings fixed since then are removed) across the build plan and phase
status, backend routing and service boundaries, auth and tenancy, models and
migrations, money handling, frontend routing, CI, Compose, Caddy, backup
scaffolds, and the Claude agent files. Checks ran against an isolated archive
with synthetic inputs. The product is at Phase 1 with foundation work queued.
Bank connectors, imports, the advisor, tax, and crypto need review as their
implementations land.

| Priority | Finding |
| --- | --- |
| Medium | The Claude phase skills still instruct agents to open phase branches and seek owner approval for a merge commit. The current SDLC uses short-lived story branches, squash merges by Keel, and phase milestone tags. The Codex entry points follow the current SDLC. [Phase kickoff](../../.claude/skills/phase-kickoff/SKILL.md#L12) · [Phase review](../../.claude/skills/phase-review/SKILL.md#L26) |

The frontend still uses handwritten API types, tracked as F3. Backup and restore
scripts exit with a clear message and are scheduled for Phase 9.
[API types](../../web/src/api/types.ts) · [Backup](../../scripts/backup.sh) ·
[Restore](../../scripts/restore.sh)

## Verification

| Check | Result |
| --- | --- |
| Backend `python -m pytest -q` | 584 passed against disposable PostgreSQL 16 and Redis 7. |
| Backend static checks | Ruff lint and format, mypy, and all three import contracts passed. |
| Database migration | One Alembic head. Upgrade, downgrade to base, and upgrade passed as the schema owner. |
| Frontend `pnpm test --run` | 193 passed, two skipped. Lint, typecheck, and build passed. |
| Browser `pnpm e2e` | Chromium passed first-run setup, passkey registration, adding a second person, sign-out, and passkey sign-in. The test used Vite preview as the local proxy. |
| Compose overlays | Port checks passed for base, dev, and production. Production rendered `APP_ENV=prod` for API and worker. |
| Codex setup | Validated all 15 agent TOMLs, six skill entry points, hook JSON, shell rule decisions, and the shared guard payload matrix (`scripts/tests/test_agent_guard.py`). Sandbox startup and deny rules were checked with synthetic files. |
