#!/usr/bin/env bash
# Apply the `main` repository ruleset (run once by Keel after F7 merges; needs repo admin via `gh`).
# Required: ci-required and review-gate (strict/up to date), linear history, no force push, no deletion, PR required.
set -euo pipefail

repo="${1:-holdfast-finance/holdfast}"
name="main-protection"

body=$(
  cat <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [ { "context": "ci-required", "integration_id": 15368 },
                                    { "context": "review-gate", "integration_id": 15368 } ] } }
  ]
}
JSON
)

existing=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" || true)
if [ -n "$existing" ]; then
  echo "$body" | gh api -X PUT "repos/$repo/rulesets/$existing" --input - >/dev/null
  echo "Updated ruleset $name ($existing) on $repo"
else
  echo "$body" | gh api -X POST "repos/$repo/rulesets" --input - >/dev/null
  echo "Created ruleset $name on $repo"
fi
