# Holdfast in Codex

Anvil is the Codex contributor. Start a fresh local Codex session at the repo
root so it can discover the agent definitions and skills. Review the project hook
commands before trusting them with `/hooks`. The hook commands use Git and
Python 3. See [Codex agent guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and [Codex hooks](https://learn.chatgpt.com/docs/hooks).

| Claude Code setup | Codex setup |
| --- | --- |
| `CLAUDE.md` and nine `.claude/rules/` files | [AGENTS.md](../../AGENTS.md) points to the shared engineering contract and requires reading all matching path rules. Security applies to every task. |
| Fifteen builders, specialists, and reviewer profiles | [Custom agents](../../.codex/agents/) preserve role names, instructions, and effort. Opus maps to GPT-6.2 Astra. Sonnet and Haiku map to GPT-6.2 Sol with the original effort. Keel remains the project coordinator. |
| Six Markdown skills | [Codex skills](../../.agents/skills/) contain their workflows. Phase operations follow the current SDLC and Anvil routes phase decisions to Keel. |
| Pre-edit guard | [Hooks](../../.codex/hooks.json) handle Codex `apply_patch` events directly. The guard checks resolved paths and patch additions, fails closed on malformed patches, and blocks edits to the guard machinery itself. There is no post-edit formatter hook, because it would run repo-writable code (Prettier config, `.venv` Ruff) outside the sandbox. Codex runs `make fmt` inside its sandbox instead. |
| Shell approvals and Plaid defaults | [Project config](../../.codex/config.toml) sets `PLAID_ENV=sandbox` for shell commands. [Command rules](../../.codex/rules/holdfast.rules) carry over the explicit approval prefixes and the forbidden secret-reading and gate-bypass prefixes. |
| Plugin capabilities | Use the project's lint/type commands, review profiles, Git tooling, official library sources, and Playwright. Browser and documentation tools depend on the Codex surface and session. |

The hooks cover Codex patch edits. Trusted local CLI sessions default to the
`holdfast` permission profile. The profile denies reads of matching private
files and marks the Claude configuration read-only. Optional private paths
use globs so a fresh checkout can launch without those directories.
On Linux the deny globs match files present when a sandboxed command starts.
Workspace-managed sessions apply their own policy. Claude's
shell and domain allowlists have no direct project-wide equivalent here, so
the setup leaves command and network authorization to the session. Reviewer
profiles inherit the parent session's permissions and carry read-only
instructions. Keel decides which SHA-bound reviews satisfy the merge gate.
See [Codex command rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
and [Codex permission profiles](https://learn.chatgpt.com/docs/permissions).

| Claude plugin | Codex workflow |
| --- | --- |
| `pyright-lsp`, `typescript-lsp` | Use project mypy and TypeScript checks. Editor language servers are editor settings. |
| `security-guidance`, `pr-review-toolkit` | Use Codex review for local diffs and Bulwark and path-specific reviewers for specialist evidence. Record the reviewed SHA for Keel. Use the connected GitHub integration when available for authorized PR reads. |
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
| Codex setup | Validated 15 agent TOMLs, six self-contained skills, hook JSON, shell rule decisions, and eight guard cases. Verified sandbox startup and deny rules using synthetic files. |

## Foundation handoff (2026-11-20)

The team verified these revisions against main at `0633604`. Onboarding PR #8
is still open. Its draft handoff names C4a and C5a for Anvil and describes the
separate agent identity and fork. This session ran under the workstation
account with managed workspace permissions.

| Story | Evidence | Remaining work |
| --- | --- | --- |
| F3c, `1d3f075a729044ca2ef6b5ce96c75e0ebf5c5665` | Both required checks passed. Anvil ran 70 targeted tests, Ruff, and mypy. The offline OpenAPI checksum matches the generated TypeScript header. Advisory review found no money-handling defect. [PR #27](https://github.com/holdfast-finance/holdfast/pull/27) | Keel verifies Ledger evidence for this SHA. Update ADR 0014's guidance to use the storage-specific types before Phase 2 adds money fields. |
| F7 live gate | Ruleset 27673213 requires both checks. PR #27 passed both and GitHub reported a clean merge state. PR #14 passed CI and failed the review gate. [PR #14](https://github.com/holdfast-finance/holdfast/pull/14) | Public-fork behavior still lacks a live example. F11 and F12 have no open PR. |
| F6, `7768def103e2e4e1f3682f1aac15f1dc31683780` | Bulwark's local re-review found no findings. Anvil ran 48 passing tests, Ruff, mypy, and offline Alembic SQL generation in both directions. The review also records Helm's 403 passing Postgres model tests. | Keel publishes the branch. CI still needs to verify the live owner migration cycle, model drift, and RLS on the PR head. |
| S0, `2bfd288dac10415aef617a557ffa0abcbdcbe729` | 30 synthetic tests passed. Bulwark's local review closes both prior findings at this SHA. | Keel updates the branch to main and collects CI and review evidence on the resulting SHA. |
| F5c, `61c005f30b12bc5257cbceab0ba194920fdda5c9` | The branch conflicts with current worker startup. An advisory check using synthetic URL credentials reproduced a missing Authorization header in the outbound request. | Harbor resolves the conflict and preserves SimpleFIN authentication through the outbound client. Recheck with synthetic credentials and send the new SHA to Bulwark. |
| F2b | Helm's branch is local at `193fe49`. The handoff identifies household binding on enqueue and recovery of rows left running after a timeout. | Helm resumes the existing branch. |

## Resume evidence

GitHub reported PR #8 open and mergeable at
`765c973f3c126db3e3a14f3d86dc01f9f4feb93b` against
`55950b5a0d8bbd090f8fadf02c35ab9aa9fdba1a`. A local patch adds the shared
policy to that base. The patch applies to an isolated archive and the policy
copy matches the workspace version byte for byte. The launch and lane rules
continue to govern Anvil and Helm. Keel can include the patch in the onboarding
PR and send its resulting head to Bulwark.

The PR #8 handoff suspends story dispatch until the separate OS and GitHub
identities are ready. The workstation lookup returned no `holdfast-agents`
user. C4a and C5a remain queued for an isolated Anvil session.

The old checkpoint predates merged F3 and its follow-ups (#22, #23, #27),
merged F2 (#24), and merged S0 (#29). GitHub reported open, conflicting PRs
for F7b (#16), F6 (#28), F5c (#31), and F2b (#32). These are existing branches
for their maintainers to reconcile with main.

For F7b, 77 offline CI and protected-path tests passed at
`8df86601d839588f03384f0e8a414d2a75e378c1` in an isolated archive. This evidence
applies to the tested commit. A reconciled PR head needs fresh checks and
Bulwark review. Live fork behavior remains unverified.

## Public repository and release preflight

GitHub confirmed that `holdfast-finance/holdfast` is public and that `main`
points to `7916e34712feae32f4f22ad811a76059c95091ce`, the S0 merge. A later
GitHub query reported PRs #8, #16, #28, #31 and #32 mergeable. That result
describes GitHub's merge calculation. Gate evidence remains separate.

At cached `main` `55950b5`, the release helper confirmed the expected product
and Python versions (`0.0.0-alpha` and `0.0.0a0`) and all eight release-tool
tests passed. Fetching the live revision was blocked by read-only Git
metadata in this checkout and GitHub DNS in an isolated clone. Artifact
builds and first-run smoke remain unverified.

The roadmap defines the MVP as Phase 5 PASS on sandbox and synthetic data.
The first alpha has a separate gate covering matching versions, build
artifacts and checksums, and a synthetic clean-install and first-run smoke.
Keel owns the release. Product story dispatch remains gated by PR #8's
isolated account setup.

Command prefix rules cannot catch every refspec form (for example `git push origin HEAD:refs/heads/main`), so the merge gate and `review-gate` stay the real control.
