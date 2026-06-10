#!/usr/bin/env python3
"""PostToolUse hook: run ruff check after editing a .py file under backend/.

Backend symmetry for tsc-check.py — CI gates on the same ruff check, but a
push can be days after the edit; this surfaces lint findings at edit time.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
RUFF = BACKEND_DIR / "venv" / "bin" / "ruff"
TIMEOUT_S = 30


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py"):
        return 0
    if "backend/" not in file_path or "venv/" in file_path:
        return 0

    ruff = str(RUFF) if RUFF.exists() else "ruff"
    try:
        r = subprocess.run(
            [ruff, "check", file_path],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"ruff-check: timed out after {TIMEOUT_S}s — skipping", file=sys.stderr)
        return 0
    except FileNotFoundError:
        # ruff missing — skip silently
        return 0

    if r.returncode != 0:
        # Exit 2: PostToolUse feeds stderr back to the agent (exit 0 stderr
        # only reaches the transcript, not the model).
        output = (r.stdout + r.stderr).strip()
        print(f"ruff findings after editing {file_path}:\n{output}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
