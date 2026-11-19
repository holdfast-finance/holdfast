# 06 — Harbor (DevOps/CI-CD) + Sounding (QA): SDLC and flow of work

## 1. Position

Three things matter this quarter.

**A. Replace the human phase-merge gate with a mechanical one.** Today `phase-review` ends "Stop. Ask for approval to merge", and `phase-kickoff` assumes a `phase/<n>` branch merged `--no-ff` by the owner. With full autonomy that stop becomes a hole: nothing structural stops an agent merging red work. Proposal:
- **Trunk-based with short-lived branches.** One PR per story (an S/M run), branch `feat/<epic>-<slug>`. The phase branch is demoted to a *milestone* (GitHub milestone + `phase-N` tag), not a long-lived integration branch. Long-lived phase branches are the worst fit for 3 parallel agents: they conflict late and carry the stale-base problem.
- **Agents open PRs and self-merge** (squash) via `gh pr merge --auto --squash` once required checks are green. Keel is the only merger; implementers never merge their own PR. This keeps one actor holding the merge queue.
- **Branch protection on `main`** (Keel applies it once; Harbor drafts the `gh api` call, listed under owner-free actions): required checks below, linear history, no force-push, no direct push, conversation resolution not required (agents), required approvals = 0 but a required **`review-gate`** status check (see section 2 E1.3) takes the place of human approval.

**B. Add e2e, a migration-safety gate, and a hermetic test DB to CI.** Playwright exists (`web/e2e/first-run.e2e.ts`, `E2E_HOOKS` token endpoint) but CI never runs it; `compose` job only does `config -q` and `build`. A passkey/auth regression or a broken Caddy/web wiring is invisible until someone runs `make up`. With no human looking, the e2e smoke is the only proof the assembled stack works.

**C. Make worktree agents start from current `main`.** Subagents with `isolation:"worktree"` branch from a stale commit (we have a stale `.claude/worktrees/agent-aa219…` right now). Fix at the process level, not by hoping: a `scripts/wt-new.sh` that does `git fetch origin && git worktree add -b <branch> <path> origin/main`, a SessionStart/`WorktreeCreate` hook (or first step of every agent brief) that runs `git fetch && git rebase origin/main` and fails loudly if `git merge-base --is-ancestor origin/main HEAD` is false, and a `make sync` target. The merge-time check is the real backstop: required "up to date with main" (strict status checks) so a stale branch cannot merge without rebase.

**Order change I would make:** do E1 (gates) and E2 (stale base) *before* Phase 2 starts and before brand-theme plumbing. It is ~4 runs and everything after is cheaper and safer. Also move the argon2/real-client-IP lows into the Phase 9 backlog item they belong to, but give the client-IP check an automated test in the e2e job now (Caddy `X-Forwarded-For` assertion).

## 2. Epics + stories

**E1 Merge gates and branch model**
- E1.1 Branch protection + PR flow doc (`CONTRIBUTING.md` section, `gh api` script `scripts/protect-main.sh`, applied by Keel) — S — `main` rejects direct push and red/stale PRs — none
- E1.2 Rewrite `phase-kickoff`/`phase-review` for trunk model (milestone instead of branch, tag `phase-N` by release job, no owner stop; stop only on BLOCKED owner-queue items) — S — skills reference no "ask owner to merge" — E1.1
- E1.3 `review-gate` workflow: PR must carry label/checklist for each applicable reviewer (security always; money/tax/crypto/ui by path via `paths-filter`), reviewer agents post a signed-off comment with the head SHA, a job verifies the latest SHA has each — M — a push after sign-off invalidates it — E1.1
- E1.4 `ci-required` aggregate job (single required check that `needs:` all others and treats skipped-by-detect as pass) — S — one stable name for branch protection — none
- E1.5 PR template update with gate checklist and `Refs: STATUS carry item` — S — template used by `gh pr create --body-file` — none

