#!/usr/bin/env python3
"""Run one repository-pinned Foundry candidate and write a DemoReport."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


_CANDIDATE_KEY = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATE_ROOT = (_BACKEND_ROOT / "foundry_candidates").resolve()


def _candidate_path(key: str) -> Path:
    if not _CANDIDATE_KEY.fullmatch(key):
        raise ValueError("candidate key is invalid")
    path = (_CANDIDATE_ROOT / f"{key}.json").resolve()
    if path.parent != _CANDIDATE_ROOT:
        raise ValueError("candidate path escaped the repository catalog")
    return path


def _write_exclusive(path: Path, payload: bytes) -> None:
    """Create one regular, host-readable artifact without following links.

    The container runs as uid 10002 while the GitHub host uses a different
    uid.  A 0600 bind-mounted file cannot be inspected or uploaded by the
    post-container host steps, so publish only the completed immutable file as
    0644 after its bytes have been flushed.  Candidate-created paths still
    fail closed through O_EXCL/O_NOFOLLOW.
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--chain-root")
    parser.add_argument("--runner-image-digest")
    parser.add_argument("--candidate-version-sha256")
    args = parser.parse_args()

    sys.path.insert(0, str(_BACKEND_ROOT))
    from app.services.foundry_demo_runner import (  # noqa: PLC0415
        load_candidate_bundle,
        run_candidate_demo,
    )

    bundle = load_candidate_bundle(_candidate_path(args.candidate))
    captured_streams: dict[str, bytes] = {}
    report = run_candidate_demo(
        bundle,
        cache_root=args.chain_root,
        runner_image_digest=args.runner_image_digest,
        candidate_version_sha256=args.candidate_version_sha256,
        captured_streams=captured_streams,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    for artifact in report["artifact_manifest"]:
        artifact_path = output.parent / artifact["path"]
        _write_exclusive(artifact_path, captured_streams[artifact["path"]])
    _write_exclusive(
        output,
        (
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8"),
    )
    print(json.dumps({
        "candidate_id": report["candidate_id"],
        "demo_run_id": report["demo_run_id"],
        "status": report["status"],
        "demo_report_sha256": report["demo_report_sha256"],
    }, sort_keys=True))
    return 0 if report["status"] in {"PASSED", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
