#!/usr/bin/env python3
"""Run or verify the public, explicitly non-formal Foundry candidate demo.

The historical receipt in ``docs/demo/foundry-candidate`` and a replay from a
current checkout are deliberately different objects.  A replay computes a new
CandidateVersion identity from the checked-out Git tree, the actual runtime
descriptor, the dependency lock, and the installed-distribution inventory.  It
never copies the historical version hash into a new report.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


_CANDIDATE_KEY = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_FILES = (
    "demo-report.json",
    "replay-identity.json",
    "stdout.log",
    "stderr.log",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"recorded_json_duplicate_key:{key}")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _hash_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _installed_distributions() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            continue
        name = re.sub(r"[-_.]+", "-", str(raw_name)).lower()
        rows.append({"name": name, "version": str(distribution.version)})
    return sorted(rows, key=lambda row: (row["name"], row["version"]))


def _write_exclusive(path: Path, payload: bytes) -> None:
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


def _candidate_path(backend_root: Path, key: str) -> Path:
    if not _CANDIDATE_KEY.fullmatch(key):
        raise ValueError("candidate key is invalid")
    root = (backend_root / "foundry_candidates").resolve()
    path = (root / f"{key}.json").resolve()
    if path.parent != root:
        raise ValueError("candidate path escaped the repository catalog")
    return path


def _runtime_identity(
    *,
    repo_root: Path,
    backend_root: Path,
    bundle: dict[str, Any],
    candidate_path: Path,
) -> dict[str, Any]:
    from app.services.foundry_candidate_identity import (  # noqa: PLC0415
        candidate_version_envelope,
        candidate_version_sha256,
    )
    from app.services.foundry_source_tree import (  # noqa: PLC0415
        git_commit,
        tracked_source_tree_hash,
    )

    commit = git_commit(repo_root)
    source_tree_sha256, _source_manifest = tracked_source_tree_hash(repo_root)

    requirements_lock = backend_root / "requirements.lock"
    dockerfile = backend_root / "Dockerfile.foundry-demo"
    dependency_lock_sha256 = _hash_file(requirements_lock)
    runner_definition_sha256 = _hash_file(dockerfile)
    if dependency_lock_sha256 != bundle["dependency_lock_sha256"]:
        raise ValueError("candidate_dependency_lock_sha256_mismatch")
    if runner_definition_sha256 != bundle["runner_definition_sha256"]:
        raise ValueError("candidate_runner_definition_sha256_mismatch")

    runtime_paths = (
        backend_root / "scripts" / "run_public_foundry_candidate_replay.py",
        backend_root / "scripts" / "run_foundry_candidate_demo.py",
        backend_root / "app" / "services" / "foundry_demo_runner.py",
        backend_root / "app" / "services" / "foundry_evidence_policy.py",
        backend_root / "app" / "services" / "foundry_candidate_identity.py",
        backend_root
        / "app"
        / "services"
        / "cosmology_likelihoods"
        / "analysis_registry.py",
        backend_root
        / "app"
        / "services"
        / "cosmology_likelihoods"
        / "dark_energy_matrix.py",
        backend_root
        / "app"
        / "services"
        / "cosmology_likelihoods"
        / "config_builder.py",
        requirements_lock,
        dockerfile,
        candidate_path,
    )
    runtime_files = {
        path.relative_to(repo_root).as_posix(): _hash_file(path)
        for path in runtime_paths
    }
    python_binary = Path(sys.executable).resolve()
    distributions = _installed_distributions()
    environment_sbom = {
        "schema_version": 1,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "distributions": distributions,
    }
    sbom_sha256 = _sha256_json(environment_sbom)
    runner_descriptor = {
        "schema_version": 1,
        "mode": "local_python_non_formal_replay",
        "git_commit": commit,
        "tracked_source_tree_sha256": source_tree_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
        "runner_definition_sha256": runner_definition_sha256,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "binary_sha256": _hash_file(python_binary),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
        },
        "environment_sbom_sha256": sbom_sha256,
        "runtime_files": runtime_files,
        "digest_kind": "LOCAL_DESCRIPTOR_SHA256",
        "environment_closure": "DESCRIPTOR_ONLY",
        "warning": (
            "This local descriptor digest is not a signed OCI image digest "
            "and cannot be promoted."
        ),
    }
    runner_image_digest = f"sha256:{_sha256_json(runner_descriptor)}"

    candidate_bundle_sha256 = _sha256_json(bundle)
    workflow_spec_sha256 = _sha256_json(bundle["workflow_spec"])
    data_hashes = {
        str(item["key"]): str(item["sha256"])
        for item in bundle.get("source_pins") or []
        if isinstance(item, dict)
        and item.get("key")
        and _HEX64.fullmatch(str(item.get("sha256") or ""))
    }
    patch_sha256 = _sha256_bytes(b"")
    envelope = candidate_version_envelope(
        candidate_bundle_sha256=candidate_bundle_sha256,
        workflow_spec_sha256=workflow_spec_sha256,
        code_tree_sha256=source_tree_sha256,
        patch_sha256=patch_sha256,
        dependency_lock_sha256=dependency_lock_sha256,
        sbom_sha256=sbom_sha256,
        fixture_hashes=list(bundle.get("fixture_hashes") or []),
        data_hashes=data_hashes,
        validation_runner_image_digest=runner_image_digest,
    )
    candidate_version_hash = candidate_version_sha256(**{
        key: value
        for key, value in envelope.items()
        if key != "schema_version"
    })
    identity = {
        "schema_version": 1,
        "identity_kind": "CURRENT_CHECKOUT_NON_FORMAL_REPLAY",
        "candidate_version_envelope": envelope,
        "candidate_version_sha256": candidate_version_hash,
        "runner_descriptor": runner_descriptor,
        "runner_image_digest": runner_image_digest,
        "runner_digest_kind": "LOCAL_DESCRIPTOR_SHA256",
        "environment_closure": "DESCRIPTOR_ONLY",
        "environment_sbom": environment_sbom,
        "historical_demo_version_reused": False,
        "ledger_recorded": False,
        "formal_registry_eligible": False,
    }
    identity["replay_identity_sha256"] = _sha256_json(identity)
    return identity


def _validate_report(
    report: dict[str, Any],
    *,
    bundle: dict[str, Any],
    identity: dict[str, Any],
) -> None:
    expected = identity["candidate_version_envelope"]

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"replay_report_invalid:{message}")

    require(report.get("evidence_class") == "NON_FORMAL_DEMO", "evidence_class")
    require(report.get("publication_ready") is False, "publication_ready")
    require(report.get("claim_eligible") is False, "claim_eligible")
    require(report.get("evidence_pack_allowed") is False, "evidence_pack_allowed")
    require(
        report.get("candidate_bundle_sha256")
        == expected["candidate_bundle_sha256"],
        "candidate_bundle_sha256",
    )
    require(
        report.get("candidate_version_sha256")
        == identity["candidate_version_sha256"],
        "candidate_version_sha256",
    )
    require(
        report.get("workflow_spec_sha256") == expected["workflow_spec_sha256"],
        "workflow_spec_sha256",
    )
    require(
        report.get("dependency_lock_sha256")
        == expected["dependency_lock_sha256"],
        "dependency_lock_sha256",
    )
    require(
        report.get("runner_definition_sha256")
        == bundle["runner_definition_sha256"],
        "runner_definition_sha256",
    )
    require(
        report.get("runner_image_digest") == identity["runner_image_digest"],
        "runner_image_digest",
    )
    require(
        report.get("environment", {}).get("tool_version")
        == identity["runner_descriptor"]["git_commit"],
        "tool_version",
    )
    require(
        _sha256_json(report.get("environment"))
        == report.get("environment_sha256"),
        "environment_sha256",
    )
    limitations = report.get("limitations")
    require(isinstance(limitations, list) and bool(limitations), "limitations")
    summary = report.get("validation_summary")
    require(isinstance(summary, dict), "validation_summary")
    failure_escape_marker = (
        report.get("failure_class") == "candidate_formal_claim_escape_blocked"
    )
    summary_escape_marker = summary.get("formal_claim_escape_blocked") is True
    require(
        failure_escape_marker == summary_escape_marker,
        "formal_claim_escape_marker",
    )
    if failure_escape_marker:
        require(report.get("status") == "FAILED", "formal_claim_escape_status")
        require(report.get("result") == {}, "formal_claim_escape_result_erasure")
    require(
        summary.get("numeric_claim_gate") == "NON_FORMAL_DEMO"
        or report.get("status") == "FAILED",
        "numeric_claim_gate",
    )
    declared_hash = str(report.get("demo_report_sha256") or "")
    unsigned = dict(report)
    unsigned.pop("demo_report_sha256", None)
    require(_HEX64.fullmatch(declared_hash) is not None, "demo_report_sha256")
    require(_sha256_json(unsigned) == declared_hash, "demo_report_self_hash")

    status = report.get("status")
    if status == "PARTIAL":
        require(
            report.get("failure_class") == "official_chain_mirror_unavailable",
            "partial_failure_class",
        )
        require(summary.get("registry_integrity") is True, "registry_integrity")
        require(
            summary.get("official_mirror_configured") is False,
            "partial_mirror_configuration",
        )
        require(summary.get("official_mirror_verified") is False, "partial_mirror")
        require(summary.get("ready_cells") == 0, "partial_ready_cells")
        require(
            isinstance(summary.get("withheld_cells"), int)
            and summary["withheld_cells"] > 0,
            "partial_withheld_cells",
        )
    elif status == "PASSED":
        require(report.get("failure_class") is None, "passed_failure_class")
        require(summary.get("registry_integrity") is True, "registry_integrity")
        require(summary.get("official_mirror_configured") is True, "passed_mirror")
        require(summary.get("official_mirror_verified") is True, "passed_mirror")
        require(
            isinstance(summary.get("ready_cells"), int)
            and summary["ready_cells"] > 0,
            "passed_ready_cells",
        )
        require(summary.get("withheld_cells") == 0, "passed_withheld_cells")
    elif status == "FAILED":
        if report.get("failure_class") == "official_chain_mirror_integrity_failed":
            require(
                summary.get("official_mirror_configured") is True,
                "failed_mirror_configuration",
            )
            require(
                summary.get("official_mirror_verified") is False,
                "failed_mirror_verification",
            )
            require(summary.get("ready_cells") == 0, "failed_ready_cells")
            require(
                isinstance(summary.get("withheld_cells"), int)
                and summary["withheld_cells"] > 0,
                "failed_withheld_cells",
            )
            require(bool(summary.get("withheld_reasons")), "failed_withheld_reasons")
    else:
        raise ValueError(f"replay_report_invalid:unexpected_status:{status!r}")


def run_replay(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    backend_root = repo_root / "backend"

    sys.path.insert(0, str(backend_root))
    from app.services.foundry_source_tree import (  # noqa: PLC0415
        assert_clean_checkout,
    )

    assert_clean_checkout(repo_root)
    from app.services.foundry_demo_runner import (  # noqa: PLC0415
        load_candidate_bundle,
        run_candidate_demo,
    )

    candidate_path = _candidate_path(backend_root, args.candidate)
    bundle = load_candidate_bundle(candidate_path)
    identity = _runtime_identity(
        repo_root=repo_root,
        backend_root=backend_root,
        bundle=bundle,
        candidate_path=candidate_path,
    )

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in _OUTPUT_FILES:
        if (output_dir / filename).exists():
            raise ValueError(f"replay_output_exists:{output_dir / filename}")

    captured_streams: dict[str, bytes] = {}
    prior_tool_version = os.environ.get("TOOL_VERSION")
    os.environ["TOOL_VERSION"] = identity["runner_descriptor"]["git_commit"]
    try:
        report = run_candidate_demo(
            bundle,
            cache_root=args.chain_root,
            runner_image_digest=identity["runner_image_digest"],
            candidate_version_sha256=identity["candidate_version_sha256"],
            captured_streams=captured_streams,
        )
    finally:
        if prior_tool_version is None:
            os.environ.pop("TOOL_VERSION", None)
        else:
            os.environ["TOOL_VERSION"] = prior_tool_version

    _validate_report(report, bundle=bundle, identity=identity)
    identity_bytes = (
        json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    report_bytes = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    _write_exclusive(output_dir / "replay-identity.json", identity_bytes)
    _write_exclusive(output_dir / "stdout.log", captured_streams["stdout.log"])
    _write_exclusive(output_dir / "stderr.log", captured_streams["stderr.log"])
    _write_exclusive(output_dir / "demo-report.json", report_bytes)

    formal_claim_escape_blocked = (
        report.get("failure_class") == "candidate_formal_claim_escape_blocked"
    )
    print(json.dumps({
        "candidate_id": report["candidate_id"],
        "candidate_version_sha256": report["candidate_version_sha256"],
        "demo_report_sha256": report["demo_report_sha256"],
        "demo_run_id": report["demo_run_id"],
        "failure_class": report.get("failure_class"),
        "formal_claim_escape_blocked": formal_claim_escape_blocked,
        "historical_demo_version_reused": False,
        "ledger_recorded": False,
        "replay_identity_sha256": identity["replay_identity_sha256"],
        "runner_digest_kind": identity["runner_digest_kind"],
        "runner_image_digest": report["runner_image_digest"],
        "status": report["status"],
    }, sort_keys=True))
    return 0 if report["status"] in {"PASSED", "PARTIAL"} else 1


def _verify_sha256sums(kit_dir: Path) -> None:
    manifest = kit_dir / "SHA256SUMS"
    kit_paths = list(kit_dir.rglob("*"))
    if any(path.is_symlink() for path in kit_paths):
        raise ValueError("recorded_sha256sums_symlink_forbidden")
    seen: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not _HEX64.fullmatch(digest):
            raise ValueError("recorded_sha256sums_invalid")
        if relative in seen or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("recorded_sha256sums_path_invalid")
        seen.add(relative)
        target = kit_dir / relative
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"recorded_sha256sums_target_invalid:{relative}")
        if _hash_file(target) != digest:
            raise ValueError(f"recorded_file_sha256_mismatch:{relative}")
    expected = {
        path.relative_to(kit_dir).as_posix()
        for path in kit_paths
        if path.is_file() and path != manifest
    }
    if seen != expected:
        raise ValueError("recorded_sha256sums_coverage_mismatch")


def verify_recorded(args: argparse.Namespace) -> int:
    kit_dir = Path(args.kit_dir).resolve()
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    from app.services.foundry_candidate_identity import (  # noqa: PLC0415
        candidate_version_sha256,
        canonical_json,
    )
    from app.services.foundry_evidence_policy import (  # noqa: PLC0415
        contains_formal_claim_escape,
    )

    _verify_sha256sums(kit_dir)
    report = _read_json(kit_dir / "demo-report.sanitized.json")
    ledger = _read_json(kit_dir / "ledger-summary.sanitized.json")
    candidate_bundle = _read_json(kit_dir / "candidate-bundle.json")
    version_receipt = _read_json(kit_dir / "candidate-version-envelope.json")
    runner_receipt = _read_json(kit_dir / "runner-descriptor.json")
    event_receipt = _read_json(kit_dir / "ledger-events.json")

    declared_report_hash = str(report.get("demo_report_sha256") or "")
    unsigned_report = dict(report)
    unsigned_report.pop("demo_report_sha256", None)
    if _sha256_json(unsigned_report) != declared_report_hash:
        raise ValueError("recorded_demo_report_self_hash_mismatch")
    if _sha256_json(report.get("environment")) != report.get("environment_sha256"):
        raise ValueError("recorded_environment_hash_mismatch")
    if (
        report.get("evidence_class") != "NON_FORMAL_DEMO"
        or report.get("publication_ready") is not False
        or report.get("claim_eligible") is not False
        or report.get("evidence_pack_allowed") is not False
        or contains_formal_claim_escape(report.get("result"))
        or contains_formal_claim_escape(report.get("validation_summary"))
    ):
        raise ValueError("recorded_demo_report_scope_invalid")

    envelope = version_receipt.get("envelope")
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise ValueError("recorded_candidate_version_envelope_invalid")
    if (
        version_receipt.get("identity_kind")
        != "HISTORICAL_BOOTSTRAP_CANDIDATE_VERSION"
        or version_receipt.get("environment_closure")
        != "BOOTSTRAP_DESCRIPTOR_ONLY"
        or version_receipt.get("historical_provenance_complete") is not False
        or version_receipt.get("formal_registry_eligible") is not False
    ):
        raise ValueError("recorded_candidate_version_scope_invalid")
    envelope_kwargs = dict(envelope)
    envelope_kwargs.pop("schema_version")
    recomputed_version = candidate_version_sha256(**envelope_kwargs)
    if (
        recomputed_version != version_receipt.get("candidate_version_sha256")
        or recomputed_version != report.get("candidate_version_sha256")
    ):
        raise ValueError("recorded_candidate_version_hash_mismatch")
    if (
        _sha256_json(candidate_bundle) != envelope.get("candidate_bundle_sha256")
        or _sha256_json(candidate_bundle.get("workflow_spec"))
        != envelope.get("workflow_spec_sha256")
        or candidate_bundle.get("dependency_lock_sha256")
        != envelope.get("dependency_lock_sha256")
        or candidate_bundle.get("fixture_hashes") != envelope.get("fixture_hashes")
        or {
            str(item.get("key")): str(item.get("sha256"))
            for item in candidate_bundle.get("source_pins") or []
            if isinstance(item, dict) and item.get("key") and item.get("sha256")
        }
        != envelope.get("data_hashes")
        or candidate_bundle.get("output_policy")
        != {
            "evidence_class": "NON_FORMAL_DEMO",
            "publication_ready": False,
            "claim_eligible": False,
            "evidence_pack_allowed": False,
        }
    ):
        raise ValueError("recorded_candidate_bundle_hash_mismatch")
    if (
        envelope.get("patch_sha256")
        != _sha256_bytes(b"repository-pinned-candidate-no-patch")
        or envelope.get("sbom_sha256")
        != _sha256_bytes(b"local-demo-sbom-not-generated")
    ):
        raise ValueError("recorded_bootstrap_placeholder_hash_mismatch")
    for report_key, envelope_key in (
        ("candidate_bundle_sha256", "candidate_bundle_sha256"),
        ("workflow_spec_sha256", "workflow_spec_sha256"),
        ("dependency_lock_sha256", "dependency_lock_sha256"),
        ("runner_image_digest", "validation_runner_image_digest"),
    ):
        if report.get(report_key) != envelope.get(envelope_key):
            raise ValueError(f"recorded_candidate_version_link_mismatch:{report_key}")
    if report.get("runner_definition_sha256") != candidate_bundle.get(
        "runner_definition_sha256"
    ):
        raise ValueError("recorded_runner_definition_link_mismatch")

    descriptor = runner_receipt.get("descriptor")
    if not isinstance(descriptor, dict):
        raise ValueError("recorded_runner_descriptor_missing")
    descriptor_sha256 = hashlib.sha256(canonical_json(descriptor)).hexdigest()
    expected_runner_digest = f"sha256:{descriptor_sha256}"
    if (
        runner_receipt.get("descriptor_sha256") != descriptor_sha256
        or runner_receipt.get("validation_runner_image_digest")
        != expected_runner_digest
        or report.get("runner_image_digest") != expected_runner_digest
        or runner_receipt.get("is_signed_oci_image_digest") is not False
        or runner_receipt.get("formal_registry_eligible") is not False
        or runner_receipt.get("digest_kind") != "LOCAL_DESCRIPTOR_SHA256"
        or runner_receipt.get("environment_closure") != "DESCRIPTOR_ONLY"
        or descriptor.get("git_commit")
        != report.get("environment", {}).get("tool_version")
        or descriptor.get("python")
        != report.get("environment", {}).get("python_version")
        or ledger.get("local_runner_descriptor") != descriptor
    ):
        raise ValueError("recorded_runner_descriptor_hash_mismatch")

    events = event_receipt.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("recorded_event_chain_missing")
    if (
        event_receipt.get("scope") != "DISPOSABLE_LOCAL_NON_PRODUCTION_DEMO"
        or event_receipt.get("environment_closure")
        != "BOOTSTRAP_DESCRIPTOR_ONLY"
        or event_receipt.get("historical_provenance_complete") is not False
        or event_receipt.get("formal_registry_eligible") is not False
    ):
        raise ValueError("recorded_event_chain_scope_invalid")
    previous_hash: str | None = None
    candidate_id: str | None = None
    for index, event in enumerate(events):
        if not isinstance(event, dict) or not isinstance(event.get("envelope"), dict):
            raise ValueError(f"recorded_event_envelope_invalid:{index}")
        event_envelope = event["envelope"]
        event_hash = hashlib.sha256(canonical_json(event_envelope)).hexdigest()
        if event_hash != event.get("event_hash"):
            raise ValueError(f"recorded_event_hash_mismatch:{index}")
        if event_envelope.get("previous_event_hash") != previous_hash:
            raise ValueError(f"recorded_event_previous_hash_mismatch:{index}")
        if candidate_id is None:
            candidate_id = event_envelope.get("candidate_id")
        elif event_envelope.get("candidate_id") != candidate_id:
            raise ValueError(f"recorded_event_candidate_mismatch:{index}")
        previous_hash = event_hash

    version_events = [
        event["envelope"]
        for event in events
        if event["envelope"].get("event_type") == "CANDIDATE_VERSION_CREATED"
    ]
    if len(version_events) != 1:
        raise ValueError("recorded_candidate_version_event_missing")
    version_payload = version_events[0].get("payload")
    if (
        not isinstance(version_payload, dict)
        or version_payload.get("version_hash") != recomputed_version
        or version_payload.get("candidate_bundle_hash")
        != envelope.get("candidate_bundle_sha256")
        or version_payload.get("workflow_id")
        != candidate_bundle.get("proposed_workflow_id")
    ):
        raise ValueError("recorded_candidate_version_event_mismatch")

    final_event = events[-1]
    final_envelope = final_event["envelope"]
    final_payload = final_envelope.get("payload")
    if (
        final_envelope.get("event_type") != "DEMO_RECORDED"
        or not isinstance(final_payload, dict)
        or final_payload.get("demo_run_id") != report.get("demo_run_id")
        or final_payload.get("demo_report_sha256") != declared_report_hash
        or final_payload.get("status") != report.get("status")
        or final_payload.get("evidence_class") != report.get("evidence_class")
        or final_payload.get("publication_ready")
        is not report.get("publication_ready")
        or final_payload.get("claim_eligible") is not report.get("claim_eligible")
        or final_payload.get("evidence_pack_allowed")
        is not report.get("evidence_pack_allowed")
    ):
        raise ValueError("recorded_final_event_report_mismatch")

    event_summary = ledger.get("event_chain")
    if not isinstance(event_summary, list) or len(event_summary) != len(events):
        raise ValueError("recorded_event_summary_mismatch")
    for summary, event in zip(event_summary, events, strict=True):
        envelope = event["envelope"]
        if summary != {
            "event_hash": event["event_hash"],
            "event_type": envelope["event_type"],
            "previous_event_hash": envelope["previous_event_hash"],
        }:
            raise ValueError("recorded_event_summary_mismatch")
    demo = ledger.get("demo")
    if not isinstance(demo, dict):
        raise ValueError("recorded_demo_ledger_missing")
    if (
        demo.get("demo_run_id") != report["demo_run_id"]
        or demo.get("demo_report_sha256") != declared_report_hash
        or demo.get("status") != report.get("status")
        or demo.get("failure_class") != report.get("failure_class")
        or demo.get("evidence_class") != report.get("evidence_class")
        or demo.get("publication_ready") is not report.get("publication_ready")
        or demo.get("claim_eligible") is not report.get("claim_eligible")
        or demo.get("evidence_pack_allowed")
        is not report.get("evidence_pack_allowed")
        or demo.get("validation_run_id") != final_payload.get("validation_run_id")
        or ledger.get("candidate", {}).get("candidate_version_hash")
        != report["candidate_version_sha256"]
        or final_envelope.get("event_type") != "DEMO_RECORDED"
    ):
        raise ValueError("recorded_demo_ledger_link_mismatch")

    print(json.dumps({
        "candidate_version_sha256": report["candidate_version_sha256"],
        "demo_report_sha256": declared_report_hash,
        "demo_run_id": report["demo_run_id"],
        "evidence_class": report["evidence_class"],
        "event_chain_head": previous_hash,
        "environment_closure": version_receipt.get("environment_closure"),
        "historical_provenance_complete": False,
        "status": report["status"],
        "verified": True,
    }, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--repo-root", required=True)
    run.add_argument("--candidate", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--chain-root")
    run.set_defaults(handler=run_replay)

    recorded = subparsers.add_parser("verify-recorded")
    recorded.add_argument("--kit-dir", required=True)
    recorded.set_defaults(handler=verify_recorded)

    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Foundry public replay failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
