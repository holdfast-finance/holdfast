#!/usr/bin/env bash
# Create an agent worktree on a fresh base and link the main repo's .env (never read or copied).
# Usage: scripts/wt-new.sh <branch> [base=origin/main] [--resume]
set -euo pipefail

resume=0
args=()
for a in "$@"; do
  if [ "$a" = "--resume" ]; then resume=1; else args+=("$a"); fi
done
if [ "${#args[@]}" -lt 1 ] || [ "${#args[@]}" -gt 2 ]; then
  echo "usage: wt-new.sh <branch> [base=origin/main] [--resume]" >&2
  exit 2
fi
branch="${args[0]}"
base="${args[1]:-origin/main}"

# Reject before fetch or worktree add. --branch also expands shorthand (@{-1}); require the
# name unchanged so resume cannot attach a different ref than the one requested.
if ! canon="$(git check-ref-format --branch "$branch" 2>/dev/null)"; then
  echo "wt-new: branch name '${branch}' is not a valid refname." >&2
  exit 1
fi
if [ "$canon" != "$branch" ]; then
  echo "wt-new: branch name '${branch}' is not a valid refname." >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
common="$(git rev-parse --path-format=absolute --git-common-dir)"
main_root="$(dirname "$common")"
slug="${branch//\//-}"
path="${main_root}/.claude/worktrees/${slug}"

git fetch origin --quiet
remote_exists=0
if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then remote_exists=1; fi

if [ "$remote_exists" = 1 ] && [ "$resume" = 0 ]; then
  echo "wt-new: branch '${branch}' already exists on origin; use --resume to continue it." >&2
  exit 1
fi
if [ -e "$path" ]; then
  echo "wt-new: ${path} already exists." >&2
  exit 1
fi

if [ "$resume" = 1 ]; then
  local_exists=0
  if git show-ref --verify --quiet "refs/heads/${branch}"; then local_exists=1; fi
  if [ "$local_exists" = 0 ] && [ "$remote_exists" = 0 ]; then
    echo "wt-new: --resume but '${branch}' is neither local nor on origin." >&2
    exit 1
  fi
  # Attach the existing local tip. -B would move it to origin/$branch and drop unpushed commits.
  # Create from origin only when this repo has no local branch yet, and never with -B.
  if [ "$local_exists" = 1 ]; then
    git worktree add "$path" "$branch" >/dev/null
  else
    git worktree add -b "$branch" "$path" "origin/${branch}" >/dev/null
  fi
  behind="$(git -C "$path" rev-list --count "HEAD..origin/main")"
  echo "wt-new: resumed ${branch}; ${behind} commit(s) behind origin/main (no ff applied)." >&2
else
  git worktree add -b "$branch" "$path" "$base" >/dev/null
  if ! (cd "$path" && "${script_dir}/check-base.sh" 0 >&2); then
    echo "wt-new: stale base '${base}'; worktree left at ${path} for you to clean up." >&2
    exit 1
  fi
fi

if [ -f "${main_root}/.env" ] && [ ! -e "${path}/.env" ]; then
  ln -s "${main_root}/.env" "${path}/.env"
fi
echo "$path"
