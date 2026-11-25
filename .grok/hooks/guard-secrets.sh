#!/usr/bin/env bash
# PreToolUse adapter. Normalizes Grok and Cursor edit payloads, then calls the
# shared Claude guard. Does not edit .claude/. Exit 2 blocks the write.
set -euo pipefail
input="$(cat)"
truncated="$(jq -r 'if .toolInputTruncated == true then "yes" else "no" end' <<<"$input")"
if [ "$truncated" = "yes" ]; then
  echo "guard-secrets: BLOCKED — tool input was truncated, so the secret scan is incomplete" >&2
  exit 2
fi
root="${CLAUDE_PROJECT_DIR:-${GROK_WORKSPACE_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
normalized="$(jq -c '
  .tool_input = (.tool_input // {})
  | .tool_input.file_path = (
      .tool_input.file_path
      // .tool_input.path
      // .tool_input.notebook_path
      // .tool_input.target_notebook
      // ""
    )
  | .tool_input.content = (
      if .tool_input.content != null then .tool_input.content
      else .tool_input.contents
      end
    )
' <<<"$input")"
printf '%s' "$normalized" | bash "$root/.claude/hooks/guard-secrets.sh"
