#!/usr/bin/env bash
# PreToolUse guard for Write/Edit/MultiEdit/NotebookEdit.
# Blocks (exit 2) writes to secret/data paths and content that looks like a real secret.
set -uo pipefail
input="$(cat)"
path="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"$input")"
content="$(jq -r '[.tool_input.content, .tool_input.new_string, (.tool_input.edits // [] | .[].new_string), .tool_input.new_source] | map(select(. != null)) | join("\n")' <<<"$input")"

block() { echo "guard-secrets: BLOCKED — $1" >&2; exit 2; }

rel="${path#"${CLAUDE_PROJECT_DIR:-$PWD}"/}"
case "$rel" in
  .env|.env.local|.env.prod*|.env.dev|*/.env|*/.env.local) block "writing $rel (secrets live outside git; edit .env.example instead)";;
  data/*|*/data/documents/*|secrets/*|*/secrets/*) block "writing under $rel (private data dir)";;
esac

# Seed phrases (12+ consecutive BIP39 words) in any string of the tool input. Shared with the Codex/Grok guard.
guard_py="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)/scripts/agent_guard.py"
[ -f "$guard_py" ] || block "scripts/agent_guard.py is missing, so the seed-phrase scan cannot run"
python3 -I "$guard_py" --seed-only <<<"$input" >/dev/null 2>&1 || block "possible seed phrase (12+ BIP39 words); never store seed phrases or private keys"

# Real-looking secrets. Test fixtures must use obviously fake values.
grep -Eq 'access-(sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-' <<<"$content" && block "Plaid access token"
grep -Eq 'sk-ant-[A-Za-z0-9_-]{20,}' <<<"$content" && block "Anthropic API key"
grep -Eq 'sk-(proj-)?[A-Za-z0-9]{32,}' <<<"$content" && block "OpenAI-style API key"
grep -Eq '\b[xtyzuv]prv[1-9A-HJ-NP-Za-km-z]{100,}' <<<"$content" && block "extended PRIVATE key (xprv/tprv): the server never holds private keys"
grep -Eq -- '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' <<<"$content" && block "PEM private key"
grep -Eiq 'fernet' <<<"$content" && grep -Eq '[0-9A-Za-z_-]{43}=' <<<"$content" && block "what looks like a Fernet key"
# SSNs: only invalid test ranges (000-, 666-, 9xx-) are allowed, so fixtures can exercise redaction.
if grep -Eo '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b' <<<"$content" | grep -Evq '^(000|666|9[0-9]{2})-'; then
  block "real-looking SSN (use 000-/666-/9xx- test values)"
fi
exit 0
