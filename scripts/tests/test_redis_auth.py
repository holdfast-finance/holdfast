"""Redis is always password-protected: the ports gate and gen-keys --add-missing."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ports = _load("check_compose_ports", "check-compose-ports.py")
keys = _load("gen_keys", "gen-keys.py")
GEN_KEYS = str(ROOT / "scripts" / "gen-keys.py")


def _run_ports(config: dict[str, object], *flags: str) -> tuple[int, str]:
    err = io.StringIO()
    with (
        mock.patch.object(sys, "argv", ["x", *flags]),
        mock.patch.object(sys, "stdin", io.StringIO(json.dumps(config))),
        redirect_stdout(io.StringIO()),
        redirect_stderr(err),
    ):
        code = ports.main()
    return code, err.getvalue()


AUTHED = ["redis-server", "--appendonly", "no", "--requirepass", "s3cret"]


class PortsRedisAuth(unittest.TestCase):
    def test_authenticated_redis_passes(self) -> None:
        code, _ = _run_ports({"services": {"redis": {"command": AUTHED}}})
        self.assertEqual(code, 0)

    def test_dev_loopback_publish_allowed_only_with_dev_flag(self) -> None:
        port = {"host_ip": "127.0.0.1", "published": "56379", "target": 6379}
        svc = {"redis": {"command": AUTHED, "ports": [port]}}
        self.assertEqual(_run_ports({"services": svc}, "--dev")[0], 0)
        self.assertEqual(_run_ports({"services": svc})[0], 1)

    def test_missing_or_empty_requirepass_fails(self) -> None:
        for command in (["redis-server"], ["redis-server", "--requirepass"], AUTHED[:-1] + [""]):
            code, err = _run_ports({"services": {"redis": {"command": command}}})
            self.assertEqual(code, 1, command)
            self.assertIn("redis:", err)


class AddMissing(unittest.TestCase):
    def _env(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text(text)
        return path

    def test_generate_includes_redis_password(self) -> None:
        self.assertTrue(keys.generate()["REDIS_PASSWORD"])

    def test_appends_only_the_missing_value_and_prints_nothing_secret(self) -> None:
        original = "FERNET_KEY=keep-me\nPOSTGRES_APP_PASSWORD=also-keep\n# note\n"
        path = self._env(original)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(keys.add_missing(path), 0)
        text = path.read_text()
        self.assertTrue(text.startswith(original))
        added = text[len(original) :].strip()
        self.assertTrue(added.startswith("REDIS_PASSWORD=") and len(added) > 20)
        self.assertNotIn(added.split("=", 1)[1], out.getvalue())
        self.assertNotIn("keep-me", out.getvalue())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_fills_an_empty_line_in_place_and_is_idempotent(self) -> None:
        path = self._env("A=1\nREDIS_PASSWORD=\nB=2\n")
        with redirect_stdout(io.StringIO()):
            keys.add_missing(path)
            first = path.read_text()
            keys.add_missing(path)
        lines = first.splitlines()
        self.assertEqual((lines[0], lines[2]), ("A=1", "B=2"))
        self.assertGreater(len(lines[1]), len("REDIS_PASSWORD="))
        self.assertEqual(path.read_text(), first)

    def test_never_mints_values_that_must_match_existing_data(self) -> None:
        self.assertEqual(keys.ADDABLE, ("REDIS_PASSWORD",))

    def test_cli_missing_env_and_bad_combination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / ".env")
            proc = subprocess.run(
                [sys.executable, GEN_KEYS, "--add-missing", "--output", missing],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
        proc = subprocess.run(
            [sys.executable, GEN_KEYS, "--add-missing", "--write"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)


class WriteIncludesRedis(unittest.TestCase):
    def test_new_env_gets_a_redis_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".env"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(keys.write_env(ROOT / ".env.example", target, False), 0)
            line = next(
                x for x in target.read_text().splitlines() if x.startswith("REDIS_PASSWORD=")
            )
            self.assertGreater(len(line), len("REDIS_PASSWORD=") + 20)


if __name__ == "__main__":
    unittest.main()
