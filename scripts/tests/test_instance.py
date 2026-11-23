"""instance.sh check refuses an unsafe holdfast-instance directory (H2). Stdlib only.

Uses throwaway directories under the system temp dir; never touches a real instance.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "instance.sh"
BASH = shutil.which("bash") or "/bin/bash"


class InstanceCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = self.tmp / "inst"
        self.dir.mkdir(mode=0o700)
        self.dir.chmod(0o700)
        env_file = self.dir / "instance.env"
        env_file.write_text("FERNET_KEY=synthetic\n")
        env_file.chmod(0o600)

    def check(self, directory: str | None, verb: str = "check") -> subprocess.CompletedProcess[str]:
        env = {"PATH": "/usr/bin:/bin"}
        if directory is not None:
            env["HOLDFAST_INSTANCE_DIR"] = directory
        return subprocess.run(
            [BASH, str(SCRIPT), verb], env=env, capture_output=True, text=True, check=False
        )

    def test_mode_700_outside_the_repo_passes(self) -> None:
        result = self.check(str(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_any_other_mode_refuses(self) -> None:
        for mode in (0o755, 0o750, 0o770, 0o777, 0o500, 0o701):
            with self.subTest(mode=oct(mode)):
                self.dir.chmod(mode)
                result = self.check(str(self.dir))
                self.assertEqual(result.returncode, 2)
                self.assertIn("not 700", result.stderr)
        self.dir.chmod(0o700)

    def test_loose_env_file_refuses(self) -> None:
        (self.dir / "instance.env").chmod(0o644)
        result = self.check(str(self.dir))
        self.assertEqual(result.returncode, 2)
        self.assertIn("mode 600", result.stderr)

    def test_missing_env_file_refuses(self) -> None:
        (self.dir / "instance.env").unlink()
        self.assertEqual(self.check(str(self.dir)).returncode, 2)

    def test_unset_relative_missing_and_symlink_refuse(self) -> None:
        self.assertEqual(self.check(None).returncode, 2)
        self.assertEqual(self.check("relative/path").returncode, 2)
        self.assertEqual(self.check(str(self.tmp / "missing")).returncode, 2)
        link = self.tmp / "link"
        os.symlink(self.dir, link)
        result = self.check(str(link))
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr)

    def test_directory_inside_the_repository_refuses(self) -> None:
        inside = ROOT / ".h2-test-instance"
        inside.mkdir(mode=0o700, exist_ok=True)
        self.addCleanup(shutil.rmtree, inside, True)
        inside.chmod(0o700)
        env_file = inside / "instance.env"
        env_file.write_text("x=1\n")
        env_file.chmod(0o600)
        result = self.check(str(inside))
        self.assertEqual(result.returncode, 2)
        self.assertIn("inside the repository", result.stderr)

    def test_env_file_verb_prints_the_path_after_the_check(self) -> None:
        result = self.check(str(self.dir), "env-file")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{self.dir}/instance.env")
        self.dir.chmod(0o755)
        self.assertEqual(self.check(str(self.dir), "env-file").returncode, 2)
        self.dir.chmod(0o700)

    def test_make_instance_up_refuses_before_touching_docker(self) -> None:
        # The Makefile runs the check first, so a loose directory never reaches `docker compose`.
        make = shutil.which("make")
        if make is None:
            self.skipTest("make is not installed")
        self.dir.chmod(0o755)
        result = subprocess.run(
            [make, "-C", str(ROOT), "instance-up", f"HOLDFAST_INSTANCE_DIR={self.dir}"],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not 700", result.stderr)
        self.assertNotIn("docker compose", result.stdout.replace("scripts/instance.sh check", ""))
        self.dir.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
