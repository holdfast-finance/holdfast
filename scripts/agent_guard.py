#!/usr/bin/env python3
"""Shared pre-edit guard for external agents (Codex/Anvil, Grok/Helm) and the Claude secret hook.

Run as `python3 -I scripts/agent_guard.py [--seed-only] < event.json`. Stdlib only.
Exit 0 allows the edit. Exit 2 blocks it. Any failure also exits 2 (fail closed).
The payload is never echoed: messages carry a reason label only.

Checks, in order:
  1. Event shape: JSON object, `tool_input` an object, `toolInputTruncated` absent or false.
  2. Paths (Codex `*** Add/Update/Delete File` and `*** Move to` headers, or Grok/Claude path keys):
     resolved with realpath against the repo root, so `..` and symlinks cannot leave it. Denies
     private data (.env*, data/, secrets/, exports/) and the agent/CI config the agents must not edit.
  3. Content: every string leaf of tool_input is scanned for secrets, SSNs and BIP39 seed phrases.

`--seed-only` runs just the seed-phrase scan over every string leaf (used by .claude/hooks/guard-secrets.sh).

BIP39 wordlist: scripts/bip39_english.txt, the official English list (2048 words).
  source: https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt
  sha256: 2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda
The hash is verified at load. A mismatch blocks everything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_REAL = Path(os.path.realpath(ROOT))
WORDLIST = Path(__file__).resolve().with_name("bip39_english.txt")
WORDLIST_SHA256 = "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"

FILE_HEADER = re.compile(r"^\s*\*\*\* (Add File|Update File|Delete File|Move to):\s*(.+?)\s*$")
PATH_KEYS = {
    "file_path", "filepath", "path", "notebook_path", "target_notebook", "target_file", "file", "filename",
}
PRIVATE_DIRS = {"data", "secrets", "exports"}
PROTECTED_TOP = {".claude", ".codex", ".agents", ".grok", ".githooks"}
PROTECTED_FILES = {"claude.md", "agents.md", ".gitleaks.toml", ".gitleaksignore"}
PROTECTED_SCRIPTS = {"agent_guard.py", "bip39_english.txt", "external-agent-launch.sh"}

SECRET_PATTERNS = (
    ("Plaid access token", r"access-(?:sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-"),
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI-style API key", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("extended private key", r"\b[xtyzuv]prv[1-9A-HJ-NP-Za-km-z]{100,}"),
    ("PEM private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("Fernet key", r"(?is)fernet.*[A-Za-z0-9_-]{43}=|[A-Za-z0-9_-]{43}=.*fernet"),
    ("real-looking SSN", r"\b(?!(?:000|666|9[0-9]{2})-)\d{3}-\d{2}-\d{4}\b"),
)
SEED_MIN_RUN = 12  # a run of 12+ wordlist words contains a 12/15/18/21/24-word mnemonic


class Block(Exception):
    """Raised with a reason label (never payload text)."""


def leaves(node):
    """Yield every string in a JSON value, including keys' values at any depth."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from leaves(value)


def path_values(node):
    """Yield string values stored under a path-like key at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in PATH_KEYS and isinstance(value, str):
                yield value
            else:
                yield from path_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from path_values(value)


def load_words() -> frozenset[str]:
    raw = WORDLIST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != WORDLIST_SHA256:
        raise Block("BIP39 wordlist failed its integrity check")
    words = raw.decode("ascii").split()
    if len(words) != 2048:
        raise Block("BIP39 wordlist has the wrong length")
    return frozenset(words)


def has_seed_phrase(texts, words) -> bool:
    """True when 12+ consecutive tokens are all BIP39 words. Tokens split on anything that is not a letter."""
    for text in texts:
        run = 0
        for token in re.findall(r"[a-z]+", text.lower()):
            run = run + 1 if token in words else 0
            if run >= SEED_MIN_RUN:
                return True
    return False


def check_seed(strings) -> None:
    words = load_words()
    if has_seed_phrase(strings, words) or has_seed_phrase(["\n".join(strings)], words):
        raise Block("possible seed phrase (12+ consecutive BIP39 words); never store seed phrases or private keys")


def check_secrets(strings) -> None:
    text = "\n".join(strings)
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            raise Block(f"possible {label}")


def resolve(raw: str, base: Path) -> tuple[Path, Path]:
    """Return (lexical, real) absolute paths. The real path follows every symlink that exists."""
    if "\x00" in raw:
        raise Block("path contains a NUL byte")
    candidate = Path(raw) if os.path.isabs(raw) else base / raw
    lexical = Path(os.path.normpath(candidate))
    return lexical, Path(os.path.realpath(candidate))


def private_name(name: str) -> bool:
    low = name.casefold()
    return low == ".env" or low.endswith(".env") or (low.startswith(".env.") and low != ".env.example")


def check_path(raw: str, base: Path) -> None:
    lexical, real = resolve(raw, base)
    if not real.is_relative_to(ROOT_REAL):
        raise Block("path resolves outside the repository")
    for form in {real.relative_to(ROOT_REAL)} | (
        {lexical.relative_to(ROOT_REAL)} if lexical.is_relative_to(ROOT_REAL) else set()
    ):
        parts = [p.casefold() for p in form.parts]
        if not parts:
            raise Block("path is the repository root")
        if any(p in PRIVATE_DIRS for p in parts) or any(private_name(p) for p in form.parts):
            raise Block("path is private data or an environment file")
        if parts[0] in PROTECTED_TOP or (len(parts) == 1 and parts[0] in PROTECTED_FILES):
            raise Block("path is agent or CI configuration (Keel and Bulwark only)")
        if parts[0] == "scripts" and len(parts) == 2 and parts[1] in PROTECTED_SCRIPTS:
            raise Block("path is part of the agent guard")


def patch_headers(strings) -> list[str]:
    found = []
    for text in strings:
        for line in text.splitlines():
            match = FILE_HEADER.match(line)
            if match:
                found.append(match[2])
    return found


def run(raw_stdin: bytes, seed_only: bool) -> None:
    if not raw_stdin.strip():
        raise Block("empty input")
    event = json.loads(raw_stdin.decode("utf-8"))
    if not isinstance(event, dict):
        raise Block("event is not a JSON object")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        raise Block("tool_input is missing or not an object")
    for key in ("toolInputTruncated", "tool_input_truncated"):
        if key in event and event[key] is not False:
            raise Block("tool input was truncated or its flag is unreadable, so the scan would be incomplete")
    strings = list(leaves(tool_input))
    if seed_only:
        check_seed(strings)
        return

    cwd = event.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else ROOT_REAL
    patch_mode = event.get("tool_name") == "apply_patch" or any("*** Begin Patch" in s for s in strings)
    paths = patch_headers(strings) if patch_mode else list(path_values(tool_input))
    if not paths:
        raise Block("no resolvable target path in the edit")
    for raw in paths:
        check_path(raw, base)
    check_secrets(strings)
    check_seed(strings)


def main(argv) -> int:
    try:
        run(sys.stdin.buffer.read(), "--seed-only" in argv)
    except Block as reason:
        print(f"agent-guard: BLOCKED - {reason}", file=sys.stderr)
        return 2
    except BaseException:  # noqa: BLE001 - fail closed on anything, including a bad payload
        print("agent-guard: BLOCKED - guard could not evaluate the input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
