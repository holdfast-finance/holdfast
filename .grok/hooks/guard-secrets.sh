#!/usr/bin/env bash
# PreToolUse adapter for Grok. All logic lives in scripts/agent_guard.py (shared with Codex).
# Fails closed: if the guard cannot run, the edit is blocked. Never prints the payload.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 2
[ -f "$root/scripts/agent_guard.py" ] || { echo "guard-secrets: BLOCKED - shared guard missing" >&2; exit 2; }
python3 -I "$root/scripts/agent_guard.py"
rc=$?
[ "$rc" -eq 0 ] && exit 0
exit 2
