#!/usr/bin/env bash
# Print the firstchair flags every Codex or Grok worker must run with on a machine that holds
# real data (ADR 0019, docs/process/FIRSTCHAIR.md rule 1). Use:
#   firstchair delegate run codex ... $(scripts/fc-hides.sh)
#
# --confine is a filesystem view under the lead's own user, not a user boundary. It hides the
# project's checkouts and common secret stores, but not sockets. Without the extra hides a
# worker with a shell could reach the running stack through the container socket, or the
# desktop keyring (and with it a stored Git host token) through the session bus.
set -euo pipefail

uid=$(id -u)
private_dir=${HOLDFAST_PRIVATE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/holdfast-private}

flags=(--confine --hide "$private_dir")
for path in \
  /run/docker.sock \
  /var/run/docker.sock \
  "/run/user/$uid/docker.sock" \
  "/run/user/$uid/podman" \
  "/run/user/$uid/libpod" \
  "/run/user/$uid/bus" \
  "/run/user/$uid/keyring" \
  "/run/user/$uid/gnupg" \
  "/run/user/$uid/gcr" \
  "/tmp/claude-$uid"; do
  # firstchair refuses a path that does not exist, so name only what is there.
  if [ -e "$path" ]; then
    flags+=(--hide "$path")
  fi
done

printf '%s ' "${flags[@]}"
printf '\n'
