# 0013. CI/CD on GitHub's free tier (no paid plans until there's revenue)

- Status: Accepted
- Date: 2026-11-19
- Deciders: owner ("we are not going to pay for anything … CI/CD has to work on free tier"); Keel

## Context
`holdfast-finance` is on GitHub Free and the repo is private (until the open-source milestone, PLAN
17.6). On that plan:
- **Rulesets and branch protection aren't available for private repos.** F7's `scripts/protect-main.sh`
  returns 403, so GitHub can't stop a push to `main`, a force-push or a merge without checks.
- **Actions gets 2,000 minutes a month** for private repos, billed per job and rounded up per job.
  A CI run today is 7 jobs (~2 min wall clock, ~10–14 billed minutes), plus `review-gate`. At W1's pace
  (about 10 PRs a day, several pushes each, plus pushes to main) the quota runs out in about a week.
- gitleaks already runs from its CLI image (the action needs a paid org license), and Dependabot is free.

## Decision
1. **No paid plan, service or license** for CI/CD until the owner says there's money for it. Any
   proposal that needs one is rejected, or deferred with a free alternative.
2. **Protect `main` by prevention where it's free, and by detection elsewhere:**
   - *Prevention:* Anvil and Flint have Read-role GitHub access and push only to a fork (ADR 0012),
     so they can't touch `main` at all. Keel merges only with
     `gh pr merge --squash --match-head-commit <sha> --subject …` after the gate is green.
   - *Detection:* a `main-guard` workflow runs on every push to `main`. It checks each new commit
     against `GET /repos/{repo}/commits/{sha}/pulls`. The commit has to be the squash merge of exactly
     one merged PR, and that PR's last head SHA has to have green `ci-required` and `review-gate`
     check runs (plus `bulwark-approved` if `review-gate` required it). If not, the run fails and
     opens an issue labelled `main-guard`. Keel reverts through a PR at the start of its next window.
     A red `main-guard` is part of the weekly digest.
   - `scripts/protect-main.sh` stays in the repo, ready for the day the repo goes public or a plan is paid for.
3. **Fit CI into 2,000 minutes a month** (target ≤ 1,500, which leaves headroom):
   - Fewer billed jobs: merge the cheap jobs (`detect`, `secrets`, `scripts-test`, `compose`) into one
     `checks` job, so a run bills about 4 jobs instead of 7.
   - Skip what didn't change: path-based detection already gates backend and web. A docs-only PR runs
     only `checks` and `ci-required`, and `ci-required` reports success when the jobs it needs were
     skipped for the right reason.
   - Keep `cancel-in-progress`. Builders push once at the end of a run, not after every commit
     (already in briefs as "verify once, at the end").
   - Push-to-`main` runs only `main-guard` plus a full CI run when the merged PR's branch was behind `main`.
     When it was up to date, the squash tree equals the tested tree.
   - e2e (F12) runs on merges to `main` that touch `web/`, `backend/` or `infra/`, and never on a cron.
   - Track it: `make ci-minutes` totals billed minutes for the month from the runs API
     (`gh api repos/{repo}/actions/runs/{id}/timing`). Keel logs it in each standup.
4. **When the quota runs out**, the gate moves to Keel's machine as `make ci-local`. The trusted
   script runs the same checks as CI (ruff, mypy, pytest, web lint/typecheck/test, lint-imports,
   one Alembic head, gitleaks on the PR range, compose config, `check-protected-paths.sh` on the
   PR diff). It does not run the pull request's Makefile, and it does not read or symlink the
   operator `.env`; throwaway keys are written into the temp worktree only. Keel posts the output
   as a PR comment naming the head SHA, and merges only on a clean result. SDLC §2 already allows this.
   **Trust limit:** `ci-local` runs the PR's code (tests, linters, installs) with the operator's
   privileges. Use it only for PRs from trusted in-org lane agents, never fork PRs, until it is
   sandboxed. Follow-up story F7c: run it in a container with an allowlisted env and no network
   (and a scrubbed env, since the steps need many tool variables).
   `main-guard` binds each check to its trusted trigger: `review-gate` counts only from a
   `pull_request_target` run that lists the merged PR with base `main` (empty list fails closed);
   `ci-required` counts only from `pull_request` runs of `ci.yml`, and those must also list the
   merged PR with base `main` (a sibling PR against a crafted base cannot forge it). A
   `base_ref_changed` event on the merged PR's timeline fails the push. `ci.yml` runs
   `pull_request` only for base `main`.
   **Accepted risk (L3):** `main-guard` detects, it does not prevent. A bad push to `main` is found
   after the fact and reverted through a PR.
5. **Later options (owner decisions, no cost):**
   - **Making the repo public** removes both limits: rulesets become available and Actions minutes are
     unlimited for public repos. It's an AGPL project going open source anyway. The cost is publishing
     the code before the planned milestone.
   - A **self-hosted runner** on the owner's host (free minutes) for same-repo branches only, never for
     fork PRs. That needs its own ADR and a Bulwark review first.

## Consequences
- `main` isn't technically locked against the owner's credentials. A mistake gets detected and
  reverted within one window instead of being blocked. Bulwark's "unprotected main" finding stays
  open as an accepted owner risk until the repo goes public.
- CI changes in F7b (Harbor) are a protected path and need a Bulwark verdict.
- Phase planning counts CI minutes as a budget next to usage windows.
