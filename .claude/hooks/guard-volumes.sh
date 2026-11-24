#!/usr/bin/env bash
# PreToolUse guard for Bash, Write, Edit, MultiEdit and NotebookEdit (H2, PHASE-2 contract
# section 6.1 row 6).
#
# Why: the owner's real data lives in the `holdfast-instance` compose project, on bind mounts
# under the instance directory (a 0700 directory outside the repo, ADR 0019). A stray prune,
# `rm -rf` or overwritten `instance.env` run from a Claude session would destroy it, and the
# backup is only as fresh as the last `docs/ops/backup.md` run.
#
# THIS IS A SEATBELT AGAINST CLAUDE'S ACCIDENTS, NOT A BOUNDARY AGAINST AN ADVERSARY. It stops the
# mistakes and the plain spellings a model actually produces. The real controls are the 0700
# directory and bind mounts, `instance-up`'s mode check, the backup, and the confinement of
# Codex and Grok workers (scripts/fc-delegate.sh hides the instance directory, the pointer files
# and the container sockets from them, and refuses to start without that).
#
# Bash commands, blocked (exit 2):
#   1. `docker|podman|docker-compose volume rm|remove|prune`, which delete volumes by name or in
#      bulk. The sandbox is torn down with `make sandbox-destroy`, which is allowed.
#   2. `system prune ... --volumes`, `system reset`, `docker rm -v|--volumes` (removes a
#      container's anonymous volumes), and `down -v|--volumes` with `-p holdfast` (the dev stack)
#      or `holdfast-instance`.
#   3. A destructive verb (rm, rmdir, unlink, shred, truncate, mv, `find -delete`,
#      `rsync --delete`, `git clean`) in a command that also names the instance. A verb counts
#      by its basename, so `/bin/rm` is `rm`.
#   4. A write: a redirect (`>`, `>>`, `>|`) whose target names the instance, or cp, dd, tee,
#      install or `sed -i` in a command that names it. This over-blocks a backup such as
#      `cp "$DIR/instance.env" /safe/`; take backups from your own terminal.
#
# "Names the instance" means any of: the literal `HOLDFAST_INSTANCE_DIR`; the directory's value
# from this shell or from the pointer file `${XDG_CONFIG_HOME:-$HOME/.config}/holdfast/instance-dir`
# that `make instance-init` writes (so a session that never exported the variable is covered);
# `instance.env`; the pointer files themselves (`holdfast/instance-dir`,
# `holdfast/instance-docker-sock`); and, for deletes only, the project name `holdfast-instance`.
#
# File tools (Write, Edit, MultiEdit, NotebookEdit): blocked when the target, resolved with `..`
# and `.` removed, is the instance directory or anything under it, a file named `instance.env`,
# or one of the pointer files.
#
# How it matches: on the whole command string after normalising it. Backslash-newline is removed
# and every quote and backslash is deleted, so `d'ocker' "volume" rm` and `r\m` read as the plain
# command the shell would run. That fails closed: a command that merely mentions these phrases (a
# commit message, say) is blocked too. Put such text in a file and use `git commit -F`.
# It also fails closed when `jq` is missing, when the input is not JSON, and when
# `.tool_input.command` is absent or not a string. It routes on `.tool_name` (Bash to the command
# check, the four file tools to the path check, anything else or no name is blocked), never on
# which fields the input happens to carry.
#
# Out of scope, stated plainly: `eval` and `bash -c` built from pieces, base64 or `printf`
# assembled commands, `$IFS` or `$'\x72m'` ANSI-C spellings, interpreters (`python -c
# 'shutil.rmtree(...)'`, node, perl, ruby), a script file written earlier, `cd` into the
# directory then a relative `rm`, a path built at run time, a symlink pointing at it, deleting
# data through the services (`docker exec db dropdb`, `DROP TABLE`), `chmod`/`chown` that lock the
# owner out, and anything outside a Claude session (the owner's terminal, Codex and Grok workers).
set -uo pipefail

block() { echo "guard-volumes: BLOCKED - $1" >&2; exit 2; }

command -v jq >/dev/null 2>&1 || block "jq is not installed, so this command can't be checked"
input="$(cat)"
[ -n "${input//[[:space:]]/}" ] || block "empty hook input"

cfgdir="${XDG_CONFIG_HOME:-${HOME:-/nonexistent}/.config}/holdfast"
dir="${HOLDFAST_INSTANCE_DIR:-}"
if [ -z "$dir" ] && [ -f "$cfgdir/instance-dir" ]; then
  IFS= read -r dir <"$cfgdir/instance-dir" || true
fi
case "$dir" in none | "") dir="" ;; esac

# ---- Route on the tool's name, never on the shape of its input. ----
# A Bash call that also carries a file_path must still get the command check, so the name decides.
tool="$(jq -r '.tool_name | strings' <<<"$input" 2>/dev/null)" ||
  block "hook input is not the JSON this guard expects"
