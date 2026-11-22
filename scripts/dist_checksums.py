#!/usr/bin/env python3
"""Write ``SHA256SUMS`` (sha256sum format) for the wheel and sdist in a dist directory.

Same output on Linux and Windows, so ``make dist`` and ``dev.ps1 dist`` agree. Files are
sorted by name and the digest is computed over the bytes, so the file is deterministic.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SUFFIXES = (".whl", ".tar.gz")


def checksums(dist: Path) -> str:
    files = sorted(p for p in dist.iterdir() if p.is_file() and p.name.endswith(SUFFIXES))
    if not any(p.name.endswith(".whl") for p in files) or not any(
        p.name.endswith(".tar.gz") for p in files
    ):
        raise SystemExit(f"dist: need one wheel and one sdist in {dist}")
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: dist_checksums.py <dist-dir>", file=sys.stderr)
        return 2
    dist = Path(argv[1])
    (dist / "SHA256SUMS").write_text(checksums(dist), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
