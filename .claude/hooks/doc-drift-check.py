#!/usr/bin/env python3
"""PostToolUse hook: after an edit to CLAUDE.md or ARCHITECTURE.md that writes
digits, feed the live repo stats (scripts/stats.sh) back to the agent so
freshly written counts can be cross-checked on the spot.

Known limit (by design): this catches "agent writes a stale number while
editing the doc". The other drift vector — code changes while the docs sit
untouched — needs a CI-side comparison and is not covered here.
"""
from __future__ import annotations
import subprocess

from _common import REPO_ROOT, edited_path, feedback, read_hook_input, recently, stamp

WATCHED_NAMES = {"claude.md", "architecture.md"}
TIMEOUT_S = 45  # must stay under the harness's 60s default hook timeout
DEBOUNCE_S = 600  # one stats dump per 10 min is plenty for cross-checking


def main() -> int:
    data = read_hook_input()
    path = edited_path(data)
    # APFS is case-insensitive: compare case-folded so a wrong-case path
    # (which still edits the real file on disk) can't bypass the check.
    if path is None or path.name.lower() not in WATCHED_NAMES:
        return 0
    if str(path.parent).lower() != str(REPO_ROOT).lower():
        return 0

    tool_input = data.get("tool_input") or {}
    written = tool_input.get("new_string") or tool_input.get("content") or ""
    if not any(ch.isdigit() for ch in written):
        return 0  # prose-only edit — nothing countable to cross-check

    # Session-scoped debounce: exit-2 feedback lands in ONE session's context,
    # so another concurrent session must not be suppressed by this one's stamp.
    debounce_key = f"doc-drift-stats:{data.get('session_id') or ''}"
    if recently(debounce_key, DEBOUNCE_S):
        return 0

    try:
        r = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "stats.sh")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return 0
    if r.returncode != 0 or not r.stdout.strip():
        return 0  # never present a failed/partial run as authoritative stats

    stamp(debounce_key)
    return feedback(
        "doc-drift-check (informational, not an error): you just edited "
        f"{path.name}. Live repo stats for cross-checking any counts/sizes "
        "you wrote:\n" + r.stdout.strip()
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
