#!/usr/bin/env bash
# The only supported way to start Codex (Anvil) or Grok (Helm). ADR 0012, docs/process/EXTERNAL-AGENTS-SETUP.md.
# Fails closed. Refuses unless:
#   1. the OS user is `holdfast-agents` (so the owner's .env, SSH keys and gh login are out of reach), and
#   2. the signed-in GitHub identity cannot push to holdfast-finance/holdfast (Read role only).
# Usage: scripts/external-agent-launch.sh codex exec "You are Anvil. ..."
#        scripts/external-agent-launch.sh grok --permission-mode acceptEdits "You are Helm. ..."
set -u

die() { echo "external-agent-launch: REFUSED - $1" >&2; exit 1; }

[ "$#" -ge 1 ] || die "usage: external-agent-launch.sh <codex|grok> [args...]"
tool="$1"; shift
case "$tool" in codex|grok) ;; *) die "unknown agent CLI (codex or grok only)";; esac

user="$(id -un 2>/dev/null)" || die "cannot determine the OS user"
[ "$user" = "holdfast-agents" ] || die "must run as the holdfast-agents OS user (got a different user); see docs/process/EXTERNAL-AGENTS-SETUP.md"

push="$(gh api repos/holdfast-finance/holdfast --jq .permissions.push 2>/dev/null)" || die "cannot read GitHub permissions (is gh signed in as the machine user?)"
[ "$push" = "false" ] || die "the signed-in GitHub identity can push to holdfast-finance/holdfast; sign in as the Read-role machine user"

# Sandbox overrides disable the holdfast permission profile.
for arg in "$@"; do
  case "$arg" in
    -s|--sandbox|--sandbox=*|--full-auto|--dangerously-bypass-approvals-and-sandbox|--yolo|--dangerously-skip-permissions|--permission-mode=bypassPermissions)
      die "flag $arg would bypass the holdfast permission profile";;
  esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || die "cannot find the repo root"
cd "$root" || die "cannot enter the repo root"
exec "$tool" "$@"
