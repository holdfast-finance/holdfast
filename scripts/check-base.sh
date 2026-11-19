#!/usr/bin/env bash
# Stale-base guard: fail when HEAD is more than N commits behind origin/main (default 0).
# Usage: scripts/check-base.sh [N]
set -euo pipefail

max="${1:-0}"
case "$max" in '' | *[!0-9]*) echo "usage: check-base.sh [max-commits-behind]" >&2; exit 2 ;; esac

git fetch origin --quiet
base="$(git merge-base HEAD origin/main)"
behind="$(git rev-list --count "${base}..origin/main")"
if [ "$behind" -gt "$max" ]; then
  echo "check-base: HEAD is ${behind} commit(s) behind origin/main (max ${max})." >&2
  echo "check-base: recreate the branch: git checkout -B <branch> origin/main" >&2
  exit 1
fi
echo "check-base: ok (${behind} behind origin/main, max ${max})"
