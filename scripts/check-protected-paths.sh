#!/usr/bin/env bash
# PR gate: protected paths and test deletions/skips need the `bulwark-approved` label.
# Env: BASE_SHA, HEAD_SHA, LABELS (comma-separated PR labels). SDLC.md section 2 / section 3.
set -euo pipefail

base="${BASE_SHA:?}"
head="${HEAD_SHA:?}"
approved=false
case ",${LABELS:-}," in *,bulwark-approved,*) approved=true ;; esac

problems=()
range="$base...$head"

protected_re='^(\.github/|\.claude/|CLAUDE\.md$|\.githooks/|backend/app/security/|backend/app/agent/guardrails\.py$|docker-compose[^/]*\.yml$|infra/caddy/)'
while IFS= read -r f; do
  [ -n "$f" ] && problems+=("protected path touched: $f")
done < <(git diff --name-only "$range" | grep -E "$protected_re" || true)

# RLS migrations: any added/modified migration that mentions RLS or policies.
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if git show "$head:$f" 2>/dev/null | grep -qiE 'row level security|create policy|drop policy|force row'; then
    problems+=("RLS migration touched: $f")
  fi
done < <(git diff --name-only --diff-filter=AM "$range" -- backend/migrations/versions)

while IFS= read -r f; do
  [ -n "$f" ] && problems+=("test deleted: $f")
done < <(git diff --name-only --diff-filter=D -M "$range" -- backend/tests)

skips=$(git diff -U0 --diff-filter=AM "$range" -- backend/tests |
  grep -E '^\+[^+]' | grep -E 'pytest\.mark\.skip|pytest\.skip\(|@unittest\.skip' || true)
if [ -n "$skips" ]; then
  problems+=("newly added skip marker(s) under backend/tests: $(echo "$skips" | head -3 | tr '\n' ' ')")
fi

if [ "${#problems[@]}" -eq 0 ]; then
  echo "No protected paths touched, no tests removed or skipped."
  exit 0
fi
printf '%s\n' "${problems[@]}"
if $approved; then
  echo "Label bulwark-approved present: allowed (the Bulwark sign-off must name the head SHA)."
  exit 0
fi
echo "::error::Protected-path or test-removal changes need the 'bulwark-approved' label (Bulwark verdict for this head SHA)."
exit 1
