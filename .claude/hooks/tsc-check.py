#!/usr/bin/env python3
"""PostToolUse hook: run tsc -b after editing a .ts/.tsx file under
frontend/src/, and report what matters through the error channel:

- diagnostics IN the edited file -> exit 2 (full text, with continuation
  lines), because the edit itself is broken;
- error-count INCREASE with no diagnostics in the edited file -> exit 2
  (summary), because an interface change likely broke its consumers —
  TypeScript reports that breakage at the consumer files, not the edited one;
- a STABLE count of errors elsewhere (pre-existing legacy errors, or the
  middle of a multi-file refactor the agent is already working through) ->
  transcript note only, so one legacy error can't make every future edit
  "fail" forever.

The previous count is kept in a tempdir state file (repo-global on purpose:
the project's error count is repo state, not session state).
"""
from __future__ import annotations
import re
import subprocess
import sys

from _common import REPO_ROOT, edited_path, feedback, read_hook_input, read_state, under, write_state

FRONTEND_DIR = REPO_ROOT / "frontend"
SRC_DIR = FRONTEND_DIR / "src"
TIMEOUT_S = 50  # must stay under the harness's 60s default hook timeout
BASELINE_KEY = "tsc-error-baseline"
# `--pretty false` diagnostic header: "src/foo.tsx(12,5): error TS2339: ..."
_DIAG_RE = re.compile(r"\S.*\(\d+,\d+\): (?:error|warning) TS\d+")


def main() -> int:
    path = edited_path(read_hook_input())
    if path is None or path.suffix not in (".ts", ".tsx") or not under(path, SRC_DIR):
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
        return feedback(f"tsc-check: timed out after {TIMEOUT_S}s — type-check result unknown")
    except FileNotFoundError:
        # npx missing — skip silently
        return 0

    if r.returncode == 0:
        write_state(BASELINE_KEY, "0")
        return 0

    # Group output into per-diagnostic blocks so indented continuation lines
    # (nested type-mismatch detail) stay attached to their header.
    blocks: list[list[str]] = []
    for ln in (r.stdout + r.stderr).strip().splitlines():
        if _DIAG_RE.match(ln):
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)

    # Same-length prefix slice works whatever the path's case; matching is
    # case-folded because tsc prints the on-disk case (APFS).
    rel = str(path)[len(str(FRONTEND_DIR)) + 1 :]
    mine = [b for b in blocks if b[0].lower().startswith(rel.lower() + "(")]
    others = len(blocks) - len(mine)

    prev_raw = read_state(BASELINE_KEY)
    prev = int(prev_raw) if prev_raw and prev_raw.isdigit() else 0
    write_state(BASELINE_KEY, str(len(blocks)))

    if mine:
        msg = f"tsc errors in {rel} after your edit:\n" + "\n".join("\n".join(b) for b in mine)
        if others:
            msg += f"\n(+ {others} error(s) in other files — `npm run build` for the full list)"
        return feedback(msg)

    if len(blocks) > prev:
        sample = "\n".join(b[0] for b in blocks[:5])
        return feedback(
            f"tsc -b: project error count rose {prev} -> {len(blocks)} right after your edit to "
            f"{rel}, all in OTHER files — the edit likely broke its consumers:\n{sample}"
        )

    print(f"tsc-check: {len(blocks)} pre-existing error(s) elsewhere, none in {rel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
