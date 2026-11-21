"""SHA256SUMS is sha256sum-format, sorted, and refuses an incomplete dist."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dist_checksums  # noqa: E402


class ChecksumTests(unittest.TestCase):
    def test_sorted_sha256sum_lines_for_wheel_and_sdist_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "b-1.tar.gz").write_bytes(b"sdist")
            (dist / "a-1-py3-none-any.whl").write_bytes(b"wheel")
            (dist / ".gitignore").write_text("*")
            self.assertEqual(dist_checksums.main(["x", str(dist)]), 0)
            text = (dist / "SHA256SUMS").read_text()
        lines = text.splitlines()
        self.assertEqual(
            lines,
            [
                f"{hashlib.sha256(b'wheel').hexdigest()}  a-1-py3-none-any.whl",
                f"{hashlib.sha256(b'sdist').hexdigest()}  b-1.tar.gz",
            ],
        )

    def test_refuses_a_dist_without_both_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a-1-py3-none-any.whl").write_bytes(b"w")
            with self.assertRaises(SystemExit):
                dist_checksums.checksums(Path(tmp))


if __name__ == "__main__":
    unittest.main()
