#!/usr/bin/env bash
# Refuse real financial data in the repository (ADR 0019). Used by .githooks/pre-commit and CI.
#   check-private-data.sh tree     every tracked file (CI)
#   check-private-data.sh staged   the staged change, plus the private denylist if one exists
#   check-private-data.sh range <rev-range>   every file added in any commit of the range, plus
#                                  the denylist over the range's added lines (pre-push)
# Output is file names only. A denylist hit never prints the matching text.
set -euo pipefail
mode="${1:-tree}"
allow='^(docs/brand/[^/]+\.pdf|docs/research/[^/]+\.pdf|backend/tests/fixtures/.+\.sanitized\.[a-z]+|backend/tests/fixtures/.+\.csv)$'
types='\.(pdf|csv|ofx|qfx|qbo|xls|xlsx|zip)$'
fail=0

if [ "$mode" = staged ]; then
  files=$(git diff --cached --name-only --diff-filter=ACMR)
elif [ "$mode" = range ]; then
  range="${2:?range mode needs a rev range}"
  # Every commit counts: a file added and then deleted in the same push is still published.
  files=$(git log --format= --name-only --diff-filter=ACMR "$range" | sort -u)
else
  files=$(git ls-files)
  ignored=$(git ls-files -ci --exclude-standard)
  if [ -n "$ignored" ]; then
    echo "private-data: tracked files that .gitignore excludes (added with -f?):" >&2
    echo "$ignored" >&2
    fail=1
  fi
fi

bad=$(printf '%s\n' "$files" | grep -Ei "$types" | grep -Ev "$allow" || true)
if [ -n "$bad" ]; then
  echo "private-data: statement-type files outside the fixture allowlist:" >&2
  echo "$bad" >&2
  fail=1
fi

paths=$(printf '%s\n' "$files" | grep -E '(^|/)\.env($|\.)|^data/|^secrets/' | grep -v '\.env\.example$' || true)
if [ -n "$paths" ]; then
  echo "private-data: private paths:" >&2
  echo "$paths" >&2
  fail=1
fi

# Optional: fixed strings the owner never wants in a commit (names, account digits, employer).
# One per line, kept outside the repository.
deny="${HOLDFAST_PRIVATE_DENYLIST:-$HOME/.local/share/holdfast-private/denylist.txt}"
if [ "$mode" = staged ] && [ -s "$deny" ]; then
  if git diff --cached --no-color -U0 | grep -E '^\+' | grep -qiFf "$deny"; then
    echo "private-data: the staged change contains a string from your private denylist. Not committed." >&2
    fail=1
  fi
fi
if [ "$mode" = range ] && [ -s "$deny" ]; then
  if git log -p --no-color -U0 --format= "$range" | grep -E '^\+' | grep -qiFf "$deny"; then
    echo "private-data: a commit in the pushed range contains a string from your private denylist." >&2
    fail=1
  fi
fi
exit "$fail"
