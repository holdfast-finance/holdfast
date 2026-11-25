# Holdfast in Grok

Helm is the Grok contributor. Claude Code's Keel stays the organizer: only Keel
merges, tags, and releases. Start Grok at the repo root so it loads `CLAUDE.md`,
`AGENTS.md`, `.claude/rules/`, and this harness. Trust the folder before project
hooks run (`/hooks-trust` or `grok --trust`).

The checkout this file was written from is five commits behind `origin/main`
(`b2cbfcf` vs `fc5d0fa`) and already has uncommitted Codex setup. The review
below reads `origin/main` for product and CI, and the working tree for the
agent files.

| Claude Code | Grok |
| --- | --- |
| `CLAUDE.md` and nine `.claude/rules/` files | Loaded as-is. `.grok/rules/helm.md` adds the Helm seat. Rules are not copied. |
| Fifteen role files in `.claude/agents/`, plus Keel as the main session | Sixteen seats in `.grok/agents/`, including `keel.md` for a session explicitly told to sit as Keel. `model: inherit`. Reviewers that must not edit use `permission_mode: plan`. Sounding stays on `default` so it can run the suite. |
| Six skills whose phase flows still open a phase branch and ask the owner to merge | `.grok/skills/` overrides those names for Grok and follows `docs/process/SDLC.md`: story branches, squash by Keel, phases as tags. Claude's copies are untouched (`.claude/` is a protected path). |
| Pre-edit secret guard and post-edit formatter | `.grok/hooks/holdfast.json` matches `search_replace`, `write`, and `hashline_edit`. Wrappers normalize `path` / `contents` and a truncated payload, then call the shared scripts. The guard fails closed. |
| `.claude/settings.json` allow, ask, and deny | Already loaded. `.grok/config.toml` adds secret-path denies at any depth. |
| `PLAID_ENV=sandbox` in Claude's env block | Unchanged. The app default is sandbox. Helm does not set `PLAID_ENV=production`. |
| Nine official plugins | Mapped below. No second copy is installed. |

Spawn a seat with `spawn_subagent` and `subagent_type` set to the file name
(`security-reviewer`, `lattice`, …). Do not pass a model. For a branch review
without edits, run `/holdfast-review` (optional `args.base`, default
`origin/main`). That workflow fans out Bulwark, Ledger, Prism, and Sounding
with shell and no writes, then writes a scratch packet. It does not merge.

| Claude plugin | What Helm uses |
| --- | --- |
| `pyright-lsp`, `typescript-lsp` | The user `~/.grok/lsp.json` (pyright and typescript). No project `lsp.json`, so a laptop path is not committed. `make lint` remains the gate. |
| `context7` | The connected Context7 MCP server. Docs must match the version in the lockfile. |
| `playwright` | The connected Playwright MCP server and `web/e2e/`. |
| Browser checks | Chrome DevTools MCP is connected. Prism still verifies the changed flow, not a single screenshot. |
| `security-guidance`, `pr-review-toolkit` | Bulwark and `/holdfast-review`. A sign-off names the head SHA. |
| `frontend-design` | `docs/brand/BRAND.md`, `.claude/rules/frontend.md`, Prism, and `ui-brand-reviewer`. |
| `commit-commands` | Conventional Commits and the attribution trailer in `CLAUDE.md`. |
| `claude-md-management` | Helm does not rewrite `CLAUDE.md` or `.claude/`. Keel does, with a Bulwark sign-off. |

## Model routing

Grok seats inherit the parent model. The roster's Opus, Sonnet, and Haiku
choices stay in `.claude/agents/` for Claude Code. `effort` on a Grok seat
records the same high / medium / low as the roster. Do not ask a Grok spawn
for a Claude model id.

## Repository review

Reviewed `origin/main` at `fc5d0fa` (F7 merge gate). The local worktree is
`b2cbfcf` plus uncommitted Anvil files and this Grok harness. Checks are from
reading the tree and from `git show`. The test suite was not re-run in this
pass.

The product is Phase 1 PASS. Phase 1.5 has not been accepted. On `origin/main`,
`backend/app/agent/`, `importers/`, `integrations/` (including `plaid/`),
`notify/`, and `tax/` are still package stubs. Bank connectors, imports, the
advisor, tax, and crypto need a fresh review when their code lands.

| Priority | Finding |
| --- | --- |
| High | Invite replacement is several Redis calls (`backend/app/services/invites.py`, unchanged on `origin/main`). Two creates for one person can leave two token keys valid. Accept deletes the person index with no compare, so a newer invite's index can be removed while its token key remains. Replace and accept should be one Redis script, and accept should clear the index only when it still points at the digest just consumed. Add tests for reissue and overlapping create/accept. |
| Medium | `docs/STATUS.md` and the Sprint 1 table on `origin/main` still describe F7 and F9 as open. `fc5d0fa` (F7, #3) and `0186ad6` (F9, #9) are already on `main`. The 08:30 checkpoint was written before those merges. Keel should move the resume line to "F7 and F9 merged; next is Bulwark on #8, F3, then `protect-main.sh` (owner queue)." |
| Medium | Claude `phase-kickoff` and `phase-review` still create `phase/<n>` branches and stop for an owner merge. Current SDLC uses story branches, a Keel squash, and a `phase-<n>` tag. Grok's skills follow the SDLC. F11 is the planned Claude-side update. `.claude/` was not edited. |
| Medium | The uncommitted Anvil section of `AGENTS.md` points at ADR 0012 and `docs/planning/HANDOFF.md`. Neither file is on `origin/main`. PR #8 is the owner of that wording and was still in review at the 08:30 checkpoint. Reconcile Helm's section with #8 before committing `AGENTS.md`. |
| Low | `docs/team/ROSTER.md` names `backend/alembic/` as the migration lane. Alembic revisions live in `backend/migrations/`. The path-scoped rule already uses the right directory. |
| Low | Top of `docs/planning/SPRINTS.md` still marks F1, F4, F7, F8, F9, and F10 as `todo` while later checkpoints and `main` show them merged. The checkpoint tables are the accurate record. |

Money: a search of the working tree found no `float(`, `parseFloat`, or `Number(` in application code. `backend/tests/test_no_float_money.py` and `web/src/components/Money.test.tsx` guard that. Frontend API types are still handwritten; F3 was in progress when the weekly limit hit.

`ci-required` on `origin/main` aggregates detect, backend, web, secrets, scripts-test, and compose. Actions in that workflow are pinned by SHA. `review-gate` runs from `pull_request_target` on the base branch, drops `bulwark-approved` on a new push, and does not check the PR out into the workdir. Whether `scripts/protect-main.sh` has been applied on the GitHub repo is an owner-queue item, not something this pass could see.

## Verification

| Check | Result |
| --- | --- |
| Secret-guard wrappers | Six synthetic payloads, then one more through `bash`. Real-looking SSN, `path`/`contents`, truncation, and a `.env` path blocked. A `000-` SSN and a normal edit were allowed. The formatter wrapper exits 0. |
| `/holdfast-review` | Smoke check only (`validate_only`). Not a live reviewer run. |
| Agent files | 16 seats under `.grok/agents/`. Claude's 15 files were not edited. |
| `grok inspect` | Not confirmed. A nested `grok inspect` refused the new hook file because this session's sandbox still has the directory writable. Start Grok at the repo root, outside that sandbox, and trust the folder. |
| Backend and browser suites | Not re-run. Last recorded full run is the Codex setup note at `b2cbfcf`. |
