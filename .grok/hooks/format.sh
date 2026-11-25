#!/usr/bin/env bash
# PostToolUse adapter. Points the shared formatter at Grok's file_path or path.
set -euo pipefail
input="$(cat)"
root="${CLAUDE_PROJECT_DIR:-${GROK_WORKSPACE_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
normalized="$(jq -c '
  .tool_input = (.tool_input // {})
  | .tool_input.file_path = (
      .tool_input.file_path
      // .tool_input.path
      // ""
    )
' <<<"$input")"
printf '%s' "$normalized" | bash "$root/.claude/hooks/format.sh"
exit 0
