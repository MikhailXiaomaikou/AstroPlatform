#!/usr/bin/env python3
"""PostToolUse hook: run ruff check after editing a .py file under backend/app/.

Mirrors the CI gate exactly (.github/workflows/ci.yml: `ruff check app/
--select E,W,F --ignore E501`) — same flags, same scope. CI does not lint
tests/ or scripts/, so findings there would be noise the gate never enforces;
the hook stays out of them too.
"""
from __future__ import annotations
import subprocess

from _common import REPO_ROOT, edited_path, feedback, notice_debounced, read_hook_input, under

BACKEND_DIR = REPO_ROOT / "backend"
APP_DIR = BACKEND_DIR / "app"
RUFF = BACKEND_DIR / "venv" / "bin" / "ruff"
CI_FLAGS = ["--select", "E,W,F", "--ignore", "E501"]  # keep in sync with .github/workflows/ci.yml
TIMEOUT_S = 30


def main() -> int:
    data = read_hook_input()
    path = edited_path(data)
    if path is None or path.suffix != ".py" or not under(path, APP_DIR):
        return 0

    if not RUFF.exists():
        # A silent no-op would be indistinguishable from "all clean" — say
        # so, but at most once an hour PER SESSION (feedback delivery is
        # session-local, so the stamp must be too). No PATH fallback: a
        # different ruff version would report findings CI does not gate on.
        if notice_debounced(f"ruff-missing:{data.get('session_id') or ''}", 3600):
            return 0
        return feedback(
            "ruff-check: backend/venv/bin/ruff not found — the backend lint "
            "hook is inactive until ruff is installed in the venv."
        )

    try:
        r = subprocess.run(
            [str(RUFF), "check", *CI_FLAGS, str(path)],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return feedback(
            f"ruff-check: timed out after {TIMEOUT_S}s — lint result unknown for {path.name}"
        )

    if r.returncode != 0:
        output = (r.stdout + r.stderr).strip()
        return feedback(f"ruff findings (CI gates on these) after editing {path}:\n{output}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
