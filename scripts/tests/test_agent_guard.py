"""Payload matrices for the external-agent guards (Codex apply_patch, Grok edit tools, Claude hook),
the fail-closed launcher, the Codex command rules and the seat files. Stdlib only.

Each adapter runs inside a throwaway copy of the repo layout so symlink cases are real and hermetic.
Secret-looking values are assembled at runtime from fragments; no literal mnemonic or key is stored.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARK = "ZZMARK"

AK = "sk-ant-" + "api03-" + MARK + "abcdefghijklmnopqrstuvwxyz"
OK = "sk-" + "proj-" + MARK + "abcdefghijklmnopqrstuvwxyz0123456789"
PL = "access-" + "sandbox-12345678-abcd-" + MARK
SSN = MARK + " 123-" + "45-6789"
FER = "FERNET_KEY=" + MARK + "abcdefghijklmnopqrstuvwxyz0123456789ABC="
PEM = MARK + " -----BEGIN " + "PRIVATE KEY-----"
XPRV = MARK + " xprv9s21ZrQH143K" + "a" * 100


def wordlist() -> list[str]:
    return (ROOT / "scripts" / "bip39_english.txt").read_text().split()


def mnemonic(n: int, sep: str = " ") -> str:
    """A synthetic n-word phrase built from wordlist indices at test time."""
    words = wordlist()
    return MARK + sep + sep.join(words[(i * 131 + 7) % 2048] for i in range(n))


class Sandbox:
    """A temp repo with copies of the guards and real symlinks that point at private and outside paths."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "repo"
        self.outside = base / "outside"
        self.outside.mkdir()
        for d in ["docs", "data", "secrets", ".claude", "backend", "scripts", ".codex/hooks", ".grok/hooks",
                  ".claude/hooks"]:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for rel in ["scripts/agent_guard.py", "scripts/bip39_english.txt", ".codex/hooks/patch.py",
                    ".grok/hooks/guard-secrets.sh", ".claude/hooks/guard-secrets.sh",
                    "scripts/external-agent-launch.sh"]:
            shutil.copy(ROOT / rel, self.root / rel)
        os.symlink("../data", self.root / "docs" / "zz-lnk-data")
        os.symlink("../.claude", self.root / "docs" / "zz-lnk-claude")
        os.symlink(self.outside, self.root / "docs" / "zz-lnk-out")

    def close(self) -> None:
        self._tmp.cleanup()

    def grok(self, payload: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        e = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        e.update(env or {})
        return subprocess.run(["bash", str(self.root / ".grok/hooks/guard-secrets.sh")], input=payload,
                              capture_output=True, text=True, cwd=self.root, env=e)

    def codex(self, payload: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["python3", str(self.root / ".codex/hooks/patch.py"), "guard"], input=payload,
                              capture_output=True, text=True, cwd=cwd or self.root)

    def claude(self, tool_input: dict) -> subprocess.CompletedProcess:
        e = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        return subprocess.run(["bash", str(self.root / ".claude/hooks/guard-secrets.sh")],
                              input=json.dumps({"tool_input": tool_input}), capture_output=True, text=True,
                              cwd=self.root, env=e)


def grok_write(path: str, contents: str = "hello") -> str:
    return json.dumps({"tool_name": "write", "tool_input": {"path": path, "contents": contents}})


def grok_replace(path: str, new: str) -> str:
    return json.dumps({"tool_name": "search_replace", "tool_input": {"path": path, "old_string": "x", "new_string": new}})


def patch(path: str, body: str = "hello", header: str = "Add File") -> str:
    lines = "\n".join("+" + line for line in body.split("\n"))
    return f"*** Begin Patch\n*** {header}: {path}\n{lines}\n*** End Patch\n"


def codex_event(patch_text: str, cwd: Path | str) -> str:
    return json.dumps({"cwd": str(cwd), "tool_name": "apply_patch", "tool_input": {"command": patch_text}})


class GuardCase(unittest.TestCase):
    sb: Sandbox

    @classmethod
    def setUpClass(cls) -> None:
        cls.sb = Sandbox()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.sb.close()

    def expect(self, result: subprocess.CompletedProcess, rc: int, name: str) -> None:
        self.assertEqual(result.returncode, rc, f"{name}: rc={result.returncode} stderr={result.stderr[:200]!r}")
        self.assertNotIn(MARK, result.stderr, f"{name}: payload echoed to stderr")
        self.assertNotIn(MARK, result.stdout, f"{name}: payload echoed to stdout")


class GrokMatrix(GuardCase):
    def table(self) -> list[tuple[str, str, int]]:
        r = self.sb.root
        w, sr = grok_write, grok_replace
        return [
            ("normal edit", w("docs/x.md"), 0),
            ("normal edit abs path", w(f"{r}/docs/x.md"), 0),
            ("env example", w(".env.example", "A="), 0),
            ("test SSN 000-", w("docs/x.md", "000-12-3456"), 0),
            ("11 BIP39 words is not a mnemonic", w("docs/x.md", mnemonic(11)), 0),
            ("fake SSN", w("docs/x.md", SSN), 2),
            ("fake SSN via search_replace", sr("docs/x.md", SSN), 2),
            ("Anthropic key", w("docs/x.md", AK), 2),
            ("OpenAI key", w("docs/x.md", OK), 2),
            ("Plaid token", w("docs/x.md", PL), 2),
            ("Fernet key", w("docs/x.md", FER), 2),
            ("PEM key", w("docs/x.md", PEM), 2),
            ("xprv", w("docs/x.md", XPRV), 2),
            ("seed phrase 12", w("docs/x.md", mnemonic(12)), 2),
            ("seed phrase 15 comma", w("docs/x.md", mnemonic(15, ", ")), 2),
            ("seed phrase 18 newline", w("docs/x.md", mnemonic(18, "\n")), 2),
            ("seed phrase 24 numbered", w("docs/x.md", "\n".join(f"{i}. {x}" for i, x in enumerate(mnemonic(24).split()[1:], 1))), 2),
            ("seed phrase in array leaves", json.dumps({"tool_input": {"path": "docs/x", "contents": mnemonic(12).split()}}), 2),
            (".env", w(".env", "A=1"), 2),
            (".env.local", w(".env.local", "A=1"), 2),
            (".env.test", w(".env.test", "A=1"), 2),
            (".env.staging", w(".env.staging", "A=1"), 2),
            ("prod.env", w("prod.env", "A=1"), 2),
            ("backend/.env.production", w("backend/.env.production", "A=1"), 2),
            ("abs .env", w(f"{r}/.env", "A=1"), 2),
            ("./.env", w("./.env", "A=1"), 2),
            ("data/x.csv", w("data/x.csv", "a"), 2),
            ("./data/x.csv", w("./data/x.csv", "a"), 2),
            ("abs data/inbox", w(f"{r}/data/inbox/x.pdf", "a"), 2),
            ("backend/data/x", w("backend/data/x.csv", "a"), 2),
            ("secrets/k", w("secrets/k", "a"), 2),
            ("nested secrets/k", w("backend/secrets/k", "a"), 2),
            ("exports/x.csv", w("exports/x.csv", "a"), 2),
            ("../ escape", w("../../outside.md", "a"), 2),
            ("docs/../data/x", w("docs/../data/x", "a"), 2),
            ("docs/../.env", w("docs/../.env", "a"), 2),
            ("abs outside", w("/tmp/evil.md", "a"), 2),
            ("abs outside home", w("/home/someone/.bashrc", "a"), 2),
            ("symlink to data", w("docs/zz-lnk-data/x", "a"), 2),
            ("symlink to .claude", w("docs/zz-lnk-claude/settings.json", "a"), 2),
            ("symlink outside repo", w("docs/zz-lnk-out/evil.md", "a"), 2),
            ("CLAUDE.md", w("CLAUDE.md", "a"), 2),
            ("claude.md case", w("claude.md", "a"), 2),
            (".claude/settings.json", w(".claude/settings.json", "a"), 2),
            (".claude/hooks/guard-secrets.sh", w(".claude/hooks/guard-secrets.sh", "exit 0"), 2),
            (".grok/hooks/guard-secrets.sh", w(".grok/hooks/guard-secrets.sh", "exit 0"), 2),
            (".grok/config.toml", w(".grok/config.toml", "x"), 2),
            (".codex/hooks/patch.py", w(".codex/hooks/patch.py", "x"), 2),
            (".codex/rules", w(".codex/rules/holdfast.rules", "x"), 2),
            (".agents/skills", w(".agents/skills/x/SKILL.md", "x"), 2),
            ("AGENTS.md", w("AGENTS.md", "x"), 2),
            (".githooks/pre-commit", w(".githooks/pre-commit", "x"), 2),
            (".gitleaks.toml", w(".gitleaks.toml", "x"), 2),
            ("scripts/agent_guard.py", w("scripts/agent_guard.py", "x"), 2),
            ("scripts/bip39_english.txt", w("scripts/bip39_english.txt", "x"), 2),
            ("truncated flag true", '{"toolInputTruncated":true,"tool_input":{"path":"docs/x.md","contents":"hi"}}', 2),
            ("truncated flag string", '{"toolInputTruncated":"false","tool_input":{"path":"docs/x.md","contents":"hi"}}', 2),
            ("truncated flag null", '{"toolInputTruncated":null,"tool_input":{"path":"docs/x.md","contents":"hi"}}', 2),
            ("truncated flag false is fine", '{"toolInputTruncated":false,"tool_input":{"path":"docs/x.md","contents":"hi"}}', 0),
            ("truncated JSON text", '{"tool_input":{"path":".env","contents":"' + MARK, 2),
            ("empty stdin", "", 2),
            ("not JSON", MARK + " garbage", 2),
            ("JSON array", "[1]", 2),
            ("tool_input string", json.dumps({"tool_input": MARK}), 2),
            ("tool_input null", '{"tool_input":null}', 2),
            ("tool_input missing", "{}", 2),
            ("tool_input list", json.dumps({"tool_input": [MARK]}), 2),
            ("no path at all", json.dumps({"tool_input": {"contents": "hi"}}), 2),
            ("path is not a string", json.dumps({"tool_input": {"path": 5, "contents": "hi"}}), 2),
            ("contents is array with SSN", json.dumps({"tool_input": {"path": "docs/x", "contents": [SSN]}}), 2),
            ("unknown field new_str", json.dumps({"tool_input": {"path": "docs/x.md", "new_str": SSN}}), 2),
            ("hashline_edit edits[].content", json.dumps({"tool_name": "hashline_edit", "tool_input": {"path": "docs/x.md", "edits": [{"content": SSN}]}}), 2),
            ("hashline_edit edits[].new_string", json.dumps({"tool_name": "hashline_edit", "tool_input": {"path": "docs/x.md", "edits": [{"new_string": SSN}]}}), 2),
            ("hashline_edit nested .env path", json.dumps({"tool_name": "hashline_edit", "tool_input": {"path": "docs/x.md", "edits": [{"path": ".env", "content": "a"}]}}), 2),
            ("file_path ok, path .env", json.dumps({"tool_input": {"file_path": "docs/x.md", "path": ".env", "contents": "a"}}), 2),
            ("file_path .env, path ok", json.dumps({"tool_input": {"file_path": ".env", "path": "docs/x.md", "contents": "a"}}), 2),
            ("notebook_path .env", json.dumps({"tool_input": {"notebook_path": ".env", "new_source": "a"}}), 2),
            ("NUL in path", json.dumps({"tool_input": {"path": "docs/x\u0000.md", "contents": "a"}}), 2),
        ]

    def test_matrix(self) -> None:
        for name, payload, rc in self.table():
            with self.subTest(name):
                self.expect(self.sb.grok(payload), rc, name)

    def test_without_project_dir_env(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_PROJECT_DIR", "GROK_WORKSPACE_ROOT"}}
        for payload, rc in [(grok_write("data/x", "a"), 2), (grok_write("docs/x.md"), 0)]:
            r = subprocess.run(["bash", str(self.sb.root / ".grok/hooks/guard-secrets.sh")], input=payload,
                               capture_output=True, text=True, cwd=self.sb.root, env=env)
            self.expect(r, rc, "no CLAUDE_PROJECT_DIR")

    def test_adapter_fails_closed_when_guard_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            hook = Path(d) / ".grok" / "hooks"
            hook.mkdir(parents=True)
            shutil.copy(ROOT / ".grok/hooks/guard-secrets.sh", hook)
            r = subprocess.run(["bash", str(hook / "guard-secrets.sh")], input=grok_write("docs/x.md"),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)


class CodexMatrix(GuardCase):
    def ev(self, p: str, cwd: Path | str | None = None) -> str:
        return codex_event(p, cwd or self.sb.root)

    def table(self) -> list[tuple[str, str, int]]:
        r = self.sb.root
        ev = self.ev
        rows: list[tuple[str, str, int]] = [
            ("normal add", ev(patch("docs/x.md")), 0),
            ("normal update", ev("*** Begin Patch\n*** Update File: docs/x.md\n@@\n-old\n+new\n*** End Patch\n"), 0),
            ("normal from subdir cwd", ev(patch("app/x.py"), r / "backend"), 0),
            ("env example", ev(patch(".env.example")), 0),
            ("test SSN 000-", ev(patch("docs/x.md", "000-12-3456")), 0),
            ("seed 11 words", ev(patch("docs/x.md", mnemonic(11) + "\n" + MARK)), 0),
        ]
        for n, s in [("fake SSN", SSN), ("Anthropic key", AK), ("OpenAI key", OK), ("Plaid token", PL),
                     ("Fernet key", FER), ("PEM", PEM), ("xprv", XPRV), ("seed 12", mnemonic(12)),
                     ("seed 24", mnemonic(24, "\n"))]:
            rows.append((n, ev(patch("docs/x.md", s)), 2))
        rows += [
            ("SSN in update hunk", ev(f"*** Begin Patch\n*** Update File: docs/x.md\n@@\n-old\n+{SSN}\n*** End Patch\n"), 2),
            ("SSN in non-+ context line", ev(f"*** Begin Patch\n*** Update File: docs/x.md\n@@ {SSN}\n-a\n+b\n*** End Patch\n"), 2),
        ]
        for p in [".env", ".env.local", ".env.test", "prod.env", "backend/.env.production", f"{r}/.env", "./.env",
                  "data/x.csv", "./data/x.csv", f"{r}/data/inbox/x.pdf", "backend/data/x.csv", "secrets/k",
                  "exports/x.csv", "../../outside.md", "docs/../data/x", "/tmp/evil.md", "/home/someone/.bashrc",
                  "docs/zz-lnk-data/k", "docs/zz-lnk-out/evil.md", "docs/zz-lnk-claude/settings.json",
                  "CLAUDE.md", "claude.md", ".claude/settings.json", ".claude/hooks/guard-secrets.sh",
                  ".codex/hooks/patch.py", ".codex/config.toml", ".codex/rules/holdfast.rules",
                  ".grok/hooks/guard-secrets.sh", ".grok/config.toml", ".agents/skills/x/SKILL.md", "AGENTS.md",
                  ".githooks/pre-commit", ".gitleaks.toml", "scripts/agent_guard.py", "scripts/bip39_english.txt"]:
            rows.append((f"path {p}", ev(patch(p)), 2))
        rows += [
            ("Delete File CLAUDE.md", ev("*** Begin Patch\n*** Delete File: CLAUDE.md\n*** End Patch\n"), 2),
            ("Delete File .codex", ev("*** Begin Patch\n*** Delete File: .codex/rules/holdfast.rules\n*** End Patch\n"), 2),
            ("Move to .env", ev("*** Begin Patch\n*** Update File: docs/x.md\n*** Move to: .env\n@@\n-a\n+b\n*** End Patch\n"), 2),
            ("Move to AGENTS.md", ev("*** Begin Patch\n*** Update File: docs/x.md\n*** Move to: AGENTS.md\n@@\n-a\n+b\n*** End Patch\n"), 2),
            ("second file is private", ev("*** Begin Patch\n*** Add File: docs/ok.md\n+a\n*** Add File: .env\n+A=1\n*** End Patch\n"), 2),
            ("leading-space header", ev("*** Begin Patch\n  *** Add File: .env\n+A=1\n*** End Patch\n"), 2),
            ("trailing-space path", ev("*** Begin Patch\n*** Add File: .env \n+A=1\n*** End Patch\n"), 2),
            ("no space after colon", ev("*** Begin Patch\n*** Add File:.env\n+A=1\n*** End Patch\n"), 2),
            ("CRLF patch", ev("*** Begin Patch\r\n*** Add File: .env\r\n+A=1\r\n*** End Patch\r\n"), 2),
            ("heredoc-wrapped apply_patch", ev("apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: .env\n+A=1\n*** End Patch\nEOF\n"), 2),
            ("no headers", ev("*** Begin Patch\n*** End Patch\n"), 2),
            ("empty stdin", "", 2),
            ("not JSON", MARK + " garbage", 2),
            ("truncated JSON", '{"cwd":"' + str(r) + '","tool_input":{"command":"*** Add File: .env ' + MARK, 2),
            ("missing tool_input", json.dumps({"cwd": str(r)}), 2),
            ("tool_input null", json.dumps({"cwd": str(r), "tool_input": None}), 2),
            ("tool_input string", json.dumps({"cwd": str(r), "tool_input": MARK}), 2),
            ("command is list (argv form)", json.dumps({"cwd": str(r), "tool_input": {"command": ["apply_patch", patch(".env")]}}), 2),
            ("input instead of command", json.dumps({"cwd": str(r), "tool_input": {"input": patch(".env")}}), 2),
            ("truncated flag", json.dumps({"cwd": str(r), "toolInputTruncated": True, "tool_input": {"command": patch("docs/x.md")}}), 2),
            ("cwd outside repo, abs .env", ev(patch(f"{r}/.env"), "/tmp"), 2),
            ("cwd outside repo, relative", ev(patch("docs/x.md"), "/tmp"), 2),
            ("missing cwd, relative .env", json.dumps({"tool_input": {"command": patch(".env")}}), 2),
        ]
        return rows

    def test_matrix(self) -> None:
        for name, payload, rc in self.table():
            with self.subTest(name):
                self.expect(self.sb.codex(payload), rc, name)

    def test_patch_adapter_rejects_unknown_action(self) -> None:
        r = subprocess.run(["python3", str(self.sb.root / ".codex/hooks/patch.py"), "format"],
                           input=codex_event(patch("docs/x.md"), self.sb.root), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_patch_adapter_fails_closed_when_guard_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            hook = Path(d) / ".codex" / "hooks"
            hook.mkdir(parents=True)
            shutil.copy(ROOT / ".codex/hooks/patch.py", hook)
            r = subprocess.run(["python3", str(hook / "patch.py"), "guard"],
                               input=codex_event(patch("docs/x.md"), d), capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)


class ClaudeHook(GuardCase):
    """The Claude hook keeps its old behaviour and exit codes, and gains the seed-phrase check."""

    def test_old_cases_and_seed(self) -> None:
        r = self.sb.root
        rows = [
            ("normal edit", {"file_path": f"{r}/docs/x.md", "content": "hello"}, 0),
            ("test SSN", {"file_path": f"{r}/docs/x.md", "content": "000-12-3456"}, 0),
            ("11 words", {"file_path": f"{r}/docs/x.md", "content": mnemonic(11)}, 0),
            (".env", {"file_path": f"{r}/.env", "content": "A=1"}, 2),
            (".env.local", {"file_path": f"{r}/.env.local", "content": "A=1"}, 2),
            ("data/", {"file_path": f"{r}/data/x.csv", "content": "a"}, 2),
            ("secrets/", {"file_path": f"{r}/secrets/k", "content": "a"}, 2),
            ("Plaid", {"file_path": f"{r}/docs/x", "content": PL}, 2),
            ("Anthropic", {"file_path": f"{r}/docs/x", "content": AK}, 2),
            ("OpenAI", {"file_path": f"{r}/docs/x", "new_string": OK}, 2),
            ("xprv", {"file_path": f"{r}/docs/x", "content": XPRV}, 2),
            ("PEM", {"file_path": f"{r}/docs/x", "content": PEM}, 2),
            ("Fernet", {"file_path": f"{r}/docs/x", "content": FER}, 2),
            ("SSN", {"file_path": f"{r}/docs/x", "content": SSN}, 2),
            ("seed 12", {"file_path": f"{r}/docs/x", "content": mnemonic(12)}, 2),
            ("seed 24 in edits[]", {"file_path": f"{r}/docs/x", "edits": [{"new_string": mnemonic(24)}]}, 2),
            ("seed in notebook new_source", {"notebook_path": f"{r}/docs/n.ipynb", "new_source": mnemonic(12)}, 2),
        ]
        for name, tool_input, rc in rows:
            with self.subTest(name):
                self.expect(self.sb.claude(tool_input), rc, name)

    def test_blocks_when_shared_guard_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            hook = Path(d) / ".claude" / "hooks"
            hook.mkdir(parents=True)
            shutil.copy(ROOT / ".claude/hooks/guard-secrets.sh", hook)
            r = subprocess.run(["bash", str(hook / "guard-secrets.sh")], input=json.dumps(
                {"tool_input": {"file_path": f"{d}/docs/x", "content": "hi"}}), capture_output=True, text=True,
                env={**os.environ, "CLAUDE_PROJECT_DIR": d})
            self.assertEqual(r.returncode, 2)


class SeedDetection(unittest.TestCase):
    def test_wordlist_is_the_official_list(self) -> None:
        import hashlib

        raw = (ROOT / "scripts" / "bip39_english.txt").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda")
        self.assertEqual(len(wordlist()), 2048)

    def test_prose_is_not_flagged(self) -> None:
        sb = Sandbox()
        try:
            prose = "The quick brown fox jumps over the lazy dog. About a dozen words appear above, absent a seed."
            r = sb.grok(grok_write("docs/x.md", prose))
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            sb.close()

    def test_every_run_length_from_12(self) -> None:
        sb = Sandbox()
        try:
            for n in (12, 15, 18, 21, 24):
                with self.subTest(n=n):
                    self.assertEqual(sb.grok(grok_write("docs/x.md", mnemonic(n))).returncode, 2)
        finally:
            sb.close()


class Launcher(unittest.TestCase):
    """scripts/external-agent-launch.sh fails closed. id, gh and the agent CLIs are shims on PATH."""

    def run_launcher(self, user: str, push: str | None, args: list[str]) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "repo" / "scripts").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/external-agent-launch.sh", base / "repo/scripts")
            bin_dir = base / "bin"
            bin_dir.mkdir()
            log = base / "ran.log"
            shims = {
                "id": f'echo "{user}"\n',
                "gh": ('exit 1\n' if push is None else f'echo "{push}"\n'),
                "codex": f'echo "codex $PWD $*" >> "{log}"\n',
                "grok": f'echo "grok $PWD $*" >> "{log}"\n',
            }
            for name, body in shims.items():
                (bin_dir / name).write_text("#!/bin/sh\n" + body)
                (bin_dir / name).chmod(0o755)
            env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": d}
            r = subprocess.run(["bash", str(base / "repo/scripts/external-agent-launch.sh"), *args],
                               capture_output=True, text=True, env=env)
            return r, log.read_text() if log.exists() else ""

    def test_launches_only_as_machine_user_with_read_role(self) -> None:
        r, ran = self.run_launcher("holdfast-agents", "false", ["codex", "exec", "go"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("codex", ran)
        self.assertIn("exec go", ran)
        self.assertIn("/repo exec", ran)  # ran from the repo root
        r, ran = self.run_launcher("holdfast-agents", "false", ["grok", "--permission-mode", "acceptEdits", "go"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("grok", ran)

    def test_refuses(self) -> None:
        cases = [
            ("wrong user", "someone", "false", ["codex"]),
            ("owner user", "geekedsilicon", "false", ["grok"]),
            ("can push", "holdfast-agents", "true", ["codex"]),
            ("push unreadable", "holdfast-agents", None, ["codex"]),
            ("push is junk", "holdfast-agents", "null", ["codex"]),
            ("push is empty", "holdfast-agents", "", ["codex"]),
            ("unknown tool", "holdfast-agents", "false", ["bash"]),
            ("no args", "holdfast-agents", "false", []),
            ("sandbox flag", "holdfast-agents", "false", ["codex", "-s", "danger-full-access", "x"]),
            ("bypass flag", "holdfast-agents", "false", ["codex", "--dangerously-bypass-approvals-and-sandbox"]),
            ("yolo", "holdfast-agents", "false", ["grok", "--yolo"]),
        ]
        for name, user, push, args in cases:
            with self.subTest(name):
                r, ran = self.run_launcher(user, push, args)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(ran, "", "agent CLI must not start")


@unittest.skipUnless(shutil.which("codex"), "codex CLI not installed")
class CodexRules(unittest.TestCase):
    RULES = ROOT / ".codex/rules/holdfast.rules"

    def decision(self, cmd: str) -> str:
        r = subprocess.run(["codex", "execpolicy", "check", "--rules", str(self.RULES), *cmd.split()],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout).get("decision", "allow")

    def test_forbidden(self) -> None:
        for cmd in ["gh pr merge 1", "gh pr review 1", "gh pr edit 1", "gh issue edit 1", "gh label create x",
                    "gh release create v1", "gh secret set X", "gh ruleset list", "gh repo edit", "gh repo delete x",
                    "gh workflow run x", "gh api -X POST x", "gh api x -f a=b",
                    "git push origin main", "git push origin +main", "git push origin HEAD:main",
                    "git push origin HEAD:refs/heads/main", "git push origin +HEAD:main",
                    "git push origin +HEAD:refs/heads/main", "git push -u origin main", "git push upstream x:main",
                    "git push upstream feat/x", "git push --force", "git push -f", "git push origin --delete main",
                    "git -C /tmp push", "git -c core.hooksPath=/x commit", "git tag v1", "git commit -n",
                    "git commit --no-verify", "git config core.hooksPath /x", "bash -c ls", "sh -c ls", "zsh -c ls",
                    "cat .env", "head .env", "base64 .env"]:
            with self.subTest(cmd):
                self.assertEqual(self.decision(cmd), "forbidden")

    def test_ordinary_work_is_not_forbidden(self) -> None:
        for cmd in ["git status", "gh pr view 1", "gh pr comment 1", "git commit -m x", "git push origin feat/x"]:
            with self.subTest(cmd):
                self.assertNotEqual(self.decision(cmd), "forbidden")


class HarnessFiles(unittest.TestCase):
    def test_keel_is_not_seated_in_grok(self) -> None:
        self.assertFalse((ROOT / ".grok/agents/keel.md").exists())

    def test_no_post_edit_formatting_for_external_agents(self) -> None:
        self.assertFalse((ROOT / ".grok/hooks/format.sh").exists())
        for rel in [".codex/hooks.json", ".grok/hooks/holdfast.json"]:
            hooks = json.loads((ROOT / rel).read_text())["hooks"]
            self.assertNotIn("PostToolUse", hooks, rel)
        self.assertNotIn("format", (ROOT / ".codex/hooks/patch.py").read_text().lower().replace("formatting", ""))

    def test_hooks_fail_closed_wiring(self) -> None:
        grok = json.loads((ROOT / ".grok/hooks/holdfast.json").read_text())["hooks"]["PreToolUse"][0]
        self.assertTrue(grok["hooks"][0]["failClosed"])

    def test_every_seat_carries_the_lane_override(self) -> None:
        sentence = "External lane (AGENTS.md) overrides ROSTER; forbidden paths stay forbidden."
        seats = sorted((ROOT / ".codex/agents").glob("*.toml")) + sorted((ROOT / ".grok/agents").glob("*.md"))
        self.assertGreaterEqual(len(seats), 30)
        for seat in seats:
            with self.subTest(seat.name):
                self.assertIn(sentence, seat.read_text())

    def test_trailers_name_the_external_agent(self) -> None:
        import re

        for directory, agent, pattern in [(".codex/agents", "Anvil", "*.toml"), (".grok/agents", "Helm", "*.md")]:
            seats = sorted((ROOT / directory).glob(pattern))
            with_trailer = 0
            for seat in seats:
                text = seat.read_text()
                self.assertIsNone(re.search(r"Co-Authored-By: (?!%s \(Holdfast\))" % agent, text), seat.name)
                if "Co-Authored-By:" in text:
                    with_trailer += 1
                    self.assertIn(f"Co-Authored-By: {agent} (Holdfast) <agents@holdfastfinance.app>", text, seat.name)
                    self.assertIn(f"`Role: {seat.stem}`", text, seat.name)
            self.assertGreaterEqual(with_trailer, 7)

    def test_reviewer_seats_sign_as_advisory(self) -> None:
        for directory, agent, ext in [(".codex/agents", "Anvil", "toml"), (".grok/agents", "Helm", "md")]:
            for seat, role in [("security-reviewer", "Bulwark"), ("money-invariants-reviewer", "Ledger"),
                               ("tax-config-verifier", "Tally"), ("crypto-custody-auditor", "Cipher"),
                               ("ui-brand-reviewer", "Prism"), ("phase-acceptance-checker", "Sounding")]:
                text = (ROOT / directory / f"{seat}.{ext}").read_text()
                self.assertIn(f"\u2014 {role} via {agent} (advisory)", text, seat)
        rhai = (ROOT / ".grok/workflows/holdfast-review.rhai").read_text()
        self.assertIn("via Helm (advisory)", rhai)

    def test_tide_seats_have_no_live_plaid(self) -> None:
        for rel in [".codex/agents/tide.toml", ".grok/agents/tide.md"]:
            text = (ROOT / rel).read_text()
            self.assertIn("fixture-only", text, rel)
            self.assertNotIn("Plaid sandbox only for a live check", text, rel)

    def test_grok_config_denies_guard_paths_and_github_writes(self) -> None:
        import tomllib

        deny = tomllib.loads((ROOT / ".grok/config.toml").read_text())["permission"]["deny"]
        for needed in ["Edit(**/.claude/**)", "Edit(**/.codex/**)", "Edit(**/.agents/**)", "Edit(**/.grok/**)",
                       "Edit(**/AGENTS.md)", "Edit(**/.githooks/**)", "Bash(gh pr merge*)", "Bash(gh api*)",
                       "Bash(git tag*)", "Bash(bash -c*)", "Bash(git -C*)", "Bash(git -c*)"]:
            self.assertIn(needed, deny)

    def test_codex_profile_marks_config_read_only(self) -> None:
        import tomllib

        fs = tomllib.loads((ROOT / ".codex/config.toml").read_text())["permissions"]["holdfast"]["filesystem"]
        roots = fs[":workspace_roots"]
        for p in [".claude", ".codex", ".agents", ".grok", ".githooks", "CLAUDE.md", "AGENTS.md"]:
            self.assertEqual(roots.get(p), "read", p)


if __name__ == "__main__":
    unittest.main()
