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
  if [ "$remote_exists" = 0 ]; then
    echo "wt-new: --resume but '${branch}' is not on origin." >&2
    exit 1
  fi
  git worktree add -B "$branch" "$path" "origin/${branch}" >/dev/null
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
