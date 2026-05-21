#!/usr/bin/env python3
"""PreToolUse hook: block Edit/Write under protected paths.

Vendored deps (venv / node_modules) and pinned scientific data
(Pantheon+ 1701-SN release) must never be edited by an agent.

Per the Claude Code hook schema, PreToolUse blocking is done by writing
JSON to stdout with hookSpecificOutput.permissionDecision = "deny".
The legacy exit-code-2 path is deprecated for PreToolUse.
"""
from __future__ import annotations
import json
import os
import sys

PROTECTED = (
    "backend/data/pantheon_plus_2022/",
    "backend/data/line_catalogs/",
    "backend/venv/",
    "backend/.venv/",
    "frontend/node_modules/",
    "frontend/dist/",
)


def allow() -> int:
    return 0


def deny(file_path: str, hit: str) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{file_path} is under protected prefix '{hit}'. "
                "These are vendored deps or pinned scientific data — "
                "refusing edit. If you really need this, edit it manually outside Claude."
            ),
        },
        "systemMessage": (
            f"BLOCKED: {file_path} under {hit} (protected path)."
        ),
    }
    print(json.dumps(payload))
    return 0  # exit 0; control flow via JSON, not exit code


def main() -> int:
    if os.getenv("CLAUDE_HOOK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from datetime import datetime
            with open("/tmp/claude-hook-debug.log", "a") as fh:
                fh.write(f"{datetime.now().isoformat()} hook invoked\n")
        except Exception:
            pass

    try:
        data = json.load(sys.stdin)
    except Exception:
        return allow()  # don't block on parse errors

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return allow()

    hit = next((p for p in PROTECTED if p in file_path), None)
    if hit is None:
        return allow()

    return deny(file_path, hit)


if __name__ == "__main__":
    sys.exit(main())
