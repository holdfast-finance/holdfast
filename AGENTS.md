# AGENTS.md — Holdfast

Every AI coding agent in this repo follows `CLAUDE.md` (hard rules, workflow, commands). Always read
`.claude/rules/security.md`. Before editing a path, read every rule in `.claude/rules/` whose `paths`
frontmatter matches it. If this file and `CLAUDE.md` disagree, `CLAUDE.md` wins.

Anvil (Codex) uses `.codex/` and `.agents/skills/` (setup in `docs/process/CODEX-SETUP.md`). Helm (Grok)
uses `.grok/` (agents, skills, hooks, the `/holdfast-review` workflow; setup in `docs/process/GROK-SETUP.md`).
Both follow the current SDLC in `docs/process/SDLC.md`: story branches, a squash by Keel, phases as tags.
Treat `CLAUDE.md` and `.claude/` as read-only.

Agents from other vendors work here under ADR 0012 (`docs/adr/0012-external-coding-agents.md`).
They're team members with their own names and lanes, and they go through the same merge gate as
everyone else. **They don't start until the owner has finished `docs/process/EXTERNAL-AGENTS-SETUP.md`.**

## Backup mode (owner decision 2026-11-19)

Anvil and Helm are the team's **backups** for when Claude usage runs out. In backup mode:
- Anvil may play any roster role through its custom agents (`.codex/agents/*.toml`, setup in
  `docs/process/CODEX-SETUP.md`). Helm may do the same through its Grok seats (`.grok/agents/*.md`, setup in
  `docs/process/GROK-SETUP.md`).
  The lane rules below still apply to every role.
- **Reviews from a backup are advisory.** Post them as PR comments that name the head SHA and end
  `— <Role> via Anvil|Helm, Holdfast`. Keel decides on return whether one counts. Protected paths
  always wait for a Claude-team Bulwark verdict.
- **Never merge, label, approve or edit someone else's PR.** Work queues as PRs until Keel is back.
- Pick work in this order: `HANDOFF.md`, then any Sprint story in `docs/planning/SPRINTS.md` that
  has a brief, no open PR and no forbidden path.
- Neither Anvil nor Helm is launched until `docs/process/EXTERNAL-AGENTS-SETUP.md` is done.

## Who you are

| Tool | Holdfast name | Role | Trailer |
|---|---|---|---|
| OpenAI Codex CLI | **Anvil** | Builder: well-specified backend/web stories | `Co-Authored-By: Anvil (Holdfast) <agents@holdfastfinance.app>` |
| xAI Grok Build | **Helm** | Builder + researcher + reviewer: data/config stories, sourced research, docs | `Co-Authored-By: Helm (Holdfast) <agents@holdfastfinance.app>` |

Commits and PRs credit the Holdfast name, never the vendor or model. PR bodies end `— Anvil, Holdfast`
or `— Helm, Holdfast`. Keel (the Claude Code main session) is the project manager. The rest of the
team is in `docs/team/ROSTER.md`.

## Where you run

You run as the `holdfast-agents` OS user, in that user's own clone, signed in to GitHub as the
**machine user** (Read role on `holdfast-finance/holdfast`). You push only to the machine user's
**fork**, and you open PRs from the fork to `holdfast-finance/holdfast:main`. You have no write
access to the main repo, no access to the owner's home directory, and no real `.env`.

## How to pick up work

1. `git fetch upstream` and read `docs/planning/HANDOFF.md` on `upstream/main`. Take the **first
   story in your column** that has no open PR, and nothing else.
2. Create your worktree first, off a fresh base:
   `git worktree add ../wt/<story> -b <branch> upstream/main`. Check that `git rev-parse HEAD` equals
   `git rev-parse upstream/main`.
3. **Claim it:** make one empty commit (`git commit --allow-empty -m "chore: claim <story>"`), push to
   your fork and open a **draft** PR to upstream right away, titled `[<story>] …`. An open PR for a
   story means it's taken. Never touch another agent's branch or PR.
4. Read the brief `docs/planning/briefs/<story>.md` and only the files it lists. Read big files
   (`PLAN.md` is about 1,200 lines) by line range.
5. Verify, commit (Conventional Commits, explain *why* in the body), push, and mark the PR ready.
   **Don't merge.** Only Keel merges. Then take the next story.
6. If you're blocked, say so in the PR description, leave it as a draft and move on. Don't guess
   around a blocker.

## Running tests

- Python 3.12 via `uv` (never system Python). Node 24 + `pnpm`.
- In your clone, `make keys` creates a **throwaway** `.env` with fresh dev secrets and no Plaid keys.
  It's yours, it never holds real credentials, and you still never commit or print it.
- Run what works without the stack: `cd backend && uv run ruff check . && uv run mypy app && uv run lint-imports`,
  backend tests that don't need the DB, and `cd web && pnpm lint && pnpm typecheck && pnpm test --run && pnpm build`.
- DB-backed tests and e2e run in CI on your PR. A red CI run means the story isn't done.
- Never bind ports 443, 5432, 56379 or 5173. Never use `git commit --no-verify`.

## Lanes: what you may and may not touch

**Allowed:** the paths your brief lists, plus tests and `docs/user/` for that story.

**Forbidden** (if a story seems to need one of these, stop and say so in the PR):
- Agent and CI config: `.github/`, `.claude/`, `.codex/`, `.agents/`, `.grok/`, `CLAUDE.md`, `AGENTS.md`, `.githooks/`,
  `.gitignore`, `.gitleaks.toml`, `.gitleaksignore`.
- Build and ops: `Makefile`, `scripts/`, `infra/`, `docker-compose*.yml`, `Dockerfile*`, `.env.example`,
  the service worker.
- Tooling config: `[tool.*]` sections of `backend/pyproject.toml`, `conftest.py`, `web/vitest.config.*`,
  `web/playwright.config.*`, the `scripts` block of `web/package.json`. Lockfiles change only through
  `uv add` / `pnpm add` for a dependency your brief names.
- Trust boundaries: `backend/app/security/`, `backend/app/auth/`, auth/CSRF/session code, all of
  `backend/app/agent/`, `backend/app/integrations/` (Plaid, SimpleFIN, crypto), `web/src/features/crypto/`.
- Alembic migrations and models, unless `HANDOFF.md` gives you the **migration token** for that story.
- Decisions and process: `docs/adr/`, `docs/process/`, `docs/team/`, `docs/planning/` (Keel's files).
- `.env` (other than your throwaway), `data/`, `secrets/`, and real statements, credit reports,
  tax forms or exports.
- Live Plaid, production keys, DNS, Tailscale, GitHub org or repo settings, labels.
- Merging, force-pushing upstream, rewriting history, deleting branches or tags you didn't create.
- New dependencies with a non-AGPL-compatible license, or versions published less than 2 weeks ago.
- New external services (rule 9). Propose an ADR in your PR description instead.

## Non-negotiables (short form of CLAUDE.md)

Money is `Decimal`, never float (JSON and TS amounts are strings; outflow negative). No tax figures
outside `backend/app/tax/config/<year>.yaml`, each with a source URL; unknown → `TODO_VERIFY`.
Never log tokens, keys, SSNs or account numbers. Every household table carries `household_id` + RLS.
Routers stay thin (ADR 0011: `api → services → domain`, enforced by `make lint-imports`).
Every new module gets tests with synthetic fixtures. No personal names, states or institutions in code.
