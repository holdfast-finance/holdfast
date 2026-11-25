"""Guard Codex apply_patch edits in the Holdfast workspace."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE_HEADER = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")
MARKERS = {"*** Begin Patch", "*** End Patch", "*** End of File"}
GUARD_PATHS = (
    ".codex/hooks.json", ".codex/hooks", ".codex/rules", ".codex/config.toml",
    ".grok/hooks", ".grok/config.toml", ".githooks", "scripts/fc-delegate.sh",
)


SECRET_PATTERNS = (
    ("Plaid access token", r"access-(?:sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-"),
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI-style API key", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("extended private key", r"\b[xtyzuv]prv[1-9A-HJ-NP-Za-km-z]{100,}"),
    ("PEM private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("Fernet key", r"(?is)fernet.*[A-Za-z0-9_-]{43}=|[A-Za-z0-9_-]{43}=.*fernet"),
    ("real-looking SSN", r"\b(?!(?:000|666|9[0-9]{2})-)\d{3}-\d{2}-\d{4}\b"),
)


def unknown_markers(command):
    for raw in command.splitlines():
        line = raw.strip()
        if line.startswith("***") and line not in MARKERS and not FILE_HEADER.fullmatch(line):
            return True
    return False


def patch_files(command):
    path = None
    content = []
    for raw in command.splitlines():
        line = raw.strip()
        header = FILE_HEADER.fullmatch(line)
        if header:
            if path is not None:
                yield path, "\n".join(content)
            path = header[2].strip()
            content = []
        elif path is not None and raw.startswith("+"):
            content.append(raw[1:])
    if path is not None:
        yield path, "\n".join(content)


def private(path):
    return (
        any(part in {"data", "secrets", "exports"} for part in path.parts)
        or path.name == ".env"
        or (path.name.startswith(".env.") and path.name != ".env.example")
    )


def block(reason):
    print(f"Holdfast patch guard: {reason}", file=sys.stderr)
    return 2


def guard_path(absolute):
    return any(
        absolute == ROOT / g or absolute.is_relative_to(ROOT / g) for g in GUARD_PATHS
    )


def main():
    event = json.load(sys.stdin)
    cwd = Path(event["cwd"])
    command = event["tool_input"]["command"]
    if not isinstance(command, str) or not command.strip():
        return block("patch command is missing or empty")
    if unknown_markers(command):
        return block("patch has an unrecognised *** marker")
    seen = False
    for name, content in patch_files(command):
        seen = True
        candidate = cwd / name
        absolute = candidate.resolve()
        if private(candidate) or private(absolute):
            return block("patch targets a protected data or secret path")
        if absolute == ROOT / "CLAUDE.md" or absolute.is_relative_to(ROOT / ".claude"):
            return block("patch targets the Claude Code configuration")
        if guard_path(absolute):
            return block("patch targets the guard machinery")
        for label, pattern in SECRET_PATTERNS:
            if re.search(pattern, content):
                return block(f"possible {label} in patch additions")
    if not seen:
        return block("no file parsed from patch")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        code = block("guard failed closed on an internal error")
    sys.exit(code)
