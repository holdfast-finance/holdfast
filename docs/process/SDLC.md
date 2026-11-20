# SDLC — how work flows at Holdfast

Owner decision 2026-11-19: **full autonomy**. The agent team runs the whole lifecycle. Gates are mechanical (CI) plus independent agent verdicts (Bulwark, Sounding, lane reviewers), never "ask the owner". The owner sees a weekly digest and the owner queue in `docs/STATUS.md`.

## 1. The flow

```
Backlog (ROADMAP.md) ─► Sprint plan (Keel, per usage window)
   ─► Contract (Lattice, per phase/epic)  ─► Gadfly challenge (new epics only)
   ─► Story brief (Keel writes docs/planning/briefs/<id>.md)
   ─► Build run (lane agent, own branch/worktree off origin/main)
   ─► PR (gh pr create, template checklist)
   ─► CI: ci-required (aggregate)  +  review-gate (agent sign-offs bound to head SHA)
   ─► Keel merges (squash) ─► main
   ─► Release (Keel, on main): annotated v-tag + GitHub Release. Phase tags are milestones only.
   ─► Deploy (pull-based on the owner's host, once it exists)
```

- **Unit of work:** one story = one branch `feat|fix|chore/<story-id>-<slug>` = one PR. S or M only; L must be split before it's scheduled.
- **Phases are milestones**, not long-lived branches. `phase-<n>` is an annotated tag on the green `main` commit after Sounding's acceptance run. It is not a product release and it does not get a GitHub Release.
- **Public version releases** are an annotated `v` + SemVer tag and a GitHub Release on that same commit. Keel cuts them from `docs/process/RELEASES.md`. `make release-check` does not publish.
- **Only Keel merges.** Builders never merge their own PR.
- **Worktrees:** create them with `scripts/wt-new.sh` so the base is fresh; see `docs/process/WORKTREES.md`.

## 2. Merge gate (all required)

1. `ci-required` green: backend ruff, format, mypy strict, pytest (no skips of real-service tests), coverage ratchet; web lint, typecheck, vitest, build.
2. Database: one Alembic head, `alembic check` clean, up/down/up as `holdfast_owner`, RLS lint (every `household_id` table has ENABLE + FORCE RLS and a policy).
3. Money/tax lint: no `float` in money paths, no tax figures outside `tax/config`, idempotent-import tests for importer changes.
4. Secrets/supply chain: gitleaks on the PR range; dependency audit blocking at high (expiring allowlist); actions pinned by SHA.
5. Compose: config for all overlays, `check-compose-ports.py`, Caddy validate, image build.
6. e2e smoke (Playwright) when `web/`, `backend/`, `infra/` or compose change.
7. `review-gate`: Bulwark always for protected paths and new trust boundaries; Ledger (money), Tally (tax), Cipher (crypto), Prism (web) by path. Each sign-off names the head SHA; a later push invalidates it.
8. Branch up to date with `main`; Conventional Commit title; agent attribution trailer.

**Protected paths** need a separate Bulwark verdict even for one-line changes: `.github/`, `.claude/`, `CLAUDE.md`, `.githooks/`, `backend/app/security/`, RLS migrations, `agent/guardrails.py`, `docker-compose*.yml`, `infra/caddy/`.

Protected-path sign-off is a separate Bulwark verdict that names the head SHA (`bulwark-approved`); `review-gate` enforces it.

`main` is not ruleset-locked. The repo is private on GitHub Free, which has no rulesets or branch protection (ADR 0013). Keel is the only merger and uses `gh pr merge --squash --match-head-commit <sha>` after `ci-required` and `review-gate` are green on that SHA. The `main-guard` workflow detects anything else, including a force-push, fails, and opens a `main-guard` issue. It does not revert. `scripts/protect-main.sh` stays in the repo unused until the repo is public or a plan is paid for.

Actions for this private repo are capped at 2,000 minutes a month. `make ci-minutes` and `./scripts/dev.ps1 ci-minutes` print the UTC month's billed total as `used/2000`. When the quota is exhausted, Keel runs `make ci-local PR=<n>` (trusted commands and a throwaway `.env` in a temp worktree; not the PR Makefile and not the operator `.env`), pastes the summary (it names the head SHA), and merges only if that run is clean (ADR 0013). `ci-local` runs PR code with the operator's privileges, so use it only for PRs from trusted in-org lane agents, never fork PRs, until it is sandboxed (follow-up F7c: container, allowlisted env, no network). CI's `pull_request` trigger runs only for PRs into `main`; `phase/**` bases get no PR CI (fine under the trunk-based flow).

## 3. Autonomy guardrails

- Agents never touch: production Plaid keys or Items, DNS, Tailscale ACLs, GitHub org/repo settings, money, legal, real personal data. Those go to the owner queue. No paid plans (ADR 0013): on GitHub Free with a private repo there are no rulesets, so `main` is guarded by `main-guard` detection plus Keel-only merges, and CI fits 2,000 minutes a month with `make ci-local` as the fallback. `scripts/protect-main.sh` is applied only if the repo goes public; any change to it needs a Bulwark verdict and is listed in the weekly digest.
- Tide stays off live Plaid (fixtures only) until the production keys are out of the dev `.env`.
- No force-push, no history rewrite on `main`, no deleting tags. Rollback = revert PR.
- Security tests can't be deleted or skip-marked without a Bulwark verdict. CI enforces this from Sprint 1 (F7); until then Bulwark checks it by hand in every protected-path review.
- Deploys go only to targets that already exist (GitHub Pages site; the owner's host once provisioned). The Cloud preview stays with the owner's engineer.
- Weekly digest to the owner (Beacon writes `docs/digest/<date>.md`): shipped, risks, owner-queue changes, tokens used.

## 4. Attribution

Every commit ends with `Co-Authored-By: <Agent> (Holdfast) <agents@holdfastfinance.app>`. PR bodies end with `— <Agent>, Holdfast`. Never credit a model vendor.

## 5. Cadence (agent time, not calendar time)

| Ceremony | When | Who | Output |
|---|---|---|---|
| Sprint plan | Start of each usage window | Keel | ≤ 4 runs picked from the ready queue, models assigned, briefs written |
| Standup | After each merge batch | Keel | 3 lines in `docs/planning/SPRINTS.md` (below) |
| Contract + challenge | Before each phase/epic | Lattice, Gadfly | `PHASE-<n>-contract.md`, critique file |
| Review | Per PR | Path reviewers | SHA-bound sign-off |
| Acceptance | Phase end | Sounding | `docs/phases/PHASE-<n>.md` with generated evidence |
| Retro + recalibration | Every 5 windows | Keel | Size table updated from logged tokens |

### Standup format (no filler)

```
<window id> · done: <story ids merged> · doing: <ids, agent, model> · blocked: <id: reason | none> · tokens: <used>/<budget>
```

If nothing is blocked and nothing changed, the standup is one line. No narrative, no restating the plan.

## 6. Definition of done (story)

Merged to `main` through the gate; tests for the new behaviour; docs touched if user-visible (`docs/user/`); STATUS "Resume here" updated by Keel; tokens logged.
