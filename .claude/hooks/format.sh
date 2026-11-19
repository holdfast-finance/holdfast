#!/usr/bin/env bash
# PostToolUse formatter. Never blocks. No-ops until the toolchains exist (Phase 0).
input="$(cat)"
path="$(jq -r '.tool_input.file_path // ""' <<<"$input")"
root="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -f "$path" ] || exit 0

case "$path" in
  "$root"/backend/*.py)
    if [ -d "$root/backend/.venv" ]; then
      (cd "$root/backend" && uv run --quiet ruff format "$path" && uv run --quiet ruff check --fix --quiet "$path") >/dev/null 2>&1
    fi
    ;;
  "$root"/web/*|"$root"/site/*)
    case "$path" in
      *.ts|*.tsx|*.css|*.json|*.astro|*.md)
        d="$root/web"; [[ "$path" == "$root"/site/* ]] && d="$root/site"
        [ -x "$d/node_modules/.bin/prettier" ] && "$d/node_modules/.bin/prettier" --write --log-level silent "$path" >/dev/null 2>&1
        ;;
    esac
    ;;
esac
exit 0
