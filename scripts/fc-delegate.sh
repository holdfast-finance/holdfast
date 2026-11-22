#!/usr/bin/env bash
# fc-delegate.sh <arguments for `firstchair delegate run`>
#
# The only way to start a Codex or Grok worker on a machine that holds real data (ADR 0019,
# docs/process/FIRSTCHAIR.md rule 1). It runs
#
#   firstchair [--session S] delegate [--repo R] run <your arguments> --confine --hide ...
#
# and fails closed: it refuses to start the worker when bubblewrap is missing, when the private
# data directory is not set up, or when a dry run of the same command does not report
# "confined": true.
#
# --confine is a filesystem view under the lead's own user and groups, not a user boundary. It
# hides the project's checkouts and common secret stores but no sockets, so this script also
# hides the container sockets, the session bus, the systemd user manager, the accessibility bus,
# the keyring and agent sockets, the X11 cookie, Git credential prompt sockets and the lead's
# scratch directory, and drops the display and bus variables from the worker's environment.
#
# Environment:
#   HOLDFAST_PRIVATE_DIR  the private data directory; must exist. "none" on a machine with no
#                         real data. Default: $XDG_DATA_HOME/holdfast-private.
#   FIRSTCHAIR_SESSION    passed as --session.
#   FC_REPO               passed as delegate --repo.
#   FC_RUNTIME_DIR        override /run/user/<uid> (tests only).
set -euo pipefail
shopt -s nullglob

die() {
  echo "fc-delegate: $*" >&2
  exit 2
}

[ "$#" -gt 0 ] || die "usage: fc-delegate.sh <seat> [delegate run options] [task]"
for arg in "$@"; do
  case "$arg" in
    --confine | --hide | --hide=*) die "do not pass $arg; this script sets every hide itself" ;;
  esac
done

command -v bwrap >/dev/null 2>&1 || die "bubblewrap (bwrap) is not installed; refusing to run unconfined"
command -v firstchair >/dev/null 2>&1 || die "firstchair is not on PATH"
: "${HOME:?HOME is not set}"

uid=$(id -u)
runtime=${FC_RUNTIME_DIR:-/run/user/$uid}

private=${HOLDFAST_PRIVATE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/holdfast-private}
hides=()
if [ "$private" != "none" ]; then
  [ -d "$private" ] || die "private data directory '$private' does not exist; set HOLDFAST_PRIVATE_DIR to it, or to 'none' on a machine with no real data"
  hides+=("$(realpath "$private")")
fi

candidates=(
  /run/docker.sock
  /var/run/docker.sock
  /run/podman
  "$runtime/docker.sock"
  "$runtime/docker"
  "$runtime/podman"
  "$runtime/libpod"
  "$runtime/bus"
  "$runtime/systemd"
  "$runtime/at-spi"
  "$runtime/keyring"
  "$runtime/gnupg"
  "$runtime/gcr"
  "$runtime/cc-socks"
  "$runtime/pipewire-0"
  "$runtime/pulse"
  "$runtime"/.mutter-Xwaylandauth.*
  "$runtime"/vscode-git-*.sock
  "$runtime"/vscode-ipc-*.sock
  "$runtime"/ssh-askpass-*.sock
  "/tmp/claude-$uid"
)
for path in "${candidates[@]}"; do
  if [ -e "$path" ] || [ -S "$path" ]; then
    hides+=("$(realpath "$path")")
  fi
done

flags=(--confine)
declare -A seen=()
for path in "${hides[@]}"; do
  [ -n "${seen[$path]:-}" ] && continue
  seen[$path]=1
  flags+=(--hide "$path")
done

pre=()
[ -n "${FIRSTCHAIR_SESSION:-}" ] && pre+=(--session "$FIRSTCHAIR_SESSION")
repo=()
[ -n "${FC_REPO:-}" ] && repo+=(--repo "$FC_REPO")

clean_env=(env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY -u DBUS_SESSION_BUS_ADDRESS
  -u SSH_AUTH_SOCK -u GPG_AGENT_INFO -u DOCKER_HOST)

# Dry run first: the same arguments must resolve to a confined worker, or nothing starts.
plan=$("${clean_env[@]}" firstchair "${pre[@]}" delegate "${repo[@]}" run "$@" "${flags[@]}" --dry-run) ||
  die "firstchair dry run failed"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("confined") is True else 1)' "$plan" ||
  die "dry run did not report \"confined\": true; refusing to start the worker"

exec "${clean_env[@]}" firstchair "${pre[@]}" delegate "${repo[@]}" run "$@" "${flags[@]}"