**E2 Worktree and parallel-agent hygiene**
- E2.1 `scripts/wt-new.sh` + `make sync` + stale-base guard in agent brief — S — new worktree HEAD == `origin/main` — none
- E2.2 Migration-head collision guard: CI step asserts exactly one Alembic head, and a `merge-queue`-style rebase rule (second PR touching `alembic/versions` must rebase and renumber) — S — two parallel migration PRs cannot both merge — none
- E2.3 File-lane map (`docs/planning/lanes.md`): which paths each agent owns to cut conflicts; CODEOWNERS mirrors it — S — CODEOWNERS present — none

**E3 CI strength**
- E3.1 e2e job: bring up the compose stack in CI (`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --wait`), `APP_ENV=test E2E_HOOKS=true`, run `pnpm e2e` chromium, upload trace on failure — M — first-run + login spec green in CI, under 10 min — none
- E3.2 Migration safety: up/down/up already present; add `alembic check` (model/migration drift), single-head assert, and RLS lint test (every table with `household_id` has `ENABLE`+`FORCE RLS` and a policy; query `pg_policies`) — M — adding a table without RLS fails CI (hard rule 10) — none
- E3.3 Money lint: ruff custom rule / grep gate banning `float` in `app/money|metrics|importers|tax` and literal tax-figure patterns outside `tax/config` — S — seeded violation fails — Ledger input
- E3.4 Coverage floor via `pytest --cov` with ratchet file (fail on decrease, not absolute) — S — number stored in repo — none
- E3.5 Switch informational pip-audit/pnpm-audit to blocking on high+ with an expiring allowlist file — S — new high CVE blocks — Bulwark input
- E3.6 Pin actions by SHA and enable Dependabot grouping for actions; `permissions: contents: read` stays — S — dependabot.yml grouped — none
- E3.7 Path-aware `detect` (docs-only PRs skip backend/web/e2e, still run gitleaks and `ci-required`) — S — docs PR finishes in <2 min — E1.4

**E4 Test strategy (Sounding)**
- E4.1 Test DB isolation: per-xdist-worker database cloned from a migrated template (`CREATE DATABASE ... TEMPLATE`), transaction-rollback per test with SAVEPOINT for unit/API tests, truncation only for tests that commit — M — `pytest -n auto` green, no cross-test leakage, suite wall time down — none
- E4.2 Fixture library `backend/tests/fixtures/` with deterministic factories (household, two persons, accounts, txns), frozen clock, seeded Faker; synthetic Plaid sandbox JSON and SimpleFIN payloads recorded as files, never live — M — Tide/Ledger stories consume it — Tide
- E4.3 Flaky policy: `--reruns 0`; any flake gets an issue and quarantine marker `@pytest.mark.quarantine` run in a non-blocking job — S — marker exists
- E4.4 Acceptance-as-code: each phase's acceptance table becomes a `tests/acceptance/phase_N/` marker set; `phase-acceptance-checker` runs `pytest -m "phase_N"` plus e2e and attaches output — M — report evidence is generated, not narrated — E3.1
- E4.5 Property/contract tests for importers (idempotent re-import) and money (Decimal round-trip) — owned by Ledger/Tide, harness by Sounding — S

**E5 Release and versioning**
- E5.1 Version = calver-less semver, single source in `VERSION`; conventional commits enforced by a `commit-msg` hook in `.githooks` and a PR-title check — S — `feat!` bumps major
- E5.2 `release.yml`: on `main` merge with release label (or phase-end), run release-please or a 40-line script to produce CHANGELOG.md, tag `vX.Y.Z` and `phase-N`, build and push images to GHCR (private) with SBOM (syft) and provenance — M — tag produces images by digest — E1.1
- E5.3 Release notes handoff to Beacon from the changelog — S

**E6 Deploy to the owner's host (Phase 9 prerequisite, built against a fake host now)**
- E6.1 `infra/deploy/` pull-based deploy: host runs a systemd timer / `docker compose pull && up -d` against a pinned image digest list; no inbound CI-to-host SSH (host is Tailscale-only). CI cannot reach it, so **pull, not push** — M — fully exercised against a local VM/compose-in-compose "staging" — E5.2
- E6.2 Migration-before-app ordering and automatic rollback: `migrate` one-shot, healthcheck gate, previous digest retained, `make rollback` — M — failed health restores prior digest in the drill — E6.1
- E6.3 Backup-before-deploy hook and restore drill in CI against `scripts/backup.sh`/`restore.sh` using a synthetic dataset (encrypted dump, restore into throwaway DB, row-count and RLS check) — M — nightly workflow green — none
- E6.4 `docs/runbook.md` deploy/rollback/key-rotation sections; owner queue lists only: host, Tailscale auth key, CF token, backup target — S

