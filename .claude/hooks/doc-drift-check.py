#!/usr/bin/env python3
"""PostToolUse hook: after editing CLAUDE.md or ARCHITECTURE.md, feed the live
repo stats (scripts/stats.sh) back to the agent so freshly written counts can
be cross-checked on the spot.

Motivation: the 2026-06-10 audit found a dozen stale facts (tool counts,
prompt size, test counts) in these two files — they are loaded as overriding
instructions, so stale numbers actively mislead agents. This only catches the
countable subset; facts like env-flag defaults still need manual care.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WATCHED = {REPO_ROOT / "CLAUDE.md", REPO_ROOT / "ARCHITECTURE.md"}
TIMEOUT_S = 120


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return 0
    try:
        if Path(file_path).resolve() not in WATCHED:
            return 0
    except OSError:
        return 0

    try:
        r = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "stats.sh")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0

    # Exit 2: PostToolUse feeds stderr back to the agent. This is
    # informational, not an error — compare the numbers you just wrote
    # against the live ones below and fix any mismatch.
    print(
        "doc-drift-check (informational, not an error): you just edited "
        f"{Path(file_path).name}. Live repo stats for cross-checking any "
        "counts/sizes you wrote:\n" + r.stdout.strip(),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
