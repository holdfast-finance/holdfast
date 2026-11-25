#!/usr/bin/env bash
# instance.sh <check|init|env-file|sandbox-env> : helpers for holdfast-instance and
# holdfast-sandbox (H2). Called by the Makefile and by scripts/dev.ps1 so both share one check.
#
#   HOLDFAST_INSTANCE_DIR  required for the instance verbs; no default, because the path is the
#                          owner's choice and no personal path belongs in code (rule 13).
#
#   check        refuse unless the directory exists, is absolute, is not a symlink, is owned by
#                this user, is mode 0700 and sits outside the repository (ADR 0019).
#   init         create the directory (0700), its subdirectories and instance.env (fresh keys)
#                on first use, then run check. Never overwrites an existing instance.env.
#               It also writes the pointer files under ${XDG_CONFIG_HOME:-$HOME/.config}/holdfast.
#   env-file     print the path of instance.env (after check).
#   sandbox-env  create .env.sandbox with fresh synthetic keys if it is absent.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
# In a linked worktree `root` is only that worktree; the main checkout is another tree to refuse.
main_root=$(cd "$root" && cd "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/.." 2>/dev/null && pwd -P) || main_root=$root
die() { echo "instance: $*" >&2; exit 2; }

dir_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
dir_owner() { stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1"; }

require_dir_var() {
  [ -n "${HOLDFAST_INSTANCE_DIR:-}" ] ||
    die "set HOLDFAST_INSTANCE_DIR to a directory outside the repo (for example on an encrypted disk)"
  case "$HOLDFAST_INSTANCE_DIR" in /*) ;; *) die "HOLDFAST_INSTANCE_DIR must be an absolute path" ;; esac
  # A colon corrupts the `-v dir:/instance` mount spec below, a newline the pointer file.
  case "$HOLDFAST_INSTANCE_DIR" in
    *:* | *$'\n'*) die "HOLDFAST_INSTANCE_DIR must not contain ':' or a newline" ;;
  esac
}

check() {
  require_dir_var
  local d=$HOLDFAST_INSTANCE_DIR
  [ ! -L "$d" ] || die "$d is a symlink; point HOLDFAST_INSTANCE_DIR at the real directory"
  [ -d "$d" ] || die "$d does not exist; run 'make instance-init' first"
  local real
  real=$(cd "$d" && pwd -P)
  case "$real/" in
    "$root"/* | "$main_root"/*) die "$real is inside the repository; real data stays outside it (ADR 0019)" ;;
  esac
  [ "$(dir_owner "$d")" = "$(id -u)" ] || die "$d is not owned by you"
  [ "$(dir_mode "$d")" = "700" ] || die "$d is mode $(dir_mode "$d"), not 700; run: chmod 700 '$d'"
  local sub
  for sub in postgres redis documents caddy_data caddy_config; do
    [ ! -L "$d/$sub" ] || die "$d/$sub is a symlink; it must be a real directory inside the instance directory"
    [ -d "$d/$sub" ] || die "$d/$sub is missing or not a directory; run 'make instance-init'"
  done
  [ -f "$d/instance.env" ] || die "$d/instance.env is missing; run 'make instance-init'"
  [ "$(dir_mode "$d/instance.env")" = "600" ] || die "$d/instance.env must be mode 600"
}

# Where agents look the directory up when HOLDFAST_INSTANCE_DIR is not exported: scripts/fc-delegate.sh
# (hides it from Codex and Grok workers, and refuses to start without it) and
# .claude/hooks/guard-volumes.sh. One path per file, mode 0600, in a 0700 directory.
write_pointers() {
  local cfg=${XDG_CONFIG_HOME:-$HOME/.config}/holdfast
  (umask 077 && mkdir -p "$cfg")
  chmod 700 "$cfg"
  (umask 077 && printf '%s\n' "$1" >"$cfg/instance-dir")
  chmod 600 "$cfg/instance-dir"
  if [ -n "${HOLDFAST_INSTANCE_DOCKER_SOCK:-}" ]; then
    (umask 077 && printf '%s\n' "$HOLDFAST_INSTANCE_DOCKER_SOCK" >"$cfg/instance-docker-sock")
    chmod 600 "$cfg/instance-docker-sock"
  fi
}

init() {
  require_dir_var
  local d=$HOLDFAST_INSTANCE_DIR port=${HOLDFAST_INSTANCE_PORT:-9443}
  local image=${BACKEND_IMAGE:-holdfast-backend:local}
  [ ! -L "$d" ] || die "$d is a symlink"
  # umask first: the directory is never visible to other users, even briefly.
  (umask 077 && mkdir -p "$d")
  if [ -n "$(ls -A "$d" 2>/dev/null)" ] && [ "$(dir_mode "$d")" != "700" ]; then
    die "$d already has content and is mode $(dir_mode "$d"); fix it with chmod 700 and re-run"
  fi
  chmod 700 "$d"
  for sub in postgres redis documents caddy_data caddy_config; do (umask 077 && mkdir -p "$d/$sub"); done
  if [ ! -f "$d/instance.env" ]; then
    python3 "$root/scripts/gen-keys.py" --write --output "$d/instance.env"
    # Loopback HTTPS on the instance's own port; passkeys bind to this origin. Production keys
    # go in by hand later (PHASE-2 contract section 12); PLAID_ENV stays sandbox until then.
    sed -i.bak -e "s|^APP_BASE_URL=.*|APP_BASE_URL=https://localhost:$port|" \
      -e "s|^WEBAUTHN_ORIGIN=.*|WEBAUTHN_ORIGIN=https://localhost:$port|" "$d/instance.env"
    rm -f "$d/instance.env.bak"
    chmod 600 "$d/instance.env"
  fi
  # Ownership by container capability set (docker-compose.yml drops them): the backend uid
  # (10001) writes documents; caddy has no capabilities at all and redis lacks DAC_OVERRIDE, so
  # root must own their directories (redis 0755 so its root entrypoint can list it and hand
  # files to the redis user). Postgres keeps DAC_OVERRIDE and chowns its own directory. The
  # 0700 parent still keeps every other user out. Plain `docker run` as root with the image's
  # own chown/chmod, as `make docs-dir` does.
  docker run --rm --user 0 --entrypoint chown -v "$d:/instance" "$image" \
    -R 10001:10001 /instance/documents
  docker run --rm --user 0 --entrypoint chown -v "$d:/instance" "$image" \
    -R 0:0 /instance/caddy_data /instance/caddy_config /instance/redis
  docker run --rm --user 0 --entrypoint chmod -v "$d:/instance" "$image" \
    755 /instance/redis
  check
  write_pointers "$d"
  echo "instance directory ready: $d (mode 700)"
}

sandbox_env() {
  cd "$root"
  if [ -f .env.sandbox ]; then return 0; fi
  python3 scripts/gen-keys.py --write --output .env.sandbox
  local port=${HOLDFAST_SANDBOX_PORT:-9444}
  sed -i.bak -e "s|^APP_BASE_URL=.*|APP_BASE_URL=https://localhost:$port|" \
    -e "s|^WEBAUTHN_ORIGIN=.*|WEBAUTHN_ORIGIN=https://localhost:$port|" \
    -e "s|^PLAID_ENV=.*|PLAID_ENV=sandbox|" .env.sandbox
  rm -f .env.sandbox.bak
  chmod 600 .env.sandbox
}

case "${1:-}" in
  check) check ;;
  init) init ;;
  env-file) check; echo "$HOLDFAST_INSTANCE_DIR/instance.env" ;;
  sandbox-env) sandbox_env ;;
  *) die "usage: instance.sh <check|init|env-file|sandbox-env>" ;;
esac
