# F7b Free-tier CI: main-guard, minutes budget, ci-local — Harbor (sonnet) — size M
Goal: ADR 0013. The gate works on GitHub Free with a private repo: no rulesets, 2,000 Actions minutes a month.
Read: docs/adr/0013-free-tier-cicd.md (all of it), .github/workflows/ci.yml, .github/workflows/review-gate.yml,
scripts/check-protected-paths.sh, Makefile (targets list only), docs/process/SDLC.md §2.
Do:
1. `.github/workflows/main-guard.yml`: on `push` to `main`, `permissions: contents: read, issues: write, checks: read, pull-requests: read`.
   For each commit in `before..after`, it must be the merge commit of exactly one merged PR
   (`gh api repos/$REPO/commits/$SHA/pulls`, with `merge_commit_sha == SHA`), and that PR's head SHA
   must have successful `ci-required` and `review-gate` check runs. If `before` isn't an ancestor of
   `after` (a force-push), fail. On any failure: fail the job and open an issue labelled `main-guard`
   (create the label if it's missing) listing the commit and the reason. Never revert automatically.
   Don't check out or run anything from the pushed commits beyond reading the API.
2. Cut billed jobs: merge `detect`, `secrets`, `scripts-test` and `compose` into one `checks` job.
   Keep `backend` and `web` separate and path-gated. `ci-required` still has one stable name and treats
   skipped-for-a-reason as pass. Missing manifests still fail (the F7 finding).
3. Push-to-main CI: skip `backend`/`web` when the merged PR's head was up to date with its base at
   merge time (the head SHA's parent chain contains the `before` SHA). Otherwise run them. When in doubt, run.
4. `make ci-minutes`: sums billed minutes for the current month (`gh api .../actions/runs?created=>=YYYY-MM-01`
   plus `/timing`, `billable.UBUNTU.total_ms`) and prints used/2000. Add the pwsh 7 equivalent in dev.ps1.
5. `make ci-local PR=<n>`: runs the gate locally against a PR's head in a temporary worktree (fetch
   `pull/<n>/head`): lint, test-locked, lint-imports, Alembic single head, gitleaks on `base..head`,
   compose config, `check-protected-paths.sh` on the PR diff. It prints a summary naming the head SHA,
   ready to paste into a PR comment. Never read `.env` (symlink it as wt-new.sh does).
6. Update SDLC.md §2 to point at ADR 0013 for main protection and the minutes fallback.
Verify: actionlint (docker); self-tests for any new script logic (seeded cases: commit with no PR, PR
with red ci-required, force-push); `make lint`. Open the PR. It's a protected path, so Bulwark reviews it.
Report billed minutes of your own PR's CI run before and after the job merge.
Branch `chore/F7b-free-tier-ci` (use scripts/wt-new.sh). Trailer `Co-Authored-By: Harbor (Holdfast) <agents@holdfastfinance.app>`.
PR title is a Conventional Commit. Body ends `— Harbor, Holdfast`. Don't merge.
