# Worktrees: never build on a stale base

Worktree agents once started on an old commit (W1). Rules:

- Create worktrees with `scripts/wt-new.sh <branch> [base=origin/main]` (`./scripts/dev.ps1 wt-new <branch>`). It fetches, branches from the fresh base, runs `scripts/check-base.sh`, and symlinks the main repo `.env` (never read or copied).
- A branch name that fails `git check-ref-format --branch` is rejected before any fetch or worktree add.
- It refuses a branch that already exists on origin unless `--resume`. Resume checks out the local branch without moving its tip. If the branch is not local yet, it is created from `origin/<branch>` (never with `git worktree add -B`, which would reset the tip). It prints how far behind `origin/main` that tip is and does not fast-forward.
- `scripts/check-base.sh [N]` exits non-zero when HEAD is more than N (default 0) commits behind `origin/main`. Briefs for worktree agents should start with `git fetch origin && git checkout -B <branch> origin/main && scripts/check-base.sh`.
- `make sync` (`dev.ps1 sync`) fast-forwards `main`; on other branches it prints ahead/behind.
- `make test-locked` and `dev.ps1 test-locked` take `${XDG_RUNTIME_DIR:-/tmp}/holdfast-make-test.lock` so parallel worktrees do not fight over the shared test DB. `flock` waits for the holder. PowerShell retries on a sharing violation until `HOLDFAST_TEST_LOCK_WAIT` seconds (default 1800) and then errors.
