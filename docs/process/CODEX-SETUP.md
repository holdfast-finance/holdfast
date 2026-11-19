# Holdfast in Codex

Anvil is the Codex contributor. Start a fresh local Codex session at the repo
root so it can discover the agent definitions and skills. Review the project hook
commands before trusting them with `/hooks`. The hook commands use Git, Bash,
Python 3, and jq. The Python and web formatters run when the local toolchains
are available. See [Codex agent guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and [Codex hooks](https://learn.chatgpt.com/docs/hooks).

| Claude Code setup | Codex setup |
| --- | --- |
| `CLAUDE.md` and nine `.claude/rules/` files | [AGENTS.md](../../AGENTS.md) points to the shared engineering contract and requires reading all matching path rules. Security applies to every task. |
| Fifteen builders, specialists, and reviewer profiles | [Custom agents](../../.codex/agents/) preserve role names, instructions, and effort. Opus maps to GPT-6.2 Astra. Sonnet and Haiku map to GPT-6.2 Sol with the original effort. Keel remains the project coordinator. |
| Six Markdown skills | [Skill entry points](../../.agents/skills/) reuse the source workflows. Phase operations follow the current SDLC and Anvil routes phase decisions to Keel. |
| Pre-edit guard and post-edit formatter | [`.codex/hooks/patch.py`](../../.codex/hooks/patch.py), wired from [hooks.json](../../.codex/hooks.json), guards and formats Codex `apply_patch` events. It rejects a resolved path outside the repo, protected data, and `.claude` edits, and it scans additions for secrets. Claude's `guard-secrets.sh` does not run for Anvil. The formatter only touches repository files. |
| Shell approvals and Plaid defaults | [Project config](../../.codex/config.toml) sets `PLAID_ENV=sandbox` for shell commands. [Command rules](../../.codex/rules/holdfast.rules) carry over the explicit approval prefixes and the two forbidden `cat` commands. |
| Plugin capabilities | Use the project's lint/type commands, review profiles, Git tooling, official library sources, and Playwright. Browser and documentation tools depend on the Codex surface and session. |

The hook and command-rule scopes matter. The hooks cover Codex patch edits.
Managed filesystem policy provides enforcement of file-read denials. Claude's
shell and domain allowlists have no direct project-wide equivalent here, so
the setup leaves command and network authorization to the session. Reviewer
profiles inherit the parent session's permissions and carry read-only
instructions. Keel decides which SHA-bound reviews satisfy the merge gate.
See [Codex command rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
and [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).

| Claude plugin | Codex workflow |
| --- | --- |
| `pyright-lsp`, `typescript-lsp` | Use project mypy and TypeScript checks. Editor language servers are editor settings. |
| `security-guidance`, `pr-review-toolkit` | Use Bulwark and the path-specific reviewers. Record evidence and the reviewed commit SHA for Keel. |
| `frontend-design`, `playwright` | Use the brand and frontend rules, Prism, and the checked-in Playwright suite. Browser tools depend on the session. |
| `commit-commands`, `claude-md-management` | Use the Git commands and attribution in the engineering contract. Maintain shared instructions through `AGENTS.md` and the existing rule files. |
| `context7` | Use official documentation for the dependency version in the lockfile. |

## Repository review

Reviewed the checked-in project at `b2cbfcf` across the build plan and phase
status, backend routing and service boundaries, auth and tenancy, models and
migrations, money handling, frontend routing, CI, Compose, Caddy, backup
scaffolds, and the Claude agent files. Checks ran against an isolated archive
with synthetic inputs. The product is at Phase 1 with foundation work queued.
Bank connectors, imports, the advisor, tax, and crypto need review as their
implementations land.

| Priority | Finding |
| --- | --- |
| High | Invite replacement is split across several Redis calls. Concurrent creates can leave two token records valid, and acceptance can unconditionally remove a newer invite's person index. Use atomic replacement and conditionally clear the index for the accepted digest. Add tests for reissue and concurrent acceptance. [invites.py](../../backend/app/services/invites.py#L67) |
| High | The checked-in CI workflow lacks the automated `ci-required` aggregate, SHA-bound review gate, coverage ratchet, and Playwright smoke required by the SDLC. Dependency audits are informational and actions use tags. This is tracked as F7, so the local Keel gate still matters. [CI](../../.github/workflows/ci.yml#L16) · [SDLC](SDLC.md#2-merge-gate-all-required) |
| Medium | The Claude phase skills still instruct agents to open phase branches and seek owner approval for a merge commit. The current SDLC uses short-lived story branches, squash merges by Keel, and phase milestone tags. The Codex entry points follow the current SDLC. [Phase kickoff](../../.claude/skills/phase-kickoff/SKILL.md#L12) · [Phase review](../../.claude/skills/phase-review/SKILL.md#L26) |
| Medium | The project checkpoint lists F1 as open. PR #4 already appears on the reviewed main history at `d223c1b`. Reconcile the checkpoint before resuming the queue. [STATUS.md](../STATUS.md#L29) |
| Low | The roster's migration lane names `backend/alembic/`, while Alembic migrations are in `backend/migrations/`. The path-scoped migration rule already uses the correct directory. [Roster](../team/ROSTER.md#L41) · [Migration rule](../../.claude/rules/migrations.md) |

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
| Codex setup | Validated all 15 agent TOMLs, six skill entry points, hook JSON, shell rule decisions, six guard cases, and formatter handoff from a subdirectory. |
