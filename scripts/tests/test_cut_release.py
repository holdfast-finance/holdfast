"""The public version tag is an annotated v-tag plus a GitHub Release, never a phase tag."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cut_release  # noqa: E402


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def commit_release(repo: Path, product: str, python: str, notes: bool) -> str:
    (repo / "web").mkdir()
    (repo / "backend").mkdir()
    (repo / "web" / "package.json").write_text(json.dumps({"version": product}) + "\n")
    (repo / "backend" / "pyproject.toml").write_text(
        f'[project]\nname = "holdfast"\nversion = "{python}"\n'
    )
    if notes:
        tag = f"v{product}"
        notes_path = repo / "docs" / "releases" / f"{tag}.md"
        notes_path.parent.mkdir(parents=True)
        notes_path.write_text(f"# {tag}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"release {product}")
    return git(repo, "rev-parse", "HEAD")


class MappingTests(unittest.TestCase):
    def test_alpha_beta_rc_and_stable(self) -> None:
        self.assertEqual(cut_release.python_version("0.0.0-alpha"), "0.0.0a0")
        self.assertEqual(cut_release.python_version("0.1.0-beta.1"), "0.1.0b1")
        self.assertEqual(cut_release.python_version("1.0.0-rc.1"), "1.0.0rc1")
        self.assertEqual(cut_release.python_version("1.2.3"), "1.2.3")

    def test_rejects_phase_tags_and_loose_numbers(self) -> None:
        for bad in ("phase-1", "v1.2.3", "1.2", "01.2.3", "1.2.3-dev", "1.2.3-alpha.01"):
            with self.assertRaises(cut_release.ReleaseError):
                cut_release.tag_name(bad)


class PlanTests(unittest.TestCase):
    def test_alpha_plan_is_annotated_prerelease_and_not_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            sha = commit_release(repo, "0.0.0-alpha", "0.0.0a0", notes=True)
            release = cut_release.describe(repo, sha)
            text = cut_release.format_plan(release)
        self.assertEqual(release.tag, "v0.0.0-alpha")
        self.assertTrue(release.prerelease)
        self.assertIn("git tag -a v0.0.0-alpha", text)
        self.assertIn("git push origin refs/tags/v0.0.0-alpha", text)
        self.assertIn("--verify-tag", text)
        self.assertIn("--prerelease", text)
        self.assertIn("--latest false", text)
        self.assertIn("--repo holdfast-finance/holdfast", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("git push --tags", text)
        self.assertNotIn("phase-", text)

    def test_stable_plan_is_latest_and_not_a_prerelease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            sha = commit_release(repo, "1.2.3", "1.2.3", notes=True)
            release = cut_release.describe(repo, sha)
        self.assertFalse(release.prerelease)
        joined = " ".join(release.commands()[2])
        self.assertIn("--latest true", joined)
        self.assertNotIn("--prerelease", joined)

    def test_refuses_a_commit_that_is_not_on_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "commit", "-q", "--allow-empty", "-m", "root")
            git(repo, "checkout", "-q", "-b", "side")
            sha = commit_release(repo, "0.0.0-alpha", "0.0.0a0", notes=True)
            with self.assertRaises(cut_release.ReleaseError):
                cut_release.describe(repo, sha)

    def test_refuses_an_existing_tag_and_a_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            sha = commit_release(repo, "0.0.0-alpha", "0.0.0a0", notes=True)
            git(repo, "tag", "-a", "v0.0.0-alpha", sha, "-m", "already cut")
            with self.assertRaises(cut_release.ReleaseError):
                cut_release.describe(repo, sha)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            sha = commit_release(repo, "0.0.0-alpha", "0.0.0", notes=True)
            with self.assertRaises(cut_release.ReleaseError):
                cut_release.describe(repo, sha)

    def test_refuses_missing_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            sha = commit_release(repo, "0.0.0-alpha", "0.0.0a0", notes=False)
            with self.assertRaises(cut_release.ReleaseError):
                cut_release.describe(repo, sha)

    def test_this_checkout_matches_one_prerelease(self) -> None:
        text = cut_release.check(ROOT)
        self.assertIn("v0.0.0-alpha", text)
        self.assertIn("0.0.0a0", text)


if __name__ == "__main__":
    unittest.main()
