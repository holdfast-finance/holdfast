#!/usr/bin/env bash
# PreToolUse guard for Bash (H2, PHASE-2 contract section 6.1 row 6).
#
# Why: the owner's real data lives in the `holdfast-instance` compose project, on bind mounts
# under $HOLDFAST_INSTANCE_DIR (a 0700 directory outside the repo, ADR 0019). A stray prune or
# `rm -rf` run from a Claude session would destroy it, and the backup is only as fresh as the last
# `docs/ops/backup.md` run. This hook blocks (exit 2) three kinds of command:
#
#   1. `docker|podman volume rm|remove|prune`, which delete volumes by name or in bulk. The
#      sandbox project is torn down with `make sandbox-destroy`, which is allowed.
#   2. `docker|podman system prune ... --volumes`, the bulk form of the same.
#   3. A destructive file verb (rm, rmdir, unlink, shred, truncate, mv, find -delete) in a command
#      that also names the instance directory: `$HOLDFAST_INSTANCE_DIR`, its resolved value when
#      this shell has it set, the project name `holdfast-instance`, or its `instance.env`. The
#      bind mounts make this the one way left to lose the data from a session.
#
# It matches on the whole command string, not per segment, so `cd "$HOLDFAST_INSTANCE_DIR" && rm
# -rf *` and `docker volume ls -q | xargs docker volume rm` are caught. That fails closed: a
# command that merely mentions these phrases (a commit message, say) is blocked too. Put such text
# in a file and use `git commit -F`.
#
# Limits, stated plainly: this binds Claude sessions only, never Codex or Grok workers (those are
# confined and the instance directory and container socket are hidden from them in
# scripts/fc-delegate.sh), never the owner's own terminal, and it can't see a path built at run
# time. It is a guard rail, not a sandbox. The real protections are the 0700 bind mounts, the
# backup and `instance-up`'s mode check.
set -uo pipefail
input="$(cat)"
cmd="$(jq -r '.tool_input.command // ""' <<<"$input")"
# Newlines and tabs become spaces so a split command can't slip past the patterns below.
flat="$(tr '\n\t' '  ' <<<"$cmd")"

block() { echo "guard-volumes: BLOCKED - $1" >&2; exit 2; }

# 1. volume rm / remove / prune. `.*` between the words covers `docker -H x volume rm`.
grep -Eq '(^|[^[:alnum:]_-])(docker|podman)( .*)? volume +(rm|remove|prune)( |$)' <<<"$flat" &&
  block "docker volume rm/prune deletes named volumes (use 'make sandbox-destroy' for the sandbox; real data is on bind mounts and is never pruned from a session)"

# 2. system prune with --volumes (any position, with or without =true).
grep -Eq '(^|[^[:alnum:]_-])(docker|podman)( .*)? system +prune( .*)? --volumes' <<<"$flat" &&
  block "docker system prune --volumes deletes every unused volume"

# 3. a destructive verb plus a reference to the instance directory.
verbs='(^|[^[:alnum:]_./-])(rm|rmdir|unlink|shred|truncate|mv)( |$)|find .* -delete'
if grep -Eq "$verbs" <<<"$flat"; then
  refs=('HOLDFAST_INSTANCE_DIR' 'holdfast-instance' 'instance\.env')
  if [ -n "${HOLDFAST_INSTANCE_DIR:-}" ]; then
    # Escape regex metacharacters in the real path.
    refs+=("$(sed 's/[][\.*^$+?(){}|/]/\\&/g' <<<"${HOLDFAST_INSTANCE_DIR%/}")")
  fi
  for ref in "${refs[@]}"; do
    grep -Eq -- "$ref" <<<"$flat" &&
      block "a delete or move on the holdfast-instance directory (real data, ADR 0019); the owner runs this from their own terminal"
  done
fi
exit 0
