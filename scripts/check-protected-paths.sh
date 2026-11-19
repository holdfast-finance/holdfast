#!/usr/bin/env bash
# PR gate: protected paths, weakened guardrails and test removals/skips need the `bulwark-approved` label.
# Env: BASE_SHA, HEAD_SHA, LABELS (comma-separated, read LIVE by the workflow; empty on synchronize).
# Runs from the BASE branch (review-gate, pull_request_target). It only reads the PR head as git data.
# SDLC.md section 2 / section 3.
set -euo pipefail

base="${BASE_SHA:?}"
head="${HEAD_SHA:?}"
approved=false
case ",${LABELS:-}," in *,bulwark-approved,*) approved=true ;; esac

problems=()
range="$base...$head"

protected_re='^(\.github/|\.claude/|CLAUDE\.md$|\.githooks/|\.gitleaks\.toml$|\.gitleaksignore$|backend/pyproject\.toml$|web/(package\.json|vitest\.config\.[^/]*|playwright\.config\.[^/]*)$|backend/app/security/|backend/app/agent/guardrails\.py$|docker-compose[^/]*\.yml$|infra/docker-compose[^/]*\.yml$|infra/(caddy|postgres)/|scripts/(check-protected-paths\.sh|check-compose-ports\.py|audit_gate\.py|protect-main\.sh|gen-keys\.py|ci_plan\.py|ci_required_eval\.py|main_guard\.py|ci_minutes\.py|ci_local\.py)$|scripts/tests/|(.*/)?conftest\.py$)'
while IFS= read -r -d '' f; do
  [ -n "$f" ] && problems+=("protected path touched: $f")
done < <(git -c core.quotePath=false diff -z --no-renames --name-only "$range" | grep -zE "$protected_re" || true)

# Added lines (no file headers) of a diff over the given pathspecs.
added() { git diff --no-renames -U0 --diff-filter=AM "$range" -- "$@" | grep -E '^\+[^+]' || true; }
# Net count of lines matching a regex: removed minus added, over every non-added file in the pathspecs.
net_removed() {
  local re="$1"; shift
  local rem add
  rem=$(git diff --no-renames -U0 --diff-filter=DM "$range" -- "$@" | grep -E '^-[^-]' | grep -cE "$re" || true)
  add=$(git diff --no-renames -U0 --diff-filter=AM "$range" -- "$@" | grep -E '^\+[^+]' | grep -cE "$re" || true)
  echo $((rem - add))
}

# RLS migrations: any added/modified migration that mentions RLS or policies.
while IFS= read -r -d '' f; do
  [ -z "$f" ] && continue
  if git show "$head:$f" 2>/dev/null | grep -qiE 'row level security|create policy|alter policy|drop policy|force row'; then
    problems+=("RLS migration touched: $f")
  fi
done < <(git -c core.quotePath=false diff -z --no-renames --name-only --diff-filter=AM "$range" -- backend/migrations/versions)

while IFS= read -r -d '' f; do
  [ -n "$f" ] && problems+=("test deleted: $f")
done < <(git -c core.quotePath=false diff -z --no-renames --name-only --diff-filter=D "$range" -- backend/tests)

while IFS= read -r -d '' f; do
  [ -n "$f" ] && problems+=("migration deleted: $f")
done < <(git -c core.quotePath=false diff -z --no-renames --name-only --diff-filter=D "$range" -- backend/migrations/versions)

weakened=$(added backend/migrations/versions | grep -iE 'alter policy|drop policy|disable row level security|no force row' || true)
[ -n "$weakened" ] && problems+=("RLS weakened in migration diff: $(echo "$weakened" | head -3 | tr '\n' ' ')")

privs=$(added backend/migrations/versions | grep -iE 'alter role|\bgrant\b|bypassrls|security definer' || true)
[ -n "$privs" ] && problems+=("role/privilege change in migration diff (ALTER ROLE/GRANT/BYPASSRLS/SECURITY DEFINER): $(echo "$privs" | head -3 | tr '\n' ' ')")

py_skip='pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|pytest\.importorskip|importorskip\(|@unittest\.skip|collect_ignore|xfail'
skips=$(added backend/tests | grep -E "$py_skip" || true)
[ -n "$skips" ] && problems+=("newly added skip/xfail/importorskip/collect_ignore under backend/tests: $(echo "$skips" | head -3 | tr '\n' ' ')")

web_skip='\b(it|test|describe)\.(skip|only|todo)\b|\b(xit|xdescribe|xtest|fit|fdescribe)\('
web_tests=(':(glob)web/**/*.test.ts' ':(glob)web/**/*.test.tsx' ':(glob)web/**/*.spec.ts' ':(glob)web/**/*.spec.tsx' ':(glob)web/e2e/**')
webskips=$(added "${web_tests[@]}" | grep -E "$web_skip" || true)
[ -n "$webskips" ] && problems+=("newly added skip/only/todo in web tests: $(echo "$webskips" | head -3 | tr '\n' ' ')")

n=$(net_removed '^[-+][[:space:]]*(async[[:space:]]+)?def test_' backend/tests)
[ "$n" -gt 0 ] && problems+=("net loss of $n 'def test_' function(s) under backend/tests")
n=$(net_removed '^[-+][[:space:]]*(it|test)(\.each\([^)]*\))?\(' "${web_tests[@]}")
[ "$n" -gt 0 ] && problems+=("net loss of $n web test case(s) (it/test)")

if [ "${#problems[@]}" -eq 0 ]; then
  echo "No protected paths touched, no tests removed or skipped."
  exit 0
fi
printf '%s\n' "${problems[@]}"
if $approved; then
  echo "Label bulwark-approved present on this head: allowed (the Bulwark sign-off must name the head SHA)."
  exit 0
fi
echo "::error::Protected-path or test-removal changes need the 'bulwark-approved' label (Bulwark verdict for this head SHA)."
exit 1
