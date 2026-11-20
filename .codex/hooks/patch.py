"""Codex PreToolUse adapter. Hands the apply_patch event to the shared scripts/agent_guard.py.

Fail closed: any problem running the guard blocks the patch (exit 2). Formatting is not done here;
CI and `make lint` enforce it.
"""

import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "agent_guard.py"


def main(action):
    if action != "guard":
        print("Holdfast patch guard: unknown action", file=sys.stderr)
        return 2
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(GUARD)],
            stdin=sys.stdin.buffer,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except BaseException:  # noqa: BLE001 - fail closed
        print("Holdfast patch guard: could not run the shared guard", file=sys.stderr)
        return 2
    sys.stderr.buffer.write(result.stderr)
    return 0 if result.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
