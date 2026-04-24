#!/usr/bin/env python3
"""Replay a local AI diagnostic bundle without network calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python backend/scripts/replay_diagnostic_bundle.py .local/diagnostics/<bundle>.json")
        return 2
    backend_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_dir))
    from app.services.local_diagnostics import replay_diagnostic_bundle

    result = replay_diagnostic_bundle(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

