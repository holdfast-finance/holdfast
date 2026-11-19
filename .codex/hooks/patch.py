"""Guard and format Codex apply_patch edits in the Holdfast workspace."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE_HEADER = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


SECRET_PATTERNS = (
    ("Plaid access token", r"access-(?:sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-"),
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI-style API key", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("extended private key", r"\b[xtyzuv]prv[1-9A-HJ-NP-Za-km-z]{100,}"),
    ("PEM private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("Fernet key", r"(?is)fernet.*[A-Za-z0-9_-]{43}=|[A-Za-z0-9_-]{43}=.*fernet"),
    ("real-looking SSN", r"\b(?!(?:000|666|9[0-9]{2})-)\d{3}-\d{2}-\d{4}\b"),
)


def patch_files(command):
    path = None
    content = []
    for line in command.splitlines():
        header = FILE_HEADER.fullmatch(line)
        if header:
            if path is not None:
                yield path, "\n".join(content)
            path = header[2]
            content = []
        elif path is not None and line.startswith("+"):
            content.append(line[1:])
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


def format_file(path):
    if not path.is_file():
        return
    area = path.relative_to(ROOT).parts[0]
    commands = []
    if area == "backend" and path.suffix == ".py":
        ruff = ROOT / "backend/.venv/bin/ruff"
        if ruff.is_file():
            commands = [[ruff, "format", path], [ruff, "check", "--fix", path]]
    elif area in {"web", "site"} and path.suffix in {".ts", ".tsx", ".css", ".json", ".astro", ".md"}:
        prettier = ROOT / area / "node_modules/.bin/prettier"
        if prettier.is_file():
            commands = [[prettier, "--write", "--log-level", "silent", path]]
    for command in commands:
        try:
            result = subprocess.run(command, cwd=ROOT / area, capture_output=True, check=False)
        except OSError:
            print(f"Holdfast formatter could not run for {path.name}.", file=sys.stderr)
            return
        if result.returncode:
            print(f"Holdfast formatter reported an error for {path.name}.", file=sys.stderr)
            return


def main(action):
    event = json.load(sys.stdin)
    cwd = Path(event["cwd"])
    command = event["tool_input"]["command"]
    formatted = set()

    for name, content in patch_files(command):
        candidate = cwd / name
        absolute = candidate.resolve()
        if action == "guard":
            if private(candidate) or private(absolute):
                return block("patch targets a protected data or secret path")
            if absolute == ROOT / "CLAUDE.md" or absolute.is_relative_to(ROOT / ".claude"):
                return block("patch targets the Claude Code configuration")
            for label, pattern in SECRET_PATTERNS:
                if re.search(pattern, content):
                    return block(f"possible {label} in patch additions")
        elif absolute.is_relative_to(ROOT) and absolute not in formatted:
            formatted.add(absolute)
            format_file(absolute)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
