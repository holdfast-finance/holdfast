"""Local measurement harness: run the statement parser over a directory, print an aggregate table.

    cd backend && uv run --extra sanitize python -m app.importers.harness <dir>

Prints per profile: files seen, parsed, reconciled, needs_review, balances_only, duplicates and
error types. It never prints statement content: no file names, no descriptions, no amounts, only
counts and exception class names / fixed reason codes. ``.txt`` files are read as pre-rendered
layout text (sanitizer output); ``.pdf`` files go through ``pdf_text``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.domain.statement_parse import (
    ParsedStatement,
    StatementParseError,
    detect_profile,
    parse_statement,
)
from app.importers.profile_loader import load_profiles

UNMATCHED = "(no profile)"


def _read(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from app.importers.pdf_text import pdf_to_text

        return pdf_to_text(path)
    return path.read_text(encoding="utf-8")


def _identity(s: ParsedStatement) -> tuple[object, ...]:
    return (
        s.profile_id,
        s.period_start,
        s.period_end,
        tuple((b.key, b.account_last4) for b in s.blocks),
    )


def _error_key(exc: Exception) -> str:
    """Class name, plus the fixed reason code for our own errors. Never other message text."""
    if isinstance(exc, StatementParseError):
        return f"err:StatementParseError:{str(exc)[:40]}"
    return f"err:{type(exc).__name__}"


def run(directory: Path) -> dict[str, Counter[str]]:
    profiles = load_profiles()
    rows: dict[str, Counter[str]] = defaultdict(Counter)
    seen_hash: set[str] = set()
    seen_identity: set[tuple[object, ...]] = set()
    files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in (".pdf", ".txt"))
    for path in files:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            text = _read(path)
        except Exception as exc:
            rows[UNMATCHED]["seen"] += 1
            rows[UNMATCHED][f"err:{type(exc).__name__}"] += 1
            continue
        profile = detect_profile(text, profiles)
        name = profile.id if profile else UNMATCHED
        c = rows[name]
        c["seen"] += 1
        if profile is None:
            continue
        if digest in seen_hash:
            c["duplicate"] += 1
            continue
        seen_hash.add(digest)
        try:
            parsed = parse_statement(text, profile)
        except Exception as exc:
            c[_error_key(exc)] += 1
            continue
        ident = _identity(parsed)
        if ident in seen_identity:
            c["duplicate"] += 1
            continue
        seen_identity.add(ident)
        c["parsed"] += 1
        c[parsed.status] += 1
        for b in parsed.blocks:
            c["blocks"] += 1
            c["txns"] += len(b.txns)
            if b.reconciliation.reason:
                c[f"reason:{b.reconciliation.reason}"] += 1
    return rows


def render(rows: dict[str, Counter[str]]) -> str:
    cols = ["seen", "parsed", "reconciled", "needs_review", "balances_only", "duplicate"]
    out = [f"{'profile':<26}" + "".join(f"{c:>14}" for c in cols)]
    total: Counter[str] = Counter()
    for name in sorted(rows):
        c = rows[name]
        total.update({k: v for k, v in c.items() if k in cols})
        out.append(f"{name:<26}" + "".join(f"{c[k]:>14}" for k in cols))
    out.append(f"{'TOTAL':<26}" + "".join(f"{total[k]:>14}" for k in cols))
    out.append("")
    for name in sorted(rows):
        extra = {k: v for k, v in rows[name].items() if k not in cols}
        if extra:
            out.append(f"{name}: " + ", ".join(f"{k}={v}" for k, v in sorted(extra.items())))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate-only statement parse report.")
    ap.add_argument("directory", type=Path)
    args = ap.parse_args(argv)
    if not args.directory.is_dir():
        print("not a directory", file=sys.stderr)
        return 2
    print(render(run(args.directory)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
