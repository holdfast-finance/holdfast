"""guard-volumes.sh blocks volume deletion and deletes under the instance directory (H2).

Each rule has commands it must block and normal commands it must allow. Stdlib only; the hook
reads the PreToolUse JSON on stdin and exits 2 to block.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "guard-volumes.sh"
BASH = shutil.which("bash") or "/bin/bash"
JQ = shutil.which("jq")
INSTANCE = "/mnt/vault/hf data"  # a synthetic path, with a space, for the resolved-value rule


def run_hook(
    command: str,
    instance_dir: str | None = None,
    home: str | None = None,
    raw: str | None = None,
    path: str = "/usr/bin:/bin",
) -> subprocess.CompletedProcess[str]:
    # HOME is an empty throwaway directory unless a test supplies one, so a developer's real
    # pointer file is never read.
    with tempfile.TemporaryDirectory() as empty:
        env = {"PATH": path, "HOME": home or empty}
        if instance_dir is not None:
            env["HOLDFAST_INSTANCE_DIR"] = instance_dir
        payload = (
            raw
            if raw is not None
            else json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        )
        return subprocess.run(
            [BASH, str(HOOK)], input=payload, env=env, capture_output=True, text=True, check=False
        )


@unittest.skipIf(JQ is None, "jq is required by the hook")
class GuardVolumesTest(unittest.TestCase):
    def assert_blocked(self, command: str, instance_dir: str | None = None) -> None:
        result = run_hook(command, instance_dir)
        self.assertEqual(result.returncode, 2, f"expected a block: {command!r}")
        self.assertIn("guard-volumes: BLOCKED", result.stderr)

    def assert_allowed(self, command: str, instance_dir: str | None = None) -> None:
        result = run_hook(command, instance_dir)
        self.assertEqual(result.returncode, 0, f"expected allow: {command!r}: {result.stderr}")

    def test_rule1_volume_rm_and_prune_are_blocked(self) -> None:
        for cmd in (
            "docker volume rm holdfast_pgdata",
            "docker volume remove holdfast_pgdata",
            "docker  volume   rm  x",
            "docker volume prune -f",
            "docker -H unix:///x.sock volume rm x",
            "docker --context other volume prune",
            "podman volume rm x",
            "sudo docker volume rm x",
            "docker volume ls -q | xargs docker volume rm",
            "cd /tmp && docker volume prune --all",
            "docker volume\nrm x",
            'bash -c "docker volume rm x"',
        ):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_rule1_normal_docker_volume_use_is_allowed(self) -> None:
        for cmd in (
            "docker volume ls",
            "docker volume inspect holdfast_pgdata",
            "docker volume create scratch",
            "docker compose ps",
            "docker compose down",
            "make sandbox-destroy",
            "make test",
            "docker image prune -f",
            "docker container prune -f",
        ):
            with self.subTest(cmd=cmd):
                self.assert_allowed(cmd)

    def test_rule2_system_prune_with_volumes_is_blocked(self) -> None:
        for cmd in (
            "docker system prune --volumes",
            "docker system prune -af --volumes",
            "docker system prune --volumes -f",
            "docker system prune --volumes=true",
            "docker --context x system  prune --all --volumes",
            "podman system prune --volumes",
        ):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_rule2_system_prune_without_volumes_is_allowed(self) -> None:
        for cmd in ("docker system prune", "docker system prune -af", "docker system df"):
            with self.subTest(cmd=cmd):
                self.assert_allowed(cmd)

    def test_rule3_delete_or_move_under_the_instance_directory_is_blocked(self) -> None:
        for cmd in (
            'rm -rf "$HOLDFAST_INSTANCE_DIR"',
            "rm -rf ${HOLDFAST_INSTANCE_DIR}/postgres",
            'cd "$HOLDFAST_INSTANCE_DIR" && rm -rf *',
            "sudo rm -rf ~/holdfast-instance/postgres",
            "rm instance.env",
            "rm -f /somewhere/instance.env",
            "rmdir $HOLDFAST_INSTANCE_DIR/redis",
            "shred -u $HOLDFAST_INSTANCE_DIR/instance.env",
            "find $HOLDFAST_INSTANCE_DIR -delete",
            "mv $HOLDFAST_INSTANCE_DIR /tmp/elsewhere",
            "truncate -s 0 $HOLDFAST_INSTANCE_DIR/documents/x",
            "\\rm -rf $HOLDFAST_INSTANCE_DIR",
        ):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_rule3_resolved_value_is_blocked_when_the_session_has_it(self) -> None:
        self.assert_blocked(f"rm -rf '{INSTANCE}/postgres'", INSTANCE)
        self.assert_blocked(f"rm -rf {INSTANCE}", INSTANCE + "/")
        # A path that only shares a prefix with a different directory is not special.
        self.assert_allowed("rm -rf /mnt/other/build", INSTANCE)

    def test_rule3_ordinary_deletes_and_instance_use_are_allowed(self) -> None:
        for cmd in (
            "rm -rf node_modules",
            "rm -f .env.sandbox",
            "rm -rf web/dist backend/.pytest_cache",
            'ls -la "$HOLDFAST_INSTANCE_DIR"',
            "make instance-up",
            "make instance-down",
            "chmod 700 $HOLDFAST_INSTANCE_DIR",
            "docker compose -p holdfast-instance ps",
            "git status",
            "git rm docs/old.md",
        ):
            with self.subTest(cmd=cmd):
                self.assert_allowed(cmd)

    def test_quoting_and_continuation_cannot_hide_a_command(self) -> None:
        for cmd in (
            'docker "volume" rm x',
            '"docker" volume prune -f',
            "d'ocker' volume rm x",
            "docker volume \\\nrm x",
            "docker vol''ume rm x",
            '"rm" -rf "$HOLDFAST_INSTANCE_DIR"',
            "r''m -rf $HOLDFAST_INSTANCE_DIR/postgres",
            "r\\m -rf $HOLDFAST_INSTANCE_DIR",
            "docker system prune '--volumes'",
        ):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_system_reset_is_blocked(self) -> None:
        for cmd in ("podman system reset", "podman system reset -f", "docker system reset"):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_overwriting_the_env_file_or_instance_files_is_blocked(self) -> None:
        for cmd in (
            ': > "$HOLDFAST_INSTANCE_DIR/instance.env"',
            "echo x >> $HOLDFAST_INSTANCE_DIR/instance.env",
            "echo x >instance.env",
            "cat /dev/null >| /somewhere/instance.env",
            "cp /tmp/x $HOLDFAST_INSTANCE_DIR/instance.env",
            "dd if=/dev/zero of=$HOLDFAST_INSTANCE_DIR/instance.env",
            "echo x | tee $HOLDFAST_INSTANCE_DIR/documents/a",
            "install -m 600 /tmp/x $HOLDFAST_INSTANCE_DIR/instance.env",
            "sed -i s/a/b/ $HOLDFAST_INSTANCE_DIR/instance.env",
            "sed -i.bak s/a/b/ instance.env",
            "sed --in-place s/a/b/ instance.env",
            "truncate -s0 instance.env",
            'echo x > "$HOLDFAST_INSTANCE_DIR"/postgres/PG_VERSION',
        ):
            with self.subTest(cmd=cmd):
                self.assert_blocked(cmd)

    def test_reading_and_unrelated_writes_are_allowed(self) -> None:
        for cmd in (
            "cp .env.example /tmp/x",
            "echo hi > /tmp/out.txt",
            "make instance-up 2>&1",
            "docker compose -p holdfast-instance ps 2>&1",
            "sed -i s/a/b/ README.md",
            "sed -n 1,5p README.md",
            "ls -l $HOLDFAST_INSTANCE_DIR > /tmp/listing",
            "tee /tmp/log",
        ):
            with self.subTest(cmd=cmd):
                self.assert_allowed(cmd)

    def test_pointer_file_names_the_directory_when_the_variable_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / ".config" / "holdfast"
            cfg.mkdir(parents=True)
            (cfg / "instance-dir").write_text("/mnt/vault/hf pointer\n")
            for cmd in (
                "rm -rf '/mnt/vault/hf pointer/postgres'",
                "echo x > '/mnt/vault/hf pointer/documents/a'",
            ):
                with self.subTest(cmd=cmd):
                    self.assertEqual(run_hook(cmd, None, home).returncode, 2)
            self.assertEqual(run_hook("rm -rf /mnt/other", None, home).returncode, 0)
        # No variable and no pointer: only the literal names are recognised.
        self.assert_allowed("rm -rf /mnt/vault/hf-pointer-unknown")

    def test_fails_closed_without_jq_and_on_unparseable_input(self) -> None:
        with tempfile.TemporaryDirectory() as only:
            for tool in ("tr", "grep", "sed", "cat"):
                found = shutil.which(tool)
                assert found is not None
                Path(only, tool).symlink_to(found)
            result = run_hook("ls", path=only)
            self.assertEqual(result.returncode, 2)
            self.assertIn("jq", result.stderr)
        for raw in ("not json", "", '{"tool_input": "oops"}', "[1,2"):
            with self.subTest(raw=raw):
                self.assertEqual(run_hook("", raw=raw).returncode, 2)

    def test_non_bash_payloads_and_empty_commands_pass(self) -> None:
        for payload in ('{"tool_input": {}}', '{"tool_input": {"command": ""}}'):
            result = subprocess.run(
                [BASH, str(HOOK)],
                input=payload,
                env={"PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_settings_json_wires_the_hook_to_bash(self) -> None:
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
        entries = settings["hooks"]["PreToolUse"]
        wired = [
            e
            for e in entries
            if e["matcher"] == "Bash"
            and any("guard-volumes.sh" in h["command"] for h in e["hooks"])
        ]
        self.assertEqual(len(wired), 1)
        self.assertTrue(HOOK.stat().st_mode & 0o111, "hook must be executable")


if __name__ == "__main__":
    unittest.main()
