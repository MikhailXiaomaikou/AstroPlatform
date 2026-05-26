#!/usr/bin/env python3
"""PostToolUse hook: flag edits to anti-fabrication red-line source files.

When an Edit/Write touches the zero-fabrication core (synthetic_code_detector
or claim_validator), inject a soft reminder (additionalContext) to run the
regression tests + red-team review. It does NOT block — these files must be
editable; the hook only ensures the safety follow-up isn't forgotten.

Per the Claude Code hook schema, the soft-reminder path is JSON on stdout with
hookSpecificOutput.additionalContext for a PostToolUse event. No deny.
"""
from __future__ import annotations
import json
import sys

REDLINE = (
    "backend/app/services/synthetic_code_detector.py",
    "backend/app/services/claim_validator.py",
)


def silent() -> int:
    return 0


def remind(file_path: str) -> int:
    msg = (
        f"You just edited the anti-fabrication red-line file '{file_path}'. "
        "Before treating this change as done: (1) run the science-test-runner agent, or "
        "`cd backend && ./venv/bin/python3 -m pytest "
        "tests/test_synthetic_code_detector.py tests/test_claim_validator.py "
        "tests/test_abstention_parser.py --no-cov -q`; "
        "(2) consider the anti-fabrication-reviewer agent to red-team for new bypasses."
    )
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        },
        "systemMessage": (
            f"anti-fabrication red line touched: {file_path} "
            "— run regression + red-team before done."
        ),
    }
    print(json.dumps(payload))
    return 0


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return silent()  # never disrupt on parse error

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return silent()

    hit = next((p for p in REDLINE if p in file_path), None)
    if hit is None:
        return silent()

    return remind(file_path)


if __name__ == "__main__":
    sys.exit(main())
