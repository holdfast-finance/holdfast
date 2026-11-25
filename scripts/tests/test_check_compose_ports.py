"""check-compose-ports.py: the --instance and --sandbox modes (H2). Stdlib only.

Synthetic compose configs for the rules, plus a render of the real files when `docker compose`
is available (config only; no daemon, no containers).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-compose-ports.py"
DOCKER = shutil.which("docker")
INSTANCE_DIR = "/srv/hf-instance-test"


def bind(target: str, name: str) -> dict[str, str]:
    return {"type": "bind", "source": f"{INSTANCE_DIR}/{name}", "target": target}


def instance_config() -> dict[str, Any]:
    return {
        "services": {
            "db": {"volumes": [bind("/var/lib/postgresql/data", "postgres")]},
            "redis": {
                "command": ["redis-server", "--requirepass", "x"],
                "volumes": [bind("/data", "redis")],
            },
            "api": {
                "environment": {"APP_ENV": "prod"},
                "volumes": [bind("/data/documents", "documents")],
            },
            "worker": {
                "environment": {"APP_ENV": "prod"},
                "volumes": [bind("/data/documents", "documents")],
            },
            "caddy": {
                "ports": [{"host_ip": "127.0.0.1", "published": "9443", "target": 443}],
                "volumes": [bind("/data", "caddy_data"), bind("/config", "caddy_config")],
            },
        }
    }


def sandbox_config() -> dict[str, Any]:
    env = {"PLAID_ENV": "sandbox", "HOLDFAST_SANDBOX": "true", "PLAID_SECRET_PRODUCTION": ""}
    return {
        "services": {
            "redis": {"command": ["redis-server", "--requirepass", "x"]},
            "api": {"environment": dict(env)},
            "worker": {"environment": dict(env)},
            "caddy": {"ports": [{"host_ip": "127.0.0.1", "published": "9444", "target": 443}]},
        }
    }


def run(config: dict[str, Any], mode: str, instance_dir: str | None = INSTANCE_DIR):
    env = {"PATH": "/usr/bin:/bin"}
    if instance_dir is not None:
        env["HOLDFAST_INSTANCE_DIR"] = instance_dir
    return subprocess.run(
        ["python3", str(SCRIPT), mode],
        input=json.dumps(config),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class InstanceModeTest(unittest.TestCase):
    def test_good_instance_passes(self) -> None:
        result = run(instance_config(), "--instance")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_published_db_or_redis_port_fails(self) -> None:
        for svc in ("db", "redis"):
            with self.subTest(svc=svc):
                cfg = instance_config()
                cfg["services"][svc]["ports"] = [
                    {"host_ip": "127.0.0.1", "published": "5432", "target": 5432}
                ]
                self.assertEqual(run(cfg, "--instance").returncode, 1)

    def test_caddy_off_loopback_or_on_443_fails(self) -> None:
        for host_ip, published in (("0.0.0.0", "9443"), ("100.64.0.1", "9443"), ("127.0.0.1", "443")):
            with self.subTest(host_ip=host_ip, published=published):
                cfg = instance_config()
                cfg["services"]["caddy"]["ports"] = [
                    {"host_ip": host_ip, "published": published, "target": 443}
                ]
                self.assertEqual(run(cfg, "--instance").returncode, 1)

    def test_a_named_volume_for_data_fails(self) -> None:
        for svc, target in (("db", "/var/lib/postgresql/data"), ("api", "/data/documents")):
            with self.subTest(svc=svc):
                cfg = instance_config()
                cfg["services"][svc]["volumes"] = [
                    {"type": "volume", "source": "pgdata", "target": target}
                ]
                result = run(cfg, "--instance")
                self.assertEqual(result.returncode, 1)
                self.assertIn("must be a bind mount", result.stderr)

    def test_missing_mount_and_wrong_directory_fail(self) -> None:
        cfg = instance_config()
        cfg["services"]["redis"]["volumes"] = []
        self.assertEqual(run(cfg, "--instance").returncode, 1)
        cfg = instance_config()
        cfg["services"]["db"]["volumes"][0]["source"] = "/elsewhere/postgres"
        self.assertEqual(run(cfg, "--instance").returncode, 1)

    def test_instance_directory_inside_the_repo_or_unset_fails(self) -> None:
        cfg = instance_config()
        for name in cfg["services"]:
            for mount in cfg["services"][name].get("volumes", []):
                mount["source"] = mount["source"].replace(INSTANCE_DIR, str(ROOT / "data"))
        self.assertEqual(run(cfg, "--instance", str(ROOT / "data")).returncode, 1)
        self.assertEqual(run(instance_config(), "--instance", None).returncode, 1)
        self.assertEqual(run(instance_config(), "--instance", "relative/dir").returncode, 1)

    def test_instance_api_must_render_prod(self) -> None:
        cfg = instance_config()
        cfg["services"]["api"]["environment"]["APP_ENV"] = "dev"
        self.assertEqual(run(cfg, "--instance").returncode, 1)


class SandboxModeTest(unittest.TestCase):
    def test_good_sandbox_passes(self) -> None:
        self.assertEqual(run(sandbox_config(), "--sandbox").returncode, 0)

    def test_production_plaid_env_fails(self) -> None:
        cfg = sandbox_config()
        cfg["services"]["api"]["environment"]["PLAID_ENV"] = "production"
        result = run(cfg, "--sandbox")
        self.assertEqual(result.returncode, 1)
        self.assertIn("PLAID_ENV must render sandbox", result.stderr)

    def test_missing_flag_or_production_secret_fails(self) -> None:
        cfg = sandbox_config()
        del cfg["services"]["worker"]["environment"]["HOLDFAST_SANDBOX"]
        self.assertEqual(run(cfg, "--sandbox").returncode, 1)
        cfg = copy.deepcopy(sandbox_config())
        cfg["services"]["api"]["environment"]["PLAID_SECRET_PRODUCTION"] = "x"
        self.assertEqual(run(cfg, "--sandbox").returncode, 1)

    def test_sandbox_ports_follow_the_same_loopback_rule(self) -> None:
        cfg = sandbox_config()
        cfg["services"]["caddy"]["ports"][0]["host_ip"] = ""
        self.assertEqual(run(cfg, "--sandbox").returncode, 1)
        cfg = sandbox_config()
        cfg["services"]["redis"]["ports"] = [
            {"host_ip": "127.0.0.1", "published": "56379", "target": 6379}
        ]
        self.assertEqual(run(cfg, "--sandbox").returncode, 1)


@unittest.skipIf(DOCKER is None, "docker compose is not installed")
class RealFilesTest(unittest.TestCase):
    """Render the real compose files with synthetic env files and check them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = self.tmp / "synthetic.env"
        subprocess.run(
            ["python3", str(ROOT / "scripts" / "gen-keys.py"), "--write", "--output", str(self.env)],
            check=True,
            capture_output=True,
        )

    def render(self, overlay: str, extra_env: dict[str, str]) -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", str(self.env),
                "-f", "docker-compose.yml", "-f", overlay, "config", "--format", "json",
            ],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.tmp), **extra_env},
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest(f"docker compose config unavailable: {result.stderr[:200]}")
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def test_real_instance_files_pass_and_use_only_bind_mounts(self) -> None:
        inst = str(self.tmp / "instance")
        cfg = self.render("docker-compose.instance.yml", {"HOLDFAST_INSTANCE_DIR": inst})
        result = run(cfg, "--instance", inst)
        self.assertEqual(result.returncode, 0, result.stderr)
        # No service mounts a named volume with data: nothing for a prune to reach.
        for name, svc in cfg["services"].items():
            for mount in svc.get("volumes", []):
                self.assertNotEqual(mount["type"], "volume", f"{name}: {mount}")

    def test_real_sandbox_files_pass_even_if_the_env_file_says_production(self) -> None:
        with self.env.open("a") as fh:
            fh.write("PLAID_ENV=production\nPLAID_SECRET_PRODUCTION=synthetic-not-real\n")
        cfg = self.render("docker-compose.sandbox.yml", {})
        result = run(cfg, "--sandbox")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = cfg["services"]["api"]["environment"]
        self.assertEqual(env["PLAID_ENV"], "sandbox")
        self.assertEqual(env["HOLDFAST_SANDBOX"], "true")

    def test_instance_overlay_without_the_directory_variable_fails_to_render(self) -> None:
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", str(self.env),
                "-f", "docker-compose.yml", "-f", "docker-compose.instance.yml", "config", "-q",
            ],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOLDFAST_INSTANCE_DIR", result.stderr)


if __name__ == "__main__":
    unittest.main()