## 3. Risks

- **Stale-base merges and silent conflicts (high).** Retire with E2.1 plus strict required checks; measure: count PRs needing rebase per window.
- **Self-merge without a real reviewer.** Reviewers are agents on the same budget; sign-off bound to head SHA (E1.3) so late edits are re-reviewed. Spike: run it on one PR before enabling required.
- **CI minutes/cost on a private repo.** e2e + compose build are the expensive jobs; gate by paths (E3.7) and cache buildx layers (`type=gha`). Spike: time the e2e job in the first run.
- **Usage-window exhaustion** (hit 2026-11-18). CI is free of tokens; push more verification into CI and fewer reviewer runs. Cap parallel implementers at 3 and run Bulwark once per PR, not per commit.
- **Parallel migrations** (E2.2).
- **e2e flakiness from WebAuthn/CDP and fresh-instance requirement.** Each CI run is a fresh stack, which suits it; keep one spec until stable.
- **Gitleaks on full history each PR** is slow later; keep `fetch-depth: 0` only on main pushes, diff-range on PRs.

## 4. Challenge

PLAN.md Section 14 and the kickoff skill assume "stop at end of each phase for review" and a `--no-ff` merge of long-lived phase branches. That was a sound control when a human was the gate; now it is a stale ritual that creates the exact big-bang merge and stale-base conflicts we are suffering. Gate on **evidence per PR**, release per phase. Second: BRIEF says "tagged phase-1" on `main` as if phases are the unit of delivery; the real unit should be the PR and the phase tag a label on a green commit. Third, I doubt PLAN's Phase 9 "Backups + restore drill" as a late item: an untested backup is not a backup, and the drill (E6.3) is cheap in CI now with synthetic data, so pull it into this quarter.

## 5. Token savers

- Reviewers receive `git diff --stat` plus the changed-file list, and read files on demand; no full-repo context. Path-triggered reviewers only (E1.3).
- Agents run `make check` equivalents and read only the failing job's tail (`gh run view --log-failed | tail -80`), never whole logs.
- Fixture library and factories (E4.2) stop each agent re-inventing test data.
- `scripts/wt-new.sh` and one-line "gate status" script (`make gate`: lint, tests for changed paths, one-head check) so agents do not re-derive the command list from CLAUDE.md.
- Aggregate required check (E1.4) means agents poll one status, not ten.
- Worktree agents get a pre-fetched base, avoiding wasted runs rebasing and re-reading drifted files.

## Concrete merge gate list (required before merge to `main`)

1. `ci-required` aggregate green, composed of: backend ruff + format + mypy (strict), pytest with `CI=1` (no skips of real-service tests), coverage ratchet; web lint + typecheck + vitest + build.
2. Alembic: single head, `alembic check` clean, up/down/up as `holdfast_owner`, RLS-policy lint.
3. Money/tax lint (no `float`, no hardcoded tax figures), plus idempotent-import tests for importer changes.
4. gitleaks (PR range) and pre-commit parity; dependency audit blocking at high.
5. compose: `config -q` all three overlays, `check-compose-ports.py` (dev, base, prod), Caddy validate, image build.
6. e2e smoke (Playwright, chromium) on any change touching `web/`, `backend/`, `infra/`, or compose.
7. `review-gate`: Bulwark sign-off always; Ledger / Tally / Cipher / Prism by path; each bound to current head SHA.
8. Branch up-to-date with `main` (strict), linear history, conventional-commit PR title.
9. Phase end only: Sounding runs `pytest -m phase_N` plus e2e, writes `docs/phases/PHASE-N.md` with generated evidence, Keel tags after the final PR merges. No owner stop unless owner-queue items are BLOCKED.
