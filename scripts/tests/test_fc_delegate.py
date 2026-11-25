"""fc-delegate.sh fails closed and passes every hide. Stdlib only.

Uses fake `firstchair` and `bwrap` executables on PATH; never starts a real worker.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fc-delegate.sh"
BASH = shutil.which("bash") or "/bin/bash"

# The fake records each call's argv (one JSON list per line) and answers a dry run with the
# "confined" value from FAKE_CONFINED.
FAKE_FIRSTCHAIR = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "display": os.environ.get("DISPLAY"), "cwd": os.getcwd()}) + "\\n")
if "--dry-run" in sys.argv:
    print(json.dumps({"confined": os.environ.get("FAKE_CONFINED", "true") == "true"}))
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class FcDelegateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        _exe(self.bin / "firstchair", FAKE_FIRSTCHAIR)
        _exe(self.bin / "bwrap", "#!/bin/sh\nexit 0\n")
        self.runtime = self.tmp / "run user"
        self.runtime.mkdir()
        for name in ("bus", "systemd", "at-spi", "keyring"):
            (self.runtime / name).mkdir()
        (self.runtime / ".mutter-Xwaylandauth.ABC123").write_text("cookie")
        (self.runtime / "vscode-git-1.sock").write_text("")
        (self.runtime / "anything-new.sock").write_text("")
        (self.runtime / "fnm_multishells").mkdir()
        self.private = self.tmp / "private data"
        self.private.mkdir()
        self.log = self.tmp / "calls.jsonl"

    def run_script(
        self, *args: str, cwd: Path | None = None, **env_over: str | None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "HOLDFAST_INSTANCE_DIR": "none",
            "DISPLAY": ":0",
        }
        env.update(env_over)
        for key in [k for k, v in env.items() if v is None]:
            del env[key]
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            env=env,
            cwd=cwd or self.tmp,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_passes_every_hide_once_and_keeps_paths_with_spaces_whole(self) -> None:
        result = self.run_script("codex", "--mode", "read", "--name", "x", "task text")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)  # dry run, then the real run
        argv = calls[1]["argv"]
        assert isinstance(argv, list)
        self.assertNotIn("--dry-run", argv)
        self.assertEqual(argv[:3], ["delegate", "run", "codex"])
        self.assertIn("--confine", argv)
        hidden = [argv[i + 1] for i, a in enumerate(argv) if a == "--hide"]
        self.assertEqual(len(hidden), len(set(hidden)))
        self.assertIn(str(self.private.resolve()), hidden)
        for name in ("bus", "systemd", "at-spi", "keyring", ".mutter-Xwaylandauth.ABC123",
                     "vscode-git-1.sock", "anything-new.sock"):
            self.assertIn(str((self.runtime / name).resolve()), hidden, name)
        self.assertNotIn(str((self.runtime / "fnm_multishells").resolve()), hidden)
        self.assertIsNone(calls[1]["display"])

    def test_instance_directory_and_engine_socket_are_hidden_when_set(self) -> None:
        # H2: the real-data instance's bind-mounted directory and a dedicated engine socket.
        instance = self.tmp / "instance dir"
        instance.mkdir()
        sock = self.tmp / "engine.sock"
        sock.write_text("")
        result = self.run_script(
            "codex",
            "task",
            HOLDFAST_INSTANCE_DIR=str(instance),
            HOLDFAST_INSTANCE_DOCKER_SOCK=str(sock),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[1]["argv"]
        hidden = [argv[i + 1] for i, a in enumerate(argv) if a == "--hide"]
        self.assertIn(str(instance.resolve()), hidden)
        self.assertIn(str(sock.resolve()), hidden)

    def test_unset_instance_variables_add_no_hides(self) -> None:
        result = self.run_script("codex", "task")
        self.assertEqual(result.returncode, 0, result.stderr)
        hidden = [a for a in self.calls()[1]["argv"] if "instance" in a]
        self.assertEqual(hidden, [])

    def pointer(self, name: str, value: str, config_home: Path | None = None) -> None:
        base = config_home or (self.tmp / ".config")
        (base / "holdfast").mkdir(parents=True, exist_ok=True)
        (base / "holdfast" / name).write_text(value + "\n")

    def hidden(self) -> list[str]:
        argv = self.calls()[1]["argv"]
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--hide"]

    def test_neither_variable_nor_pointer_fails_closed(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("HOLDFAST_INSTANCE_DIR", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_none_is_the_explicit_opt_out(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR="none")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_instance_directory_refuses(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=str(self.tmp / "nope"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_pointer_file_supplies_the_directory_when_the_variable_is_unset(self) -> None:
        instance = self.tmp / "ptr dir"
        instance.mkdir()
        self.pointer("instance-dir", str(instance))
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(instance.resolve()), self.hidden())

    def test_pointer_file_honours_xdg_config_home(self) -> None:
        instance = self.tmp / "xdg dir"
        instance.mkdir()
        xdg = self.tmp / "xdg"
        self.pointer("instance-dir", str(instance), xdg)
        result = self.run_script(
            "codex", "task", HOLDFAST_INSTANCE_DIR=None, XDG_CONFIG_HOME=str(xdg)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(instance.resolve()), self.hidden())

    def test_pointer_to_a_missing_directory_refuses(self) -> None:
        self.pointer("instance-dir", str(self.tmp / "gone"))
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_variable_and_pointer_must_name_the_same_directory(self) -> None:
        a = self.tmp / "a"
        b = self.tmp / "b"
        a.mkdir()
        b.mkdir()
        self.pointer("instance-dir", str(b))
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=str(a))
        self.assertEqual(result.returncode, 2)
        self.assertIn("different directories", result.stderr)
        self.assertEqual(self.calls(), [])
        # The same directory spelled through a symlink agrees.
        link = self.tmp / "link-to-b"
        link.symlink_to(b)
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=str(link))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(b.resolve()), self.hidden())

    def test_none_comes_only_from_the_variable_and_not_over_a_pointer(self) -> None:
        # A pointer that says none is not an opt-out.
        self.pointer("instance-dir", "none")
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("none", result.stderr)
        # The variable's none can't override a pointer that names a directory.
        real = self.tmp / "real"
        real.mkdir()
        self.pointer("instance-dir", str(real))
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR="none")
        self.assertEqual(result.returncode, 2)
        self.assertIn("pointer", result.stderr)

    def test_instance_path_must_be_a_directory(self) -> None:
        plain = self.tmp / "plain file"
        plain.write_text("x")
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=str(plain))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a directory", result.stderr)

    def test_the_pointer_directory_is_hidden_so_a_worker_cannot_retarget_it(self) -> None:
        instance = self.tmp / "inst"
        instance.mkdir()
        self.pointer("instance-dir", str(instance))
        result = self.run_script("codex", "task", HOLDFAST_INSTANCE_DIR=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str((self.tmp / ".config" / "holdfast").resolve()), self.hidden())

    def test_an_attacker_made_pointer_under_xdg_config_home_is_refused(self) -> None:
        decoy = self.tmp / "decoy"
        decoy.mkdir()
        evil = self.tmp / "evil"
        # Says none: refused. Names a decoy while the variable names the real one: refused.
        self.pointer("instance-dir", "none", evil)
        result = self.run_script(
            "codex", "task", HOLDFAST_INSTANCE_DIR=None, XDG_CONFIG_HOME=str(evil)
        )
        self.assertEqual(result.returncode, 2)
        real = self.tmp / "real2"
        real.mkdir()
        self.pointer("instance-dir", str(decoy), evil)
        result = self.run_script(
            "codex", "task", HOLDFAST_INSTANCE_DIR=str(real), XDG_CONFIG_HOME=str(evil)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("different directories", result.stderr)

    def test_the_worker_starts_from_the_repo_root_not_the_callers_directory(self) -> None:
        instance = self.tmp / "inst2"
        instance.mkdir()
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "HOLDFAST_INSTANCE_DIR": str(instance),
        }
        result = subprocess.run(
            [BASH, str(SCRIPT), "codex", "task"],
            env=env,
            cwd=instance,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in self.calls():
            self.assertEqual(Path(call["cwd"]).resolve(), ROOT.resolve())

    def test_missing_engine_socket_refuses_and_a_pointer_socket_is_hidden(self) -> None:
        result = self.run_script(
            "codex", "task", HOLDFAST_INSTANCE_DOCKER_SOCK=str(self.tmp / "no.sock")
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)
        sock = self.tmp / "engine2.sock"
        sock.write_text("")
        self.pointer("instance-docker-sock", str(sock))
        self.log.unlink(missing_ok=True)
        result = self.run_script("codex", "task")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(sock.resolve()), self.hidden())
        self.pointer("instance-docker-sock", str(self.tmp / "gone.sock"))
        self.assertEqual(self.run_script("codex", "task").returncode, 2)

    def test_repo_defaults_to_the_callers_checkout_and_explicit_wins(self) -> None:
        # A lead running this checkout's wrapper from a worktree hands the worker that worktree.
        work = self.tmp / "work tree"
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        result = self.run_script("codex", "task", cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("--repo") + 1], str(work.resolve()))
        self.assertLess(argv.index("--repo"), argv.index("run"))
        # Outside any repository nothing is added.
        self.log.unlink()
        self.assertEqual(self.run_script("codex", "task").returncode, 0)
        self.assertNotIn("--repo", self.calls()[1]["argv"])
        # An explicit FC_REPO wins, and a relative one is made absolute against the caller.
        self.log.unlink()
        self.run_script("codex", "task", cwd=work, FC_REPO="/explicit")
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("--repo") + 1], "/explicit")
        self.log.unlink()
        self.run_script("codex", "task", cwd=work, FC_REPO="sub/repo")
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("--repo") + 1], f"{work}/sub/repo")

    def test_relative_prompt_file_and_output_are_made_absolute_before_the_cd(self) -> None:
        work = self.tmp / "caller"
        work.mkdir()
        result = self.run_script(
            "codex", "--prompt-file", "p.txt", "--output", "o.json", "--name", "n", cwd=work
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("--prompt-file") + 1], f"{work}/p.txt")
        self.assertEqual(argv[argv.index("--output") + 1], f"{work}/o.json")
        self.assertEqual(argv[argv.index("--name") + 1], "n")
        self.log.unlink()
        self.run_script("codex", "--prompt-file=q.txt", "--output=/abs/o.json", "--", "-", cwd=work)
        argv = self.calls()[1]["argv"]
        self.assertIn(f"--prompt-file={work}/q.txt", argv)
        self.assertIn("--output=/abs/o.json", argv)
        # A lone dash (stdin) and ordinary arguments are untouched.
        self.log.unlink()
        self.run_script("codex", "--prompt-file", "-", "relative task words", cwd=work)
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("--prompt-file") + 1], "-")
        self.assertIn("relative task words", argv)

    def test_session_and_repo_go_before_the_subcommands(self) -> None:
        result = self.run_script("grok", "task", FIRSTCHAIR_SESSION="s1", FC_REPO="/r")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[:6], ["--session", "s1", "delegate", "--repo", "/r", "run"])

    def test_missing_private_directory_refuses(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_PRIVATE_DIR=str(self.tmp / "nope"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_private_none_is_explicit_opt_out(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_PRIVATE_DIR="none")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_bubblewrap_refuses(self) -> None:
        # A PATH holding only the tools the script needs, so a system bwrap cannot be found.
        only = self.tmp / "nobwrap"
        only.mkdir()
        _exe(only / "firstchair", FAKE_FIRSTCHAIR)
        for tool in ("env", "id", "realpath", "python3"):
            found = shutil.which(tool)
            assert found is not None
            (only / tool).symlink_to(found)
        result = self.run_script("codex", "task", PATH=str(only))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("bubblewrap", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unconfined_dry_run_refuses_to_start(self) -> None:
        result = self.run_script("codex", "task", FAKE_CONFINED="false")
        self.assertEqual(result.returncode, 2)
        self.assertIn("confined", result.stderr)
        self.assertEqual(len(self.calls()), 1)  # only the dry run

    def test_caller_cannot_pass_its_own_hides_or_a_prefix_of_them(self) -> None:
        for flag in ("--hide", "--hid", "--hi", "--hide=/x", "--confine", "--conf", "--con"):
            with self.subTest(flag=flag):
                result = self.run_script("codex", flag, "/x", "task")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls(), [])

    def test_grok_write_flag_and_its_abbreviations_are_refused(self) -> None:
        for flag in ("--allow-grok-write", "--allow-grok", "--allow", "--all", "--allow-grok-write=1"):
            with self.subTest(flag=flag):
                result = self.run_script("grok", flag, "task")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("read-only", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_ordinary_flags_are_not_mistaken_for_hide_prefixes(self) -> None:
        result = self.run_script("codex", "--mode", "read", "--name", "n", "--help-me", "task")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_planted_module_in_the_working_directory_is_not_imported(self) -> None:
        planted = self.tmp / "planted"
        planted.mkdir()
        (planted / "json.py").write_text(
            "import sys\n"
            "def loads(_s):\n"
            "    return {'confined': True}\n"
        )
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "HOLDFAST_INSTANCE_DIR": "none",
            "FAKE_CONFINED": "false",
        }
        result = subprocess.run(
            [BASH, str(SCRIPT), "codex", "task"],
            env=env,
            cwd=planted,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(len(self.calls()), 1)  # only the dry run

    def test_runs_from_any_directory(self) -> None:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "HOLDFAST_INSTANCE_DIR": "none",
        }
        result = subprocess.run(
            [BASH, str(SCRIPT), "codex", "task"],
            env=env,
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