case "$tool" in
  Bash) ;;
  Write | Edit | MultiEdit | NotebookEdit)
    file="$(jq -r '(.tool_input.file_path // .tool_input.notebook_path // empty) | strings' <<<"$input" 2>/dev/null)" ||
      block "hook input is not the JSON this guard expects"
    [ -n "$file" ] || block "a file tool call without a path can't be checked"
    resolved="$(realpath -m -- "$file" 2>/dev/null)" || resolved="$file"
    case "$resolved" in
      */instance.env | */holdfast/instance-dir | */holdfast/instance-docker-sock)
        block "a file tool write to instance.env or an instance pointer file (real data, ADR 0019)" ;;
    esac
    if [ -n "$dir" ]; then
      rdir="$(realpath -m -- "$dir" 2>/dev/null)" || rdir="$dir"
      rdir="${rdir%/}"
      case "$resolved/" in
        "$rdir"/*) block "a file tool write under the holdfast-instance directory (real data, ADR 0019); the owner does this from their own terminal" ;;
      esac
    fi
    exit 0
    ;;
  *) block "unexpected tool '${tool:-<none>}': this guard is wired to Bash and the file tools only" ;;
esac

# ---- Bash. ----
cmd="$(jq -er 'if (.tool_input.command | type) == "string" then .tool_input.command else error("no command string") end' <<<"$input" 2>/dev/null)" ||
  block "hook input has no command string, so it can't be checked"

# Normalise: join backslash-newline continuations, drop quotes and backslashes, flatten whitespace.
flat="$(printf '%s' "$cmd" | sed -e ':a' -e 'N' -e '$!ba' -e 's/\\\n//g' | tr -d "'\"\\\\" | tr '\n\t' '  ')"

engines='(docker|podman|docker-compose|podman-compose)'
lead='(^|[^[:alnum:]_-])'      # a command word may follow a path: /usr/bin/docker
verb_lead='(^|[^[:alnum:]_.-])' # same for rm, mv, cp, ...: /bin/rm

# 1. volume rm / remove / prune. `.*` between the words covers `docker -H x volume rm`.
grep -Eq "$lead$engines( .*)? volume +(rm|remove|prune)( |\$)" <<<"$flat" &&
  block "docker volume rm/prune deletes named volumes (use 'make sandbox-destroy' for the sandbox; real data is on bind mounts and is never pruned from a session)"

# 2. system prune --volumes, system reset, rm -v, and down -v on the instance project.
grep -Eq "$lead$engines( .*)? system +prune( .*)? --volumes" <<<"$flat" &&
  block "docker system prune --volumes deletes every unused volume"
grep -Eq "$lead$engines( .*)? system +reset( |\$)" <<<"$flat" &&
  block "system reset wipes the container engine's storage, volumes included"
grep -Eq "$lead$engines( .*)? rm( .*)? (-[a-zA-Z]*v[a-zA-Z]*|--volumes)( |\$)" <<<"$flat" &&
  block "docker rm -v removes a container's anonymous volumes"
if grep -Eq "(^| )down( .*)? (-[a-zA-Z]*v[a-zA-Z]*|--volumes)( |\$)" <<<"$flat"; then
  # The live dev stack is the compose project `holdfast`; the real-data stack is `holdfast-instance`.
  # Only `holdfast-sandbox` may be brought down with -v (make sandbox-destroy).
  grep -Eq 'holdfast-instance' <<<"$flat" &&
    block "down -v on the holdfast-instance project (the real-data stack is stopped with 'make instance-down')"
  grep -Eq "((^| )(-p|--project-name)[ =]+|COMPOSE_PROJECT_NAME=)holdfast( |\$)" <<<"$flat" &&
    block "down -v on the holdfast project (the dev stack's named volumes; only 'make sandbox-destroy' deletes volumes)"
fi

# What names the instance. Regexes (ERE), so metacharacters in the real path are escaped.
esc() { sed 's/[][\.*^$+?(){}|/]/\\&/g' <<<"${1%/}"; }
paths=('HOLDFAST_INSTANCE_DIR' 'instance\.env' 'holdfast/instance-(dir|docker-sock)')
[ -n "$dir" ] && paths+=("$(esc "$dir")")

# 3. a destructive verb plus a reference to the instance.
verbs="${verb_lead}(rm|rmdir|unlink|shred|truncate|mv)( |\$)|find .* -delete|${verb_lead}rsync( .*)? --delete|${verb_lead}git( .*)? clean( |\$)"
if grep -Eq "$verbs" <<<"$flat"; then
  for ref in "${paths[@]}" 'holdfast-instance'; do
    grep -Eq -- "$ref" <<<"$flat" &&
      block "a delete or move on the holdfast-instance directory (real data, ADR 0019); the owner runs this from their own terminal"
  done
fi

# 4. a write to the instance directory, instance.env or a pointer file.
writers="${verb_lead}(cp|dd|tee|install)( |\$)|${verb_lead}sed( .*)? (-[a-zA-Z]*i|--in-place)"
for ref in "${paths[@]}"; do
  grep -Eq -- ">[|]?[[:space:]]*[^[:space:]]*$ref" <<<"$flat" &&
    block "a redirect into the holdfast-instance directory, instance.env or a pointer file (real data, ADR 0019)"
  if grep -Eq "$writers" <<<"$flat"; then
    grep -Eq -- "$ref" <<<"$flat" &&
      block "a write to the holdfast-instance directory, instance.env or a pointer file (real data, ADR 0019); the owner does this from their own terminal"
  fi
done
exit 0
