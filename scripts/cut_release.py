#!/usr/bin/env python3
"""Plan a public Holdfast version release. This does not create a tag or a GitHub Release.

`check` compares the committed product SemVer with the Python package version.
`plan --sha` prints the commands Keel runs, and only when that commit is on main.
A product release is an annotated ``v`` tag plus a GitHub Release on that same commit.
Phase tags are milestones and are refused here.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

CANONICAL_REPO = "holdfast-finance/holdfast"
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(alpha|beta|rc)(?:\.(0|[1-9]\d*))?)?$"
)
_PRE_PEP = {"alpha": "a", "beta": "b", "rc": "rc"}


class ReleaseError(Exception):
    """The commit is not a publishable product version."""


@dataclass(frozen=True)
class Release:
    product: str
    python: str
    tag: str
    prerelease: bool
    sha: str
    notes: Path

    @property
    def message(self) -> str:
        return f"Holdfast {self.tag} (product {self.product}, Python package {self.python})"

    def commands(self) -> list[list[str]]:
        latest = "false" if self.prerelease else "true"
        release = [
            "gh", "release", "create", self.tag,
            "--repo", CANONICAL_REPO,
            "--target", self.sha,
            "--title", f"Holdfast {self.tag}",
            "--notes-file", self.notes.as_posix(),
            "--verify-tag",
            "--latest", latest,
        ]
        if self.prerelease:
            release.append("--prerelease")
        return [
            ["git", "tag", "-a", self.tag, self.sha, "-m", self.message],
            ["git", "push", "origin", f"refs/tags/{self.tag}"],
            release,
        ]


def python_version(product: str) -> str:
    """Map a product SemVer string to the PEP 440 form stored in pyproject.toml."""
    match = _SEMVER.fullmatch(product)
    if match is None:
        raise ReleaseError(f"product version {product!r} is not SemVer major.minor.patch[-alpha|beta|rc[.N]]")
    core = ".".join(match.group(1, 2, 3))
    kind = match.group(4)
    if kind is None:
        return core
    number = match.group(5) or "0"
    return f"{core}{_PRE_PEP[kind]}{number}"


def tag_name(product: str) -> str:
    python_version(product)
    if product.startswith("phase-") or product.startswith("v"):
        raise ReleaseError(f"{product!r} is not a product version")
    return f"v{product}"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _show(root: Path, sha: str, path: str) -> str:
    result = _git(root, "show", f"{sha}:{path}")
    if result.returncode != 0:
        raise ReleaseError(f"{sha} does not contain {path}")
    return result.stdout


def versions_at(root: Path, sha: str) -> tuple[str, str]:
    try:
        product = json.loads(_show(root, sha, "web/package.json"))["version"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReleaseError("web/package.json has no string version") from exc
    if not isinstance(product, str):
        raise ReleaseError("web/package.json version must be a string")
    try:
        python = tomllib.loads(_show(root, sha, "backend/pyproject.toml"))["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReleaseError("backend/pyproject.toml has no project.version") from exc
    if not isinstance(python, str):
        raise ReleaseError("backend/pyproject.toml project.version must be a string")
    return product, python


def describe(root: Path, sha: str) -> Release:
    product, python = versions_at(root, sha)
    expected = python_version(product)
    if python != expected:
        raise ReleaseError(
            f"Python version {python} does not match product {product} ({expected})"
        )
    tag = tag_name(product)
    if _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0:
        raise ReleaseError(f"{tag} already exists; version tags are not moved")
    on_main = _git(root, "merge-base", "--is-ancestor", sha, "main")
    if on_main.returncode != 0:
        raise ReleaseError(f"{sha} is not on main")
    full = _git(root, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if full.returncode != 0:
        raise ReleaseError(f"{sha} is not a commit")
    resolved = full.stdout.strip()
    notes = Path("docs/releases") / f"{tag}.md"
    if not (root / notes).is_file():
        raise ReleaseError(f"missing release notes {notes.as_posix()}")
    prerelease = "-" in product
    return Release(product, python, tag, prerelease, resolved, notes)


def format_plan(release: Release) -> str:
    lines = [
        f"tag: {release.tag}",
        f"commit: {release.sha}",
        f"product: {release.product}",
        f"python: {release.python}",
        f"prerelease: {str(release.prerelease).lower()}",
        "annotated: true",
        "lightweight: false",
        "force: false",
        "",
        "Keel runs these commands. Do not force-update a tag, and do not omit -a or --verify-tag.",
    ]
    lines.extend(shlex.join(command) for command in release.commands())
    return "\n".join(lines) + "\n"


def check(root: Path) -> str:
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        raise ReleaseError("HEAD is not a commit")
    product, python = versions_at(root, "HEAD")
    expected = python_version(product)
    if python != expected:
        raise ReleaseError(
            f"Python version {python} does not match product {product} ({expected})"
        )
    tag = tag_name(product)
    return f"{tag}\nproduct {product}\npython {python}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "plan"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sha", default="HEAD")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            sys.stdout.write(check(args.root))
        else:
            sys.stdout.write(format_plan(describe(args.root, args.sha)))
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
