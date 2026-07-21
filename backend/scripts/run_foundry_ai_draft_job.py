#!/usr/bin/env python3
"""Generate or finalize a non-executed Foundry candidate draft.

The ``generate`` phase may receive an AI provider credential.  It invokes one
operator-configured adapter with ``shell=False`` and accepts only a bounded
JSON response.  Candidate code is written as an inert patch and is never
applied, imported, tested, or executed.

The ``finalize`` phase runs in a separate job without AI credentials.  It
hashes the inert artifacts and creates the exact callback document accepted by
the control plane.  An unavailable/misconfigured provider produces a
classified FAILED receipt and a non-zero generate exit; success is never
fabricated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GAP_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_DESCRIPTOR_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/@-]{0,127}$")
_DESCRIPTOR_KEYS = frozenset(
    {
        "gap_code",
        "dataset_key",
        "dataset_keys",
        "workflow_key",
        "supported_selection",
        "source_profile_key",
        "claim_schema",
        "claim_type",
        "model",
        "parameter",
        "statistic",
        "evidence_kind",
        "research_domain",
    }
)
_SAFE_PROVIDER = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PATCH_PATHS = (
    re.compile(r"^backend/app/services/foundry_generated/[a-z][a-z0-9_]{2,96}\.py$"),
)
_MAX_PROVIDER_STDOUT = 4 * 1024 * 1024
_MAX_PATCH = 1024 * 1024
_MAX_JSON_ARTIFACT = 1024 * 1024


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


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_canonical(value) + b"\n")


def _safe_descriptor_value(value: Any, *, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if isinstance(value, str):
        return bool(_DESCRIPTOR_TOKEN.fullmatch(value))
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return abs(value) <= 10**12
    if isinstance(value, float):
        return math.isfinite(value) and abs(value) <= 10**12
    if isinstance(value, list):
        return len(value) <= 16 and all(
            _safe_descriptor_value(item, depth=depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 16 and all(
            isinstance(key, str)
            and bool(_DESCRIPTOR_TOKEN.fullmatch(key))
            and _safe_descriptor_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _binding(args: argparse.Namespace) -> dict[str, Any]:
    try:
        draft_run_id = str(uuid.UUID(args.draft_run_id))
        candidate_id = str(uuid.UUID(args.candidate_id))
    except ValueError as exc:
        raise ValueError("draft_identifier_invalid") from exc
    fingerprint = str(args.gap_fingerprint).lower()
    if not _HEX64.fullmatch(fingerprint):
        raise ValueError("gap_fingerprint_invalid")
    if not _GAP_CODE.fullmatch(str(args.gap_code)):
        raise ValueError("gap_code_invalid")
    if args.generation_route not in {"COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"}:
        raise ValueError("generation_route_invalid")
    if args.risk_level not in {"R0", "R1", "R2", "R3"}:
        raise ValueError("risk_level_invalid")
    raw_descriptor = str(args.gap_descriptor)
    try:
        descriptor = json.loads(raw_descriptor)
    except json.JSONDecodeError as exc:
        raise ValueError("gap_descriptor_invalid") from exc
    if (
        not isinstance(descriptor, dict)
        or not {"gap_code", "research_domain"}.issubset(descriptor)
        or not set(descriptor).issubset(_DESCRIPTOR_KEYS)
        or descriptor.get("gap_code") != str(args.gap_code)
        or descriptor.get("research_domain") != "cosmology"
        or any(not _safe_descriptor_value(item) for item in descriptor.values())
        or len(raw_descriptor.encode("utf-8")) > 4096
        or raw_descriptor.encode("utf-8") != _canonical(descriptor)
        or _sha256_json(descriptor) != fingerprint
    ):
        raise ValueError("gap_descriptor_invalid")
    return {
        "draft_run_id": draft_run_id,
        "candidate_id": candidate_id,
        "gap_fingerprint": fingerprint,
        "gap_code": str(args.gap_code),
        "gap_descriptor": descriptor,
        "generation_route": args.generation_route,
        "risk_level": args.risk_level,
    }


def _provider_receipt(
    *, provider: str, model: str, request_id: str | None
) -> dict[str, Any]:
    safe_provider = provider if _SAFE_PROVIDER.fullmatch(provider) else "invalid-provider"
    safe_model = model if _SAFE_PROVIDER.fullmatch(model) else "invalid-model"
    return {
        "contract_version": 1,
        "provider": safe_provider,
        "model": safe_model,
        "request_id_sha256": (
            _sha256_bytes(request_id.encode("utf-8")) if request_id else None
        ),
        "prompt_or_user_data_stored": False,
        "generated_code_executed": False,
        "tests_executed": False,
    }


def _failure_result(binding: dict[str, str], failure_class: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        **binding,
        "status": "FAILED",
        "failure_class": failure_class,
        "provider_receipt": _provider_receipt(
            provider="unavailable", model="unavailable", request_id=None
        ),
    }


def _parse_provider_command() -> list[str]:
    raw = os.getenv("FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON", "").strip()
    if not raw:
        raise ValueError("draft_provider_command_unconfigured")
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("draft_provider_command_invalid") from exc
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 16
        or any(
            not isinstance(part, str) or not part or len(part) > 1024
            for part in command
        )
    ):
        raise ValueError("draft_provider_command_invalid")
    return command


def generate(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        binding = _binding(args)
    except ValueError as exc:
        # Invalid workflow inputs are a workflow configuration failure; no
        # unbound callback must be produced.
        raise SystemExit(str(exc)) from exc
    request = {
        "contract_version": 1,
        "task": "draft_non_formal_foundry_candidate",
        **binding,
        "constraints": {
            "cosmology_only": True,
            "candidate_version": "server_assigned",
            "candidate_code_must_not_execute": True,
            "publication_ready": False,
            "claim_eligible": False,
            "evidence_class": "NON_FORMAL_DEMO",
            "do_not_return_claim_source_prompt_or_user_data": True,
        },
    }
    try:
        command = _parse_provider_command()
        completed = subprocess.run(
            command,
            input=_canonical(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=600,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise ValueError("draft_provider_failed")
        if not completed.stdout or len(completed.stdout) > _MAX_PROVIDER_STDOUT:
            raise ValueError("draft_provider_output_invalid")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("draft_provider_output_invalid") from exc
        if not isinstance(response, dict) or set(response) != {
            "schema_version",
            "candidate_bundle",
            "patch",
            "sbom",
            "provider",
        }:
            raise ValueError("draft_provider_output_invalid")
        if response.get("schema_version") != 1:
            raise ValueError("draft_provider_contract_unsupported")
        candidate_bundle = response.get("candidate_bundle")
        patch = response.get("patch")
        sbom = response.get("sbom")
        provider = response.get("provider")
        if not isinstance(candidate_bundle, dict):
            raise ValueError("draft_candidate_bundle_invalid")
        raw_candidate_version = candidate_bundle.get("candidate_version")
        if raw_candidate_version is not None and raw_candidate_version != 0:
            raise ValueError("draft_candidate_version_must_be_server_assigned")
        generation = candidate_bundle.get("generation")
        if (
            not isinstance(generation, dict)
            or generation.get("prompt_or_claim_stored") is not False
            or generation.get("generated_code_executed_by_draft_job") is not False
        ):
            raise ValueError("draft_generation_metadata_unsafe")
        if not isinstance(patch, str) or len(patch.encode("utf-8")) > _MAX_PATCH:
            raise ValueError("draft_patch_invalid")
        if not isinstance(sbom, (dict, list)) or len(_canonical(sbom)) > _MAX_JSON_ARTIFACT:
            raise ValueError("draft_sbom_invalid")
        if not isinstance(provider, dict) or set(provider) != {
            "provider",
            "model",
            "request_id",
        }:
            raise ValueError("draft_provider_receipt_invalid")
        provider_name = str(provider.get("provider") or "")
        model = str(provider.get("model") or "")
        request_id = provider.get("request_id")
        if (
            not _SAFE_PROVIDER.fullmatch(provider_name)
            or not _SAFE_PROVIDER.fullmatch(model)
            or (request_id is not None and len(str(request_id)) > 512)
        ):
            raise ValueError("draft_provider_receipt_invalid")
        receipt = _provider_receipt(
            provider=provider_name,
            model=model,
            request_id=str(request_id) if request_id else None,
        )
        _write_json(output / "candidate.json", candidate_bundle)
        (output / "candidate.patch").write_text(patch, encoding="utf-8")
        _write_json(output / "sbom.json", sbom)
        _write_json(output / "provider-receipt.json", receipt)
        result = {
            "schema_version": 1,
            **binding,
            "status": "SUCCEEDED",
            "failure_class": None,
            "provider_receipt": receipt,
        }
        _write_json(output / "provider-result.json", result)
        return 0
    except subprocess.TimeoutExpired:
        failure = _failure_result(binding, "draft_provider_timeout")
    except (OSError, ValueError) as exc:
        failure_class = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", failure_class):
            failure_class = "draft_provider_internal_error"
        failure = _failure_result(binding, failure_class)
    _write_json(output / "provider-receipt.json", failure["provider_receipt"])
    _write_json(output / "provider-result.json", failure)
    return 1


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("draft_artifact_too_large")
    return {
        "path": path.name,
        "kind": kind,
        "sha256": _sha256_bytes(data),
        "bytes": len(data),
    }


def _validate_patch_paths(patch: bytes) -> list[str]:
    """Allow only generated Python modules; reject binary/link/CI mutations."""

    if len(patch) > _MAX_PATCH:
        raise ValueError("draft_patch_invalid")
    if not patch:
        return []
    if any(
        marker in patch
        for marker in (
            b"GIT binary patch",
            b"Binary files ",
            b"new file mode 120000",
            b"new file mode 160000",
            b"old mode 120000",
            b"old mode 160000",
            b"Subproject commit ",
        )
    ):
        raise ValueError("draft_patch_unsafe_type")
    forbidden_headers = (
        b"rename from ",
        b"rename to ",
        b"copy from ",
        b"copy to ",
        b"similarity index ",
        b"dissimilarity index ",
    )
    paths: set[str] = set()
    current_path: str | None = None
    saw_old = False
    saw_new = False
    in_hunk = False

    def finish_current() -> None:
        if current_path is not None and (not saw_old or not saw_new):
            raise ValueError("draft_patch_file_header_missing")

    for raw_line in patch.splitlines():
        if any(raw_line.startswith(prefix) for prefix in forbidden_headers):
            raise ValueError("draft_patch_extended_header_forbidden")
        if raw_line.startswith(b"diff --git "):
            finish_current()
            saw_old = False
            saw_new = False
            in_hunk = False
            try:
                marker, option, left, right = raw_line.decode("utf-8").split(" ")
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("draft_patch_path_invalid") from exc
            if (
                marker != "diff"
                or option != "--git"
                or not left.startswith("a/")
                or not right.startswith("b/")
            ):
                raise ValueError("draft_patch_path_invalid")
            left_path = left[2:]
            right_path = right[2:]
            if left_path != right_path:
                raise ValueError("draft_patch_rename_forbidden")
            if not any(pattern.fullmatch(right_path) for pattern in _PATCH_PATHS):
                raise ValueError("draft_patch_path_forbidden")
            current_path = right_path
            paths.add(right_path)
            continue
        if current_path is None:
            raise ValueError("draft_patch_content_before_header")
        if raw_line.startswith(b"@@ "):
            if not saw_old or not saw_new:
                raise ValueError("draft_patch_file_header_missing")
            in_hunk = True
            continue
        if in_hunk:
            continue
        if raw_line.startswith(b"--- "):
            if saw_old:
                raise ValueError("draft_patch_duplicate_file_header")
            old_path = raw_line[4:].decode("utf-8")
            if old_path not in {f"a/{current_path}", "/dev/null"}:
                raise ValueError("draft_patch_old_path_mismatch")
            saw_old = True
        elif raw_line.startswith(b"+++ "):
            if not saw_old or saw_new:
                raise ValueError("draft_patch_duplicate_file_header")
            new_path = raw_line[4:].decode("utf-8")
            # Candidate generation may create or update its one allowlisted
            # module, but may not delete it via +++ /dev/null.
            if new_path != f"b/{current_path}":
                raise ValueError("draft_patch_new_path_mismatch")
            saw_new = True
    finish_current()
    if not paths:
        raise ValueError("draft_patch_missing_diff_header")
    return sorted(paths)


def _apply_inert_patch_and_hash(
    *, source_repo: Path, patch_path: Path
) -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    from app.services.foundry_source_tree import (  # noqa: PLC0415
        assert_clean_checkout,
        git_commit,
        tracked_source_tree_hash,
    )

    source_repo = source_repo.resolve()
    assert_clean_checkout(source_repo)
    base_commit = git_commit(source_repo)
    base_hash, _base_manifest = tracked_source_tree_hash(source_repo)
    patch = patch_path.read_bytes()
    changed_paths = _validate_patch_paths(patch)
    if patch:
        for args in (
            ("apply", "--check", "--index", "--whitespace=error-all", str(patch_path)),
            ("apply", "--index", "--whitespace=error-all", str(patch_path)),
        ):
            completed = subprocess.run(
                ["git", "-C", str(source_repo), *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
            if completed.returncode != 0:
                raise ValueError("draft_patch_apply_failed")
    assert_clean_checkout(source_repo, allow_staged_changes=bool(patch))
    post_hash, post_manifest = tracked_source_tree_hash(source_repo)
    lock = (source_repo / "backend" / "requirements.lock").read_bytes()
    runner_definition = (
        source_repo / "backend" / "Dockerfile.foundry-demo"
    ).read_bytes()
    return {
        "hash_algorithm": post_manifest["schema"],
        "base_commit": base_commit,
        "base_source_tree_sha256": base_hash,
        "post_patch_source_tree_sha256": post_hash,
        "patch_sha256": _sha256_bytes(patch),
        "patch_applied": bool(patch),
        "changed_paths": changed_paths,
        "dependency_lock_sha256": _sha256_bytes(lock),
        "runner_definition_sha256": _sha256_bytes(runner_definition),
    }


def materialize(args: argparse.Namespace) -> int:
    """Apply an inert allowlisted patch to the disposable trusted checkout."""

    input_dir = Path(args.input_dir)
    result = json.loads((input_dir / "provider-result.json").read_text())
    binding = _binding(args)
    if any(result.get(key) != value for key, value in binding.items()):
        raise ValueError("draft_provider_result_binding_mismatch")
    if result.get("status") != "SUCCEEDED":
        return 2
    receipt = _apply_inert_patch_and_hash(
        source_repo=Path(args.source_repo),
        patch_path=input_dir / "candidate.patch",
    )
    _write_json(Path(args.output), receipt)
    return 0


def finalize(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    result_path = input_dir / "provider-result.json"
    if not result_path.is_file() or result_path.stat().st_size > 1024 * 1024:
        raise SystemExit("draft_provider_result_missing")
    try:
        provider_result = json.loads(result_path.read_text(encoding="utf-8"))
        binding = _binding(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("draft_provider_result_invalid") from exc
    if not isinstance(provider_result, dict) or set(provider_result) != {
        "schema_version",
        "draft_run_id",
        "candidate_id",
        "gap_fingerprint",
        "gap_code",
        "gap_descriptor",
        "generation_route",
        "risk_level",
        "status",
        "failure_class",
        "provider_receipt",
    }:
        raise SystemExit("draft_provider_result_invalid")
    if any(provider_result.get(key) != value for key, value in binding.items()):
        raise SystemExit("draft_provider_result_binding_mismatch")
    status = provider_result.get("status")
    receipt_path = input_dir / "provider-receipt.json"
    artifact_manifest = [_artifact(receipt_path, "PROVIDER_RECEIPT")]
    candidate_version = None
    source_receipt = None
    artifact_receipt = None
    if status == "SUCCEEDED":
        digest = str(args.validation_runner_image_digest or "").lower()
        if not _IMAGE_DIGEST.fullmatch(digest):
            raise SystemExit("validation_runner_image_digest_invalid")
        candidate_path = input_dir / "candidate.json"
        patch_path = input_dir / "candidate.patch"
        sbom_path = input_dir / "sbom.json"
        try:
            bundle = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("draft_candidate_artifact_invalid") from exc
        artifact_manifest.extend(
            [
                _artifact(candidate_path, "CANDIDATE_BUNDLE"),
                _artifact(patch_path, "PATCH"),
                _artifact(sbom_path, "SBOM"),
            ]
        )
        by_name = {item["path"]: item for item in artifact_manifest}
        try:
            source_receipt = json.loads(
                Path(args.source_receipt).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("draft_source_receipt_missing") from exc
        if source_receipt["patch_sha256"] != by_name["candidate.patch"]["sha256"]:
            raise SystemExit("draft_patch_hash_mismatch")
        generation = bundle.get("generation")
        if not isinstance(generation, dict):
            raise SystemExit("draft_generation_metadata_unsafe")
        generation.update(
            {
                "source_hash_algorithm": source_receipt["hash_algorithm"],
                "source_base_commit": source_receipt["base_commit"],
                "source_base_tree_sha256": source_receipt[
                    "base_source_tree_sha256"
                ],
                "source_tree_sha256": source_receipt[
                    "post_patch_source_tree_sha256"
                ],
                "source_materialization_required": source_receipt[
                    "patch_applied"
                ],
            }
        )
        bundle["generation"] = generation
        bundle["dependency_lock_sha256"] = source_receipt[
            "dependency_lock_sha256"
        ]
        bundle["runner_definition_sha256"] = source_receipt[
            "runner_definition_sha256"
        ]
        repository = str(args.artifact_repository or "")
        artifact_name = str(args.artifact_name or "")
        artifact_digest = str(args.artifact_digest or "").lower()
        if artifact_digest.startswith("sha256:"):
            artifact_digest = artifact_digest[7:]
        if (
            not _REPOSITORY.fullmatch(repository)
            or not str(args.workflow_run_id).isdigit()
            or not str(args.artifact_id).isdigit()
            or not _ARTIFACT_NAME.fullmatch(artifact_name)
            or not _HEX64.fullmatch(artifact_digest)
        ):
            raise SystemExit("draft_artifact_receipt_invalid")
        artifact_receipt = {
            "repository": repository,
            "workflow_run_id": str(args.workflow_run_id),
            "artifact_id": str(args.artifact_id),
            "artifact_name": artifact_name,
            "artifact_sha256": artifact_digest,
        }
        candidate_version = {
            "candidate_bundle": bundle,
            "validation_runner_image_digest": digest,
            "code_tree_hash": source_receipt["post_patch_source_tree_sha256"],
            "patch_hash": by_name["candidate.patch"]["sha256"],
            "sbom_hash": by_name["sbom.json"]["sha256"],
        }
    elif status != "FAILED":
        raise SystemExit("draft_provider_status_invalid")
    report = {
        "schema_version": 1,
        **binding,
        "status": status,
        "candidate_version": candidate_version,
        "provider_receipt": provider_result["provider_receipt"],
        "artifact_manifest": artifact_manifest,
        "artifact_receipt": artifact_receipt,
        "source_receipt": source_receipt,
        "failure_class": provider_result["failure_class"],
    }
    report["draft_result_sha256"] = _sha256_json(report)
    _write_json(Path(args.output), report)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "materialize", "finalize"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--draft-run-id", required=True)
        sub.add_argument("--candidate-id", required=True)
        sub.add_argument("--gap-fingerprint", required=True)
        sub.add_argument("--gap-code", required=True)
        sub.add_argument("--gap-descriptor", required=True)
        sub.add_argument(
            "--generation-route",
            required=True,
            choices=("COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"),
        )
        sub.add_argument(
            "--risk-level", required=True, choices=("R0", "R1", "R2", "R3")
        )
    generate_parser = subparsers.choices["generate"]
    generate_parser.add_argument("--output-dir", required=True)
    materialize_parser = subparsers.choices["materialize"]
    materialize_parser.add_argument("--input-dir", required=True)
    materialize_parser.add_argument("--source-repo", required=True)
    materialize_parser.add_argument("--output", required=True)
    finalize_parser = subparsers.choices["finalize"]
    finalize_parser.add_argument("--input-dir", required=True)
    finalize_parser.add_argument("--output", required=True)
    finalize_parser.add_argument("--validation-runner-image-digest", required=True)
    finalize_parser.add_argument("--source-repo", required=True)
    finalize_parser.add_argument("--source-receipt", required=True)
    finalize_parser.add_argument("--artifact-repository", required=True)
    finalize_parser.add_argument("--workflow-run-id", required=True)
    finalize_parser.add_argument("--artifact-id", required=True)
    finalize_parser.add_argument("--artifact-name", required=True)
    finalize_parser.add_argument("--artifact-digest", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "generate":
        return generate(args)
    if args.command == "materialize":
        return materialize(args)
    return finalize(args)


if __name__ == "__main__":
    sys.exit(main())
