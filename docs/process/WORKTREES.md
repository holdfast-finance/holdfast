# Worktrees: never build on a stale base

Worktree agents once started on an old commit (W1). Rules:

- Create worktrees with `scripts/wt-new.sh <branch> [base=origin/main]` (`./scripts/dev.ps1 wt-new <branch>`). It fetches, branches from the fresh base, runs `scripts/check-base.sh`, and symlinks the main repo `.env` (never read or copied).
- It refuses a branch that already exists on origin unless `--resume`; resume checks the branch out and prints how far behind `origin/main` it is. It does not fast-forward.
- `scripts/check-base.sh [N]` exits non-zero when HEAD is more than N (default 0) commits behind `origin/main`. Briefs for worktree agents should start with `git fetch origin && git checkout -B <branch> origin/main && scripts/check-base.sh`.
- `make sync` (`dev.ps1 sync`) fast-forwards `main`; on other branches it prints ahead/behind.
- `make test-locked` runs `make test` under `flock /tmp/holdfast-make-test.lock` so parallel worktrees do not fight over the shared test DB. The pwsh variant uses its own exclusive lock file, not shared with the flock one.
