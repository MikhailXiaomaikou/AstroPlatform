#!/usr/bin/env python3
"""PostToolUse hook: run tsc -b after editing a .ts/.tsx file under frontend/src/.

verbatimModuleSyntax + noUnusedLocals catch most issues at compile time;
we want them flagged before the user pushes (CI gates on the same check).
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
TIMEOUT_S = 120


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not (file_path.endswith(".ts") or file_path.endswith(".tsx")):
        return 0
    if "frontend/src/" not in file_path:
        return 0

    try:
        r = subprocess.run(
            ["npx", "tsc", "-b", "--pretty", "false"],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"tsc-check: timed out after {TIMEOUT_S}s — skipping", file=sys.stderr)
        return 0
    except FileNotFoundError:
        # npx missing — skip silently
        return 0

    if r.returncode != 0:
        # Exit 2: PostToolUse feeds stderr back to the agent. (Exit 0 stderr
        # only reaches the transcript — the old `return 0` here meant the
        # agent never actually saw these errors.)
        output = (r.stdout + r.stderr).strip()
        print(f"tsc errors after editing {file_path}:\n{output}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
