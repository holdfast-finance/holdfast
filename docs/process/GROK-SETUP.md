# Holdfast in Grok

Helm is the Grok contributor. Claude Code's Keel stays the organizer: only Keel
merges, tags, and releases. Keel is a Claude-only seat; there is no Keel seat in `.grok/`.
**Dispatch is suspended** until the `holdfast-agents` OS user and the Read-role machine user exist
(owner queue, ADR 0012). The only supported launch is `scripts/external-agent-launch.sh grok ...`, which
refuses unless `id -un` is `holdfast-agents` and `gh api repos/holdfast-finance/holdfast --jq .permissions.push`
prints `false`, and starts Grok at the repo root so it loads `CLAUDE.md`, `AGENTS.md`, `.claude/rules/`, and this
harness. Trust the folder before project hooks run (`/hooks-trust` or `grok --trust`).

This harness ships with the onboarding PR (ADR 0012, `AGENTS.md`, `docs/planning/HANDOFF.md`).
The review below was read from `origin/main` at `2b4a61e` plus that PR.

| Claude Code | Grok |
| --- | --- |
| `CLAUDE.md` and nine `.claude/rules/` files | Loaded as-is. `.grok/rules/helm.md` adds the Helm seat. Rules are not copied. |
| Fifteen role files in `.claude/agents/`, plus Keel as the main session | Fifteen seats in `.grok/agents/`, none for Keel. `model: inherit`. Reviewers that must not edit use `permission_mode: plan`. Sounding stays on `default` so it can run the suite. |
| Six skills whose phase flows still open a phase branch and ask the owner to merge | `.grok/skills/` overrides those names for Grok and follows `docs/process/SDLC.md`: story branches, squash by Keel, phases as tags. Claude's copies are untouched (`.claude/` is a protected path). |
| Pre-edit secret guard | `.grok/hooks/holdfast.json` matches the edit tools and runs `.grok/hooks/guard-secrets.sh`, a thin adapter over the shared `scripts/agent_guard.py` (same engine as Codex). It resolves paths with realpath, blocks `.env*`, `data/`, `secrets/`, `exports/` and the agent and CI config, scans every string in `tool_input` for secrets, SSNs and 12+ consecutive BIP39 words, and fails closed on empty or malformed input, a truncated payload, a missing path or any error. There is no post-edit formatter; CI and `make lint` enforce formatting. |
| `.claude/settings.json` allow, ask, and deny | Already loaded. `.grok/config.toml` adds secret-path denies at any depth, `Edit` denies for agent and CI config, and `Bash` denies for `gh` merge, review, edit, label, release, secret, ruleset, workflow and `api`, pushes to `upstream` and `main`, `git tag`, `--no-verify`, `git -C`/`-c` and `bash -c`. |
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

Reviewed `origin/main` at `2b4a61e`. Checks are from reading the tree and from
`git show`. The test suite was not re-run in this pass.

The product is Phase 1 PASS. Phase 1.5 has not been accepted. On `origin/main`,
`backend/app/agent/`, `importers/`, `integrations/` (including `plaid/`),
`notify/`, and `tax/` are still package stubs. Bank connectors, imports, the
advisor, tax, and crypto need a fresh review when their code lands.

| Priority | Finding |
| --- | --- |
| Medium | Claude `phase-kickoff` and `phase-review` still create `phase/<n>` branches and stop for an owner merge. Current SDLC uses story branches, a Keel squash, and a `phase-<n>` tag. Grok's skills follow the SDLC. F11 is the planned Claude-side update. `.claude/` was not edited. |
| Low | The top of `docs/planning/SPRINTS.md` may still mark early foundation stories as `todo` while later checkpoints and `main` show them merged. The checkpoint tables are the accurate record. |

Earlier drafts of this review listed the invite replacement race, stale F7 and F9
status, the missing ADR 0012 and `HANDOFF.md`, and the roster's `backend/alembic/` path.
Those are fixed (#25, #3, #9, this PR).

Money: a search of the working tree found no `float(`, `parseFloat`, or `Number(` in application code. `backend/tests/test_no_float_money.py` and `web/src/components/Money.test.tsx` guard that. Frontend API types are still handwritten; F3 was in progress when the weekly limit hit.

`ci-required` on `origin/main` aggregates detect, backend, web, secrets, scripts-test, and compose. Actions in that workflow are pinned by SHA. `review-gate` runs from `pull_request_target` on the base branch, drops `bulwark-approved` on a new push, and does not check the PR out into the workdir. Rulesets on `main` need a public repo or a paid plan (ADR 0013), so `main-guard` detection covers it until then.

## Verification

| Check | Result |
| --- | --- |
| Secret guard | `scripts/tests/test_agent_guard.py` runs the Grok and Codex payload matrices (paths, symlinks, secrets, seed phrases, malformed and truncated input). Every case that should block does. A `000-` SSN, `.env.example` and normal edits are allowed. |
| `/holdfast-review` | Smoke check only (`validate_only`). Not a live reviewer run. |
| Agent files | 15 seats under `.grok/agents/`. Claude's 15 files were not edited. |
| `grok inspect` | Not confirmed. A nested `grok inspect` refused the new hook file because this session's sandbox still has the directory writable. Start Grok at the repo root, outside that sandbox, and trust the folder. |
| Backend and browser suites | Not re-run. Last recorded full run is the Codex setup note at `b2cbfcf`. |
