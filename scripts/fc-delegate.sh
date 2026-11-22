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
# hides the whole per-user runtime directory, the container, Tailscale and system bus sockets and
# the lead's scratch directory, and drops the display and bus variables from the worker's
# environment.
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
# argparse accepts unambiguous prefixes (--hid, --conf), so refuse any prefix of either flag too.
for arg in "$@"; do
  name=${arg%%=*}
  if [ "${#name}" -ge 3 ] &&
    { [[ "--confine" == "$name"* ]] || [[ "--hide" == "$name"* ]]; }; then
    die "do not pass $arg; this script sets --confine and every --hide itself"
  fi
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
  # System-wide sockets a worker could abuse: the container engine (root-equivalent for the
  # docker group), the Tailscale daemon (world-writable socket, so a worker could run
  # `tailscale serve` or `funnel` and publish the stack), and the system bus (polkit actions).
  /run/docker.sock
  /var/run/docker.sock
  /run/podman
  /run/tailscale
  /var/run/tailscale
  /run/dbus
  /var/run/dbus
  "/tmp/claude-$uid"
)
# Every entry of the per-user runtime directory (session bus, systemd user manager, keyring,
# agent and credential-prompt sockets, Wayland, PipeWire, the X11 cookie, ...), except the few
# a worker needs to start at all. An allowlist, because a denylist here always missed something.
# fnm_multishells holds the Node shims the Codex CLI is launched through.
RUNTIME_KEEP=(fnm_multishells)
for entry in "$runtime"/* "$runtime"/.*; do
  name=${entry##*/}
  case "$name" in . | ..) continue ;; esac
  keep=0
  for k in "${RUNTIME_KEEP[@]}"; do [ "$name" = "$k" ] && keep=1; done
  [ "$keep" = 1 ] || candidates+=("$entry")
done
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
python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("confined") is True else 1)' "$plan" ||
  die "dry run did not report \"confined\": true; refusing to start the worker"

exec "${clean_env[@]}" firstchair "${pre[@]}" delegate "${repo[@]}" run "$@" "${flags[@]}"
