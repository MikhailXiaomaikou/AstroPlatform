#!/usr/bin/env python3
"""Run one repository-pinned Foundry candidate and write a DemoReport."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


_CANDIDATE_KEY = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATE_ROOT = (_BACKEND_ROOT / "foundry_candidates").resolve()
_TRUSTED_OUTPUT_ROOT = Path("/trusted-output")
_PR_SET_DUMPABLE = 4


def _candidate_path(key: str) -> Path:
    if not _CANDIDATE_KEY.fullmatch(key):
        raise ValueError("candidate key is invalid")
    path = (_CANDIDATE_ROOT / f"{key}.json").resolve()
    if path.parent != _CANDIDATE_ROOT:
        raise ValueError("candidate path escaped the repository catalog")
    return path


def _write_exclusive(path: Path, payload: bytes) -> None:
    """Create one regular, host-readable artifact without following links."""

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


def _trusted_supervisor_enabled() -> bool:
    return os.getenv("FOUNDRY_TRUSTED_SUPERVISOR") == "1"


def _harden_trusted_supervisor() -> None:
    """Require the official root/PID-1 boundary and hide its descriptors."""

    if os.name != "posix" or os.geteuid() != 0 or os.getpid() != 1:
        raise RuntimeError("trusted_supervisor_root_pid1_required")
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "prctl"):
        raise RuntimeError("trusted_supervisor_prctl_unavailable")
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "trusted_supervisor_dumpability_lock_failed")


def _validate_trusted_output_root(path: Path) -> Path:
    """Accept only an empty root-owned 0700 directory, never a link."""

    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("trusted_output_root_not_directory")
    if metadata.st_uid != 0 or metadata.st_gid != 0:
        raise RuntimeError("trusted_output_root_owner_invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError("trusted_output_root_mode_invalid")
    if any(path.iterdir()):
        raise RuntimeError("trusted_output_root_not_empty")
    return path


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_trusted_result(
    report: dict[str, Any],
    captured_streams: dict[str, bytes],
) -> None:
    """Recheck the report and the actual bytes before root publishes them."""

    from app.services.foundry_evidence_policy import (  # noqa: PLC0415
        contains_formal_claim_escape,
        contains_formal_claim_escape_text,
        demo_report_contract_issue,
    )

    issue = demo_report_contract_issue(report)
    if issue is not None:
        raise RuntimeError(f"candidate_demo_report_invalid:{issue}")
    if contains_formal_claim_escape(report, scan_text_leaves=True):
        raise RuntimeError("candidate_demo_report_formal_escape")
    unsigned = dict(report)
    declared_hash = str(unsigned.pop("demo_report_sha256"))
    if hashlib.sha256(_canonical_json(unsigned)).hexdigest() != declared_hash:
        raise RuntimeError("candidate_demo_report_hash_mismatch")
    if set(captured_streams) != {"stdout.log", "stderr.log"}:
        raise RuntimeError("candidate_demo_stream_set_invalid")
    expected_manifest = []
    for filename, kind in (("stdout.log", "STDOUT"), ("stderr.log", "STDERR")):
        payload = captured_streams[filename]
        if contains_formal_claim_escape_text(payload):
            raise RuntimeError("candidate_demo_stream_formal_escape")
        expected_manifest.append(
            {
                "path": filename,
                "kind": kind,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    if report.get("artifact_manifest") != expected_manifest:
        raise RuntimeError("candidate_demo_artifact_manifest_mismatch")


def _run_candidate(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, bytes]]:
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
    return report, captured_streams


def _publish(
    output_root: Path,
    report: dict[str, Any],
    captured_streams: dict[str, bytes],
    *,
    report_path: Path | None = None,
) -> None:
    for filename in ("stdout.log", "stderr.log"):
        _write_exclusive(output_root / filename, captured_streams[filename])
    _write_exclusive(
        report_path or output_root / "demo-report.json",
        (
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8"),
    )


def _run_legacy_local(args: argparse.Namespace) -> int:
    if os.geteuid() == 0 or args.output is None:
        raise RuntimeError("legacy_output_mode_forbidden")
    output = Path(args.output)
    if not output.name or output.name.casefold() in {"stdout.log", "stderr.log"}:
        raise RuntimeError("legacy_output_path_reserved")
    report, captured_streams = _run_candidate(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    _publish(output.parent, report, captured_streams, report_path=output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output")
    parser.add_argument("--chain-root")
    parser.add_argument("--runner-image-digest")
    parser.add_argument("--candidate-version-sha256")
    args = parser.parse_args()

    if not _trusted_supervisor_enabled():
        return _run_legacy_local(args)
    if args.output is not None:
        raise RuntimeError("trusted_output_argument_forbidden")
    _harden_trusted_supervisor()
    output_root = _validate_trusted_output_root(_TRUSTED_OUTPUT_ROOT)
    report, captured_streams = _run_candidate(args)
    _validate_trusted_result(report, captured_streams)
    _publish(output_root, report, captured_streams)
    print(
        json.dumps(
            {
                "candidate_id": report["candidate_id"],
                "demo_run_id": report["demo_run_id"],
                "status": report["status"],
                "demo_report_sha256": report["demo_report_sha256"],
            },
            sort_keys=True,
        )
    )
    # A contract-valid FAILED report is still a completed validation outcome.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
