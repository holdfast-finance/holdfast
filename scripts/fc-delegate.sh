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
#   HOLDFAST_INSTANCE_DIR the holdfast-instance data directory (H2). Required, like
#                         HOLDFAST_PRIVATE_DIR: unset falls back to the pointer file
#                         ${XDG_CONFIG_HOME:-$HOME/.config}/holdfast/instance-dir that
#                         `make instance-init` writes; with neither, the script refuses. "none" on
#                         a machine that hosts no instance. A path that is not a directory is refused. When both the
#                         variable and the pointer are present they must name the same
#                         directory. "none" is accepted only from the variable, and not at all
#                         when a pointer names a directory. The pointer directory is hidden from
#                         the worker, and the worker starts in the repository root.
#   HOLDFAST_INSTANCE_DOCKER_SOCK
#                         the container-engine socket that runs holdfast-instance when it is not a
#                         standard path (a dedicated rootless engine). Falls back to the pointer
#                         file .../holdfast/instance-docker-sock; "none" or neither means no extra
#                         hide. A path that does not exist is refused.
#   FIRSTCHAIR_SESSION    passed as --session.
#   FC_REPO               passed as delegate --repo. Unset: the git checkout the caller is standing
#                         in. A relative FC_REPO, --prompt-file or --output is made absolute
#                         against the caller's directory, because the script then moves to the
#                         repo root.
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

# The real-data instance (H2): its bind-mounted data directory and, when it runs on an engine of
# its own, that engine's socket. The standard sockets and the runtime directory are hidden below.
cfg=${XDG_CONFIG_HOME:-$HOME/.config}/holdfast
# The pointer directory itself is hidden, so a worker can't rewrite the pointer files that the
# next launch (and the Claude hook) trust.
[ -d "$cfg" ] && hides+=("$(realpath "$cfg")")

read_pointer() { # first line of a pointer file, or empty
  local value=""
  if [ -f "$1" ]; then IFS= read -r value <"$1" || true; fi
  printf '%s' "$value"
}
check_dir() { # a named instance directory must exist and be a directory
  [ -e "$1" ] || die "instance directory '$1' does not exist; fix HOLDFAST_INSTANCE_DIR or $cfg/instance-dir"
  [ -d "$1" ] || die "instance path '$1' is not a directory"
}

env_dir=${HOLDFAST_INSTANCE_DIR:-}
ptr_dir=$(read_pointer "$cfg/instance-dir")
# "none" is an explicit statement by the person launching the worker, so only the variable can
# say it, and only when no pointer names an instance on this machine.
[ "$ptr_dir" != "none" ] || die "$cfg/instance-dir says 'none'; 'none' is accepted only from HOLDFAST_INSTANCE_DIR"
if [ "$env_dir" = "none" ]; then
  [ -z "$ptr_dir" ] || die "HOLDFAST_INSTANCE_DIR=none, but the pointer $cfg/instance-dir names a directory; remove one of them"
elif [ -z "$env_dir" ] && [ -z "$ptr_dir" ]; then
  die "no instance directory: set HOLDFAST_INSTANCE_DIR (or run make instance-init, which writes $cfg/instance-dir), or set it to 'none' on a machine that hosts no instance"
else
  for named in "$env_dir" "$ptr_dir"; do
    [ -z "$named" ] || check_dir "$named"
  done
  if [ -n "$env_dir" ] && [ -n "$ptr_dir" ] && [ "$(realpath "$env_dir")" != "$(realpath "$ptr_dir")" ]; then
    die "HOLDFAST_INSTANCE_DIR and the pointer $cfg/instance-dir name different directories"
  fi
  hides+=("$(realpath "${env_dir:-$ptr_dir}")")
fi
sock=${HOLDFAST_INSTANCE_DOCKER_SOCK:-}
[ -n "$sock" ] || sock=$(read_pointer "$cfg/instance-docker-sock")
if [ -n "$sock" ] && [ "$sock" != "none" ]; then
  [ -e "$sock" ] || die "instance engine socket '$sock' does not exist; fix HOLDFAST_INSTANCE_DOCKER_SOCK or $cfg/instance-docker-sock, or set it to 'none'"
  hides+=("$(realpath "$sock")")
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
# The list is computed at launch: an entry created during a run (a new ssh-askpass or vscode-git
# socket) is visible to that worker until it exits.
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

# The script moves to the repo root below, so anything that depended on the caller's directory is
# fixed first. The worker's repository is FC_REPO, else the checkout the caller is standing in (a
# lead running the main checkout's wrapper from a worktree hands the worker that worktree). A
# relative FC_REPO, --prompt-file or --output is made absolute against the caller's directory. A
# lone "-" (stdin) is left alone.
caller=$PWD
absolute() {
  case "$1" in
    - | /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$caller" "$1" ;;
  esac
}
fc_repo=${FC_REPO:-}
if [ -n "$fc_repo" ]; then
  fc_repo=$(absolute "$fc_repo")
else
  fc_repo=$(git -C "$caller" rev-parse --show-toplevel 2>/dev/null) || fc_repo=""
fi
repo=()
[ -n "$fc_repo" ] && repo+=(--repo "$fc_repo")

fc_args=()
want_path=0
for arg in "$@"; do
  if [ "$want_path" = 1 ]; then
    fc_args+=("$(absolute "$arg")")
    want_path=0
    continue
  fi
  case "$arg" in
    --prompt-file | --output) fc_args+=("$arg"); want_path=1 ;;
    --prompt-file=* | --output=*) fc_args+=("${arg%%=*}=$(absolute "${arg#*=}")") ;;
    *) fc_args+=("$arg") ;;
  esac
done

clean_env=(env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY -u DBUS_SESSION_BUS_ADDRESS
  -u SSH_AUTH_SOCK -u GPG_AGENT_INFO -u DOCKER_HOST)

# Start from the repository root, never from the caller's directory: a shell left inside the
# instance directory would otherwise hand the worker a working directory in real data.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Dry run first: the same arguments must resolve to a confined worker, or nothing starts.
plan=$("${clean_env[@]}" firstchair "${pre[@]}" delegate "${repo[@]}" run "${fc_args[@]}" "${flags[@]}" --dry-run) ||
  die "firstchair dry run failed"
python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("confined") is True else 1)' "$plan" ||
  die "dry run did not report \"confined\": true; refusing to start the worker"

exec "${clean_env[@]}" firstchair "${pre[@]}" delegate "${repo[@]}" run "${fc_args[@]}" "${flags[@]}"
