#!/usr/bin/env python3
"""Generate Holdfast secrets.

    python3 scripts/gen-keys.py            # print FERNET_KEY / SESSION_SECRET / POSTGRES_PASSWORD
    python3 scripts/gen-keys.py --write    # create .env from .env.example with secrets filled in
    python3 scripts/gen-keys.py --write --force   # replace an existing .env

Stdlib only (uses `cryptography` for the Fernet key when it is installed; the fallback produces
the identical format: urlsafe base64 of 32 random bytes). With --write nothing secret is printed.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "CHANGE_ME"


def fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return base64.urlsafe_b64encode(os.urandom(32)).decode()
    return Fernet.generate_key().decode()


def generate() -> dict[str, str]:
    return {
        "FERNET_KEY": fernet_key(),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        # urlsafe alphabet needs no escaping inside DATABASE_URL.
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    }


LINE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")


def render(template: str, values: dict[str, str]) -> str:
    """Fill empty secret keys; sync DATABASE_URL's placeholder password. Keep everything else."""
    out: list[str] = []
    for line in template.splitlines():
        m = LINE.match(line)
        if m:
            key, value = m["key"], m["value"]
            if key in values and value.strip() == "":
                line = f"{key}={values[key]}"
            elif key == "DATABASE_URL" and f":{PLACEHOLDER}@" in value:
                synced = value.replace(f":{PLACEHOLDER}@", f":{values['POSTGRES_PASSWORD']}@")
                line = f"{key}={synced}"
        out.append(line)
    return "\n".join(out) + "\n"


def write_env(example: Path, target: Path, force: bool) -> int:
    if not example.is_file():
        print(f"error: {example} not found", file=sys.stderr)
        return 1
    if target.exists() and not force:
        print(
            f"error: {target} already exists; refusing to overwrite (use --force)", file=sys.stderr
        )
        return 1
    content = render(example.read_text(encoding="utf-8"), generate())
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=target.parent)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.chmod(target, 0o600)
    print(f"wrote {target.name}")
    if force:
        print(
            "note: new POSTGRES_PASSWORD/FERNET_KEY. An existing pgdata volume keeps the old db "
            "password, and data encrypted with the old FERNET_KEY can't be decrypted.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--write", action="store_true", help="create .env from .env.example")
    p.add_argument("--force", action="store_true", help="with --write: overwrite an existing .env")
    p.add_argument("--example", type=Path, default=ROOT / ".env.example", help=argparse.SUPPRESS)
    p.add_argument("--output", type=Path, default=ROOT / ".env", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.write:
        return write_env(args.example, args.output, args.force)
    if args.force:
        p.error("--force only makes sense with --write")
    for key, value in generate().items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
