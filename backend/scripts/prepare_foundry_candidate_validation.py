#!/usr/bin/env python3
"""Prepare an exact AI Draft artifact for isolated Candidate Validation.

This host step verifies the immutable GitHub artifact, safely extracts it,
replays the inert allowlisted patch on the pinned base commit, recomputes the
canonical source tree, and reconstructs the server-assigned candidate version.
It never imports or executes candidate code.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CANDIDATE_KEY = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_EXPECTED_FILES = {
    "candidate.json",
    "candidate.patch",
    "sbom.json",
    "provider-receipt.json",
    "provider-result.json",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical(value))


def _binding(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("draft_binding_invalid") from exc
    required = {
        "candidate_id",
        "candidate_version_id",
        "candidate_version_number",
        "candidate_version_hash",
        "candidate_bundle_hash",
        "candidate_artifact_hash",
        "draft_run_id",
        "artifact_id",
        "artifact_workflow_run_id",
        "artifact_name",
        "artifact_sha256",
        "artifact_repository",
        "base_commit",
        "base_source_tree_sha256",
        "post_patch_source_tree_sha256",
        "patch_sha256",
        "sbom_sha256",
        "validation_runner_image_digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("draft_binding_shape_invalid")
    normalized = {key: str(item) for key, item in value.items()}
    if (
        any(
            not _UUID.fullmatch(normalized[field])
            for field in ("candidate_id", "candidate_version_id", "draft_run_id")
        )
        or not normalized["candidate_version_number"].isdigit()
        or int(normalized["candidate_version_number"]) < 1
        or not normalized["artifact_id"].isdigit()
        or not normalized["artifact_workflow_run_id"].isdigit()
        or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", normalized["artifact_name"]
        )
        or not _REPOSITORY.fullmatch(normalized["artifact_repository"])
        or not re.fullmatch(r"[0-9a-f]{40}", normalized["base_commit"])
        or any(
            not _HEX64.fullmatch(normalized[field])
            for field in (
                "candidate_version_hash",
                "candidate_bundle_hash",
                "candidate_artifact_hash",
                "artifact_sha256",
                "base_source_tree_sha256",
                "post_patch_source_tree_sha256",
                "patch_sha256",
                "sbom_sha256",
            )
        )
        or not _IMAGE_DIGEST.fullmatch(
            normalized["validation_runner_image_digest"]
        )
    ):
        raise ValueError("draft_binding_invalid")
    return normalized


def _version_binding(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("version_binding_invalid") from exc
    required = {
        "candidate_id",
        "candidate_version_id",
        "candidate_key",
        "candidate_version_number",
        "candidate_version_hash",
        "candidate_bundle_hash",
        "validation_runner_image_digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("version_binding_shape_invalid")
    normalized = {key: str(item) for key, item in value.items()}
    if (
        any(
            not _UUID.fullmatch(normalized[field])
            for field in ("candidate_id", "candidate_version_id")
        )
        or not _CANDIDATE_KEY.fullmatch(normalized["candidate_key"])
        or not normalized["candidate_version_number"].isdigit()
        or int(normalized["candidate_version_number"]) < 1
        or not _HEX64.fullmatch(normalized["candidate_version_hash"])
        or not _HEX64.fullmatch(normalized["candidate_bundle_hash"])
        or not _IMAGE_DIGEST.fullmatch(
            normalized["validation_runner_image_digest"]
        )
    ):
        raise ValueError("version_binding_invalid")
    return normalized


def emit_outputs(args: argparse.Namespace) -> int:
    binding = _binding(args.binding_json)
    version = _version_binding(args.version_binding_json)
    if binding["artifact_repository"] != args.expected_repository:
        raise ValueError("draft_artifact_repository_mismatch")
    for field in (
        "candidate_id",
        "candidate_version_id",
        "candidate_version_number",
        "candidate_version_hash",
        "candidate_bundle_hash",
        "validation_runner_image_digest",
    ):
        if binding[field] != version[field]:
            raise ValueError("draft_version_binding_mismatch")
    lines = {
        "artifact_id": binding["artifact_id"],
        "artifact_workflow_run_id": binding["artifact_workflow_run_id"],
        "artifact_name": binding["artifact_name"],
        "artifact_sha256": binding["artifact_sha256"],
        "base_commit": binding["base_commit"],
        "runner_image_digest": binding["validation_runner_image_digest"],
        "candidate_version_hash": binding["candidate_version_hash"],
    }
    with Path(args.github_output).open("a", encoding="utf-8") as output:
        for key, value in lines.items():
            output.write(f"{key}={value}\n")
    return 0


def emit_version_outputs(args: argparse.Namespace) -> int:
    binding = _version_binding(args.version_binding_json)
    if binding["candidate_key"] != args.candidate_key:
        raise ValueError("version_candidate_key_mismatch")
    lines = {
        "candidate_version_hash": binding["candidate_version_hash"],
        "candidate_bundle_hash": binding["candidate_bundle_hash"],
        "runner_image_digest": binding["validation_runner_image_digest"],
    }
    with Path(args.github_output).open("a", encoding="utf-8") as output:
        for key, value in lines.items():
            output.write(f"{key}={value}\n")
    return 0


def _safe_extract(archive: Path, target: Path) -> None:
    if archive.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("draft_artifact_archive_too_large")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        members = [member for member in handle.infolist() if not member.is_dir()]
        if len(members) != len(_EXPECTED_FILES):
            raise ValueError("draft_artifact_file_set_invalid")
        names: set[str] = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            mode = (member.external_attr >> 16) & 0o170000
            if (
                len(path.parts) != 1
                or path.name not in _EXPECTED_FILES
                or path.name in names
                or mode == 0o120000
            ):
                raise ValueError("draft_artifact_path_invalid")
            total += member.file_size
            if member.file_size > 4 * 1024 * 1024 or total > 8 * 1024 * 1024:
                raise ValueError("draft_artifact_contents_too_large")
            data = handle.read(member)
            if len(data) != member.file_size:
                raise ValueError("draft_artifact_size_mismatch")
            (target / path.name).write_bytes(data)
            names.add(path.name)
        if names != _EXPECTED_FILES:
            raise ValueError("draft_artifact_file_set_invalid")


def prepare(args: argparse.Namespace) -> int:
    binding = _binding(args.binding_json)
    candidate_key = str(args.candidate_key)
    if not _CANDIDATE_KEY.fullmatch(candidate_key):
        raise ValueError("candidate_key_invalid")
    archive = Path(args.artifact_zip)
    archive_hash = _sha256_bytes(archive.read_bytes())
    if archive_hash != binding["artifact_sha256"]:
        raise ValueError("draft_artifact_archive_hash_mismatch")
    extracted = Path(args.extracted_dir)
    _safe_extract(archive, extracted)
    candidate_bytes = (extracted / "candidate.json").read_bytes()
    patch_bytes = (extracted / "candidate.patch").read_bytes()
    sbom_bytes = (extracted / "sbom.json").read_bytes()
    if (
        _sha256_bytes(candidate_bytes) != binding["candidate_artifact_hash"]
        or _sha256_bytes(patch_bytes) != binding["patch_sha256"]
        or _sha256_bytes(sbom_bytes) != binding["sbom_sha256"]
    ):
        raise ValueError("draft_artifact_file_hash_mismatch")

    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    from scripts.run_foundry_ai_draft_job import (  # noqa: PLC0415
        _apply_inert_patch_and_hash,
    )

    source = _apply_inert_patch_and_hash(
        source_repo=Path(args.source_repo),
        patch_path=extracted / "candidate.patch",
    )
    if (
        source["base_commit"] != binding["base_commit"]
        or source["base_source_tree_sha256"]
        != binding["base_source_tree_sha256"]
        or source["post_patch_source_tree_sha256"]
        != binding["post_patch_source_tree_sha256"]
        or source["patch_sha256"] != binding["patch_sha256"]
    ):
        raise ValueError("draft_source_replay_mismatch")
    try:
        bundle = json.loads(candidate_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("draft_candidate_bundle_invalid") from exc
    raw_version = bundle.get("candidate_version") if isinstance(bundle, dict) else None
    if not isinstance(bundle, dict) or (
        raw_version is not None and raw_version != 0
    ):
        raise ValueError("draft_candidate_bundle_invalid")
    if bundle.get("candidate_id") != candidate_key:
        raise ValueError("draft_candidate_key_mismatch")
    bundle["candidate_version"] = int(binding["candidate_version_number"])
    generation = bundle.get("generation")
    if not isinstance(generation, dict):
        raise ValueError("draft_generation_metadata_invalid")
    generation.update(
        {
            "source_hash_algorithm": source["hash_algorithm"],
            "source_base_commit": source["base_commit"],
            "source_base_tree_sha256": source["base_source_tree_sha256"],
            "source_tree_sha256": source["post_patch_source_tree_sha256"],
            "source_materialization_required": source["patch_applied"],
        }
    )
    bundle["generation"] = generation
    bundle["dependency_lock_sha256"] = source["dependency_lock_sha256"]
    bundle["runner_definition_sha256"] = source["runner_definition_sha256"]

    if patch_bytes:
        if bundle.get("entrypoint_id") != "candidate_generated_python_demo_v1":
            raise ValueError("draft_generated_entrypoint_required")
        expected_module = (
            f"backend/app/services/foundry_generated/{candidate_key}.py"
        )
        if source["changed_paths"] != [expected_module]:
            raise ValueError("draft_generated_module_binding_mismatch")
        module_source = (
            Path(args.source_repo) / expected_module
        ).read_text(encoding="utf-8")
        tree = ast.parse(module_source)
        if not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_demo"
            for node in tree.body
        ):
            raise ValueError("draft_generated_run_demo_missing")

    from app.services.foundry_demo_runner import (  # noqa: PLC0415
        validate_candidate_bundle,
    )
    from app.services.foundry_candidate_identity import (  # noqa: PLC0415
        candidate_version_sha256,
    )

    normalized = validate_candidate_bundle(bundle)
    if _sha256_json(normalized) != binding["candidate_bundle_hash"]:
        raise ValueError("draft_candidate_bundle_hash_mismatch")
    workflow_spec_hash = _sha256_json(normalized["workflow_spec"])
    data_hashes = {
        str(item.get("key")): str(item.get("sha256"))
        for item in normalized.get("source_pins") or []
        if isinstance(item, dict)
        and item.get("key")
        and _HEX64.fullmatch(str(item.get("sha256") or ""))
    }
    version_hash = candidate_version_sha256(
        candidate_bundle_sha256=binding["candidate_bundle_hash"],
        workflow_spec_sha256=workflow_spec_hash,
        code_tree_sha256=source["post_patch_source_tree_sha256"],
        patch_sha256=binding["patch_sha256"],
        dependency_lock_sha256=normalized["dependency_lock_sha256"],
        sbom_sha256=binding["sbom_sha256"],
        fixture_hashes=list(normalized.get("fixture_hashes") or []),
        data_hashes=data_hashes,
        validation_runner_image_digest=binding[
            "validation_runner_image_digest"
        ],
    )
    if version_hash != binding["candidate_version_hash"]:
        raise ValueError("draft_candidate_version_hash_mismatch")
    Path(args.output_candidate).write_text(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "candidate_id": binding["candidate_id"],
        "candidate_version_id": binding["candidate_version_id"],
        "candidate_version_sha256": binding["candidate_version_hash"],
        "candidate_bundle_sha256": binding["candidate_bundle_hash"],
        "artifact_sha256": binding["artifact_sha256"],
        "source_tree_sha256": source["post_patch_source_tree_sha256"],
        "runner_image_digest": binding["validation_runner_image_digest"],
        "candidate_code_executed": False,
    }
    Path(args.output_receipt).write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    emit = subparsers.add_parser("emit-outputs")
    emit.add_argument("--binding-json", required=True)
    emit.add_argument("--version-binding-json", required=True)
    emit.add_argument("--expected-repository", required=True)
    emit.add_argument("--github-output", required=True)
    emit_version = subparsers.add_parser("emit-version-outputs")
    emit_version.add_argument("--version-binding-json", required=True)
    emit_version.add_argument("--candidate-key", required=True)
    emit_version.add_argument("--github-output", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--binding-json", required=True)
    prepare_parser.add_argument("--candidate-key", required=True)
    prepare_parser.add_argument("--artifact-zip", required=True)
    prepare_parser.add_argument("--extracted-dir", required=True)
    prepare_parser.add_argument("--source-repo", required=True)
    prepare_parser.add_argument("--output-candidate", required=True)
    prepare_parser.add_argument("--output-receipt", required=True)
    args = parser.parse_args()
    if args.command == "emit-outputs":
        return emit_outputs(args)
    if args.command == "emit-version-outputs":
        return emit_version_outputs(args)
    return prepare(args)


if __name__ == "__main__":
    raise SystemExit(main())
