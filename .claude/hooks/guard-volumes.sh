#!/usr/bin/env bash
# PreToolUse guard for Bash (H2, PHASE-2 contract section 6.1 row 6).
#
# Why: the owner's real data lives in the `holdfast-instance` compose project, on bind mounts
# under the instance directory (a 0700 directory outside the repo, ADR 0019). A stray prune,
# `rm -rf` or overwritten `instance.env` run from a Claude session would destroy it, and the
# backup is only as fresh as the last `docs/ops/backup.md` run. This hook blocks (exit 2):
#
#   1. `docker|podman volume rm|remove|prune`, which delete volumes by name or in bulk. The
#      sandbox project is torn down with `make sandbox-destroy`, which is allowed.
#   2. `docker|podman system prune ... --volumes` and `system reset` (the bulk forms).
#   3. A destructive file verb (rm, rmdir, unlink, shred, truncate, mv, find -delete) in a command
#      that also names the instance: the directory, `instance.env`, or the project name
#      `holdfast-instance`.
#   4. A write to the instance directory or `instance.env`: a redirect (`>`, `>>`, `>|`) whose
#      target names it, or cp, dd, tee, install, or sed -i in a command that names it. This
#      over-blocks a backup such as `cp "$DIR/instance.env" /safe/`; take backups from your own
#      terminal.
#
# "Names the directory" means the literal `HOLDFAST_INSTANCE_DIR`, its value from this shell, or
# the path in the pointer file `${XDG_CONFIG_HOME:-$HOME/.config}/holdfast/instance-dir` that
# `make instance-init` writes. The pointer makes the hook work in a session that never exported
# the variable.
#
# It matches on the whole command string, not per segment, after normalising it: backslash-newline
# is removed and every quote and backslash is deleted, so `d'ocker' "volume" rm`, `"rm" -rf` and
# `r\m` read as the plain command the shell would run. That fails closed: a command that merely
# mentions these phrases (a commit message, say) is blocked too. Put such text in a file and use
# `git commit -F`. The hook also fails closed when `jq` is missing or its input can't be parsed.
#
# What it can NOT catch (it is a guard rail against mistakes, not a sandbox):
#   - deletion through another language or tool: `python -c 'shutil.rmtree(...)'`, `perl`,
#     `node`, `git clean`, `rsync --delete`, a script file that was written earlier;
#   - deleting data through the services: `docker exec db dropdb|psql -c 'DROP ...'`,
#     `docker run -v <path>:/x alpine ...` where the path comes from a variable not named above;
#   - a path built at run time (`d=$(printf ...)`), a `cd` into the directory followed by a
#     relative `rm`, or a symlink pointing at it;
#   - `chmod`/`chown` that lock the owner out, and `ln -sf` swaps;
#   - any command outside a Claude session: the owner's terminal, and Codex or Grok workers.
#     Those workers are confined instead: scripts/fc-delegate.sh hides the instance directory and
#     the container sockets and refuses to start without them.
# The real protections are the 0700 bind mounts, `instance-up`'s mode check, and the backup.
set -uo pipefail

block() { echo "guard-volumes: BLOCKED - $1" >&2; exit 2; }

command -v jq >/dev/null 2>&1 || block "jq is not installed, so this command can't be checked"
input="$(cat)"
[ -n "${input//[[:space:]]/}" ] || block "empty hook input"
cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)" ||
  block "hook input is not the JSON this guard expects"

# Normalise: join backslash-newline continuations, drop quotes and backslashes, flatten whitespace.
flat="$(printf '%s' "$cmd" | sed -e ':a' -e 'N' -e '$!ba' -e 's/\\\n//g' | tr -d "'\"\\\\" | tr '\n\t' '  ')"

# 1. volume rm / remove / prune. `.*` between the words covers `docker -H x volume rm`.
grep -Eq '(^|[^[:alnum:]_-])(docker|podman)( .*)? volume +(rm|remove|prune)( |$)' <<<"$flat" &&
  block "docker volume rm/prune deletes named volumes (use 'make sandbox-destroy' for the sandbox; real data is on bind mounts and is never pruned from a session)"

# 2. system prune with --volumes (any position, with or without =true), and system reset.
grep -Eq '(^|[^[:alnum:]_-])(docker|podman)( .*)? system +prune( .*)? --volumes' <<<"$flat" &&
  block "docker system prune --volumes deletes every unused volume"
grep -Eq '(^|[^[:alnum:]_-])(docker|podman)( .*)? system +reset( |$)' <<<"$flat" &&
  block "system reset wipes the container engine's storage, volumes included"

# What names the instance. The pointer file covers a session without the variable exported.
esc() { sed 's/[][\.*^$+?(){}|/]/\\&/g' <<<"${1%/}"; }
dir="${HOLDFAST_INSTANCE_DIR:-}"
if [ -z "$dir" ] && [ -f "${XDG_CONFIG_HOME:-${HOME:-/nonexistent}/.config}/holdfast/instance-dir" ]; then
  IFS= read -r dir <"${XDG_CONFIG_HOME:-${HOME:-/nonexistent}/.config}/holdfast/instance-dir" || true
fi
case "$dir" in none | "") dir="" ;; esac
paths=('HOLDFAST_INSTANCE_DIR' 'instance\.env')
[ -n "$dir" ] && paths+=("$(esc "$dir")")

# 3. a destructive verb plus a reference to the instance.
verbs='(^|[^[:alnum:]_./-])(rm|rmdir|unlink|shred|truncate|mv)( |$)|find .* -delete'
if grep -Eq "$verbs" <<<"$flat"; then
  for ref in "${paths[@]}" 'holdfast-instance'; do
    grep -Eq -- "$ref" <<<"$flat" &&
      block "a delete or move on the holdfast-instance directory (real data, ADR 0019); the owner runs this from their own terminal"
  done
fi

# 4. a write to the instance directory or instance.env.
writers='(^|[^[:alnum:]_./-])(cp|dd|tee|install)( |$)|(^|[^[:alnum:]_./-])sed( .*)? (-[a-zA-Z]*i|--in-place)'
for ref in "${paths[@]}"; do
  grep -Eq -- ">[|]?[[:space:]]*[^[:space:]]*$ref" <<<"$flat" &&
    block "a redirect into the holdfast-instance directory or instance.env (real data, ADR 0019)"
  if grep -Eq "$writers" <<<"$flat"; then
    grep -Eq -- "$ref" <<<"$flat" &&
      block "a write to the holdfast-instance directory or instance.env (real data, ADR 0019); the owner does this from their own terminal"
  fi
done
exit 0
