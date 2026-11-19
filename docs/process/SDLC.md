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
   ─► Release (phase end or on demand): tag, CHANGELOG, images, Beacon notes
   ─► Deploy (pull-based on the owner's host, once it exists)
```

- **Agents from other vendors** (Anvil = Codex, Flint = Grok) work under ADR 0012 and `AGENTS.md`, pulling from `docs/planning/HANDOFF.md`. Same gate; only Keel merges.
- **Unit of work:** one story = one branch `feat|fix|chore/<story-id>-<slug>` = one PR. S or M only; L must be split before it's scheduled.
- **Phases are milestones**, not long-lived branches. `phase-<n>` is a tag on the green `main` commit after Sounding's acceptance run.
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

**Protected paths** need a separate Bulwark verdict even for one-line changes: `.github/`, `.claude/`, `.codex/`, `.agents/`, `CLAUDE.md`, `AGENTS.md`, `.githooks/`, `backend/app/security/`, RLS migrations, `agent/guardrails.py`, `docker-compose*.yml`, `infra/caddy/`.

Until the CI pieces exist (Sprint 1, F7), Keel runs the same list locally (`make test && make lint && make ports`, `gitleaks git` over the PR range, `uv run pip-audit` and `pnpm audit --audit-level high`) and dispatches the reviewer runs by hand. Protected-path sign-off comes from a **separate** Bulwark run that names the head SHA; F7 adds CODEOWNERS for protected paths and a required check for that sign-off.

## 3. Autonomy guardrails

- Agents never touch: production Plaid keys or Items, DNS, Tailscale ACLs, GitHub org/repo settings, money, legal, real personal data. Those go to the owner queue. Branch protection/rulesets are applied once by F7; any later change needs a Bulwark verdict and is listed in the weekly digest.
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
