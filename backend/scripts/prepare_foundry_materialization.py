#!/usr/bin/env python3
"""Trusted host utilities for materializing an inert allowlisted Draft patch.

The script hashes and compares source.  It never imports or executes a
generated Candidate module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_MODULE = re.compile(r"^backend/app/services/foundry_generated/[a-z][a-z0-9_]{2,96}\.py$")
_ORIGIN_MAIN_REF = "refs/remotes/origin/main"
_EXPECTED_ARTIFACT_FILES = {
    "candidate.json", "candidate.patch", "sbom.json", "provider-receipt.json", "provider-result.json"
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(raw: str, *, schema: str, required: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("materialization_binding_invalid") from exc
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != schema:
        raise ValueError("materialization_binding_shape_invalid")
    return value


_PR_REQUEST_FIELDS = {
    "schema_version", "materialization_request_id", "candidate_id", "origin_candidate_version_id",
    "origin_candidate_version_hash", "candidate_key", "candidate_module_path", "draft_run_id",
    "artifact_repository", "artifact_workflow_run_id", "artifact_id", "artifact_name",
    "artifact_sha256", "base_commit", "base_source_tree_sha256", "post_patch_source_tree_sha256",
    "patch_sha256", "candidate_bundle_sha256", "branch_name", "auto_merge_allowed",
    "candidate_code_execution_allowed",
}
_FINAL_REQUEST_FIELDS = {
    "schema_version", "candidate_id", "origin_candidate_version_id", "origin_candidate_version_hash",
    "materialization_attestation_id", "materialization_attestation_receipt_sha256",
    "materialization_request_id", "draft_run_id", "artifact_repository", "artifact_workflow_run_id",
    "artifact_id", "artifact_name", "artifact_sha256", "base_commit", "base_source_tree_sha256",
    "post_patch_source_tree_sha256", "patch_sha256", "candidate_module_path",
    "candidate_module_sha256", "branch_name", "pull_request_number", "pull_request_head_commit",
    "pull_request_head_tree_sha256", "candidate_code_execution_allowed",
}


def _validate_common(value: dict[str, Any]) -> None:
    for key in ("candidate_id", "origin_candidate_version_id", "materialization_request_id", "draft_run_id"):
        if not _UUID.fullmatch(str(value.get(key) or "")):
            raise ValueError("materialization_uuid_invalid")
    for key in (
        "origin_candidate_version_hash", "artifact_sha256", "base_source_tree_sha256",
        "post_patch_source_tree_sha256", "patch_sha256",
    ):
        if not _HEX64.fullmatch(str(value.get(key) or "")):
            raise ValueError("materialization_hash_invalid")
    if (
        not _REPOSITORY.fullmatch(str(value.get("artifact_repository") or ""))
        or not str(value.get("artifact_workflow_run_id") or "").isdigit()
        or not str(value.get("artifact_id") or "").isdigit()
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(value.get("artifact_name") or ""))
        or not _GIT_SHA.fullmatch(str(value.get("base_commit") or ""))
        or not _MODULE.fullmatch(str(value.get("candidate_module_path") or ""))
        or value.get("candidate_code_execution_allowed") is not False
    ):
        raise ValueError("materialization_binding_invalid")


def parse_pr_binding(raw: str) -> dict[str, Any]:
    value = _json(raw, schema="standard_astro_materialization_request_v1", required=_PR_REQUEST_FIELDS)
    _validate_common(value)
    if (
        not _HEX64.fullmatch(str(value.get("candidate_bundle_sha256") or ""))
        or value.get("auto_merge_allowed") is not False
    ):
        raise ValueError("materialization_binding_invalid")
    if not re.fullmatch(r"foundry/materialize-[0-9a-f]{12}-v[1-9][0-9]*-[0-9a-f]{12}", str(value.get("branch_name") or "")):
        raise ValueError("materialization_branch_invalid")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,96}", str(value.get("candidate_key") or "")):
        raise ValueError("materialization_candidate_key_invalid")
    return value


def parse_final_binding(raw: str) -> dict[str, Any]:
    value = _json(
        raw,
        schema="standard_astro_materialization_finalization_request_v1",
        required=_FINAL_REQUEST_FIELDS,
    )
    _validate_common(value)
    if (
        not _UUID.fullmatch(str(value.get("materialization_attestation_id") or ""))
        or not _HEX64.fullmatch(str(value.get("materialization_attestation_receipt_sha256") or ""))
        or not _HEX64.fullmatch(str(value.get("candidate_module_sha256") or ""))
        or not _GIT_SHA.fullmatch(str(value.get("pull_request_head_commit") or ""))
        or not _HEX64.fullmatch(str(value.get("pull_request_head_tree_sha256") or ""))
        or type(value.get("pull_request_number")) is not int
        or not 1 <= value["pull_request_number"] <= 2_147_483_647
    ):
        raise ValueError("materialization_final_binding_invalid")
    return value


def _pull_request_head_repository(pull_request: dict[str, Any]) -> str:
    repository = pull_request.get("headRepository")
    if isinstance(repository, dict):
        full_name = str(repository.get("nameWithOwner") or "")
        if full_name:
            return full_name
        owner = pull_request.get("headRepositoryOwner")
        owner_name = str(owner.get("login") or "") if isinstance(owner, dict) else ""
        repository_name = str(repository.get("name") or "")
        if owner_name and repository_name:
            return f"{owner_name}/{repository_name}"
    return str(repository or "")


def _verify_pull_request_merge_identity(
    pull_request: dict[str, Any],
    *,
    binding: dict[str, Any],
    expected_repository: str,
    expected_merge_commit: str,
) -> dict[str, Any]:
    repository = str(expected_repository or "")
    merge_value = pull_request.get("mergeCommit")
    merge_commit = str(
        merge_value.get("oid") if isinstance(merge_value, dict) else ""
    ).lower()
    base_ref = str(pull_request.get("baseRefName") or "")
    head_repository = _pull_request_head_repository(pull_request)
    if (
        not _REPOSITORY.fullmatch(repository)
        or pull_request.get("number") != binding["pull_request_number"]
        or pull_request.get("state") != "MERGED"
        or pull_request.get("headRefOid") != binding["pull_request_head_commit"]
        or base_ref != "main"
        or head_repository != repository
        or not _GIT_SHA.fullmatch(merge_commit)
        or merge_commit != str(expected_merge_commit).lower()
    ):
        raise ValueError("materialization_merge_identity_invalid")
    return {
        "merge_commit": merge_commit,
        "pull_request_base_ref": base_ref,
        "pull_request_head_repository": head_repository,
    }


def _git_revision(repository: Path, revision: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", revision],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("materialization_protected_main_invalid") from exc
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not _GIT_SHA.fullmatch(value):
        raise ValueError("materialization_protected_main_invalid")
    return value


def _verify_protected_main_ancestry(
    repository: Path,
    *,
    merge_commit: str,
    protected_main_commit: str,
) -> str:
    expected = str(protected_main_commit or "").lower()
    if not _GIT_SHA.fullmatch(expected):
        raise ValueError("materialization_protected_main_invalid")
    if (
        _git_revision(repository, "HEAD") != expected
        or _git_revision(repository, _ORIGIN_MAIN_REF) != expected
    ):
        raise ValueError("materialization_protected_main_invalid")
    try:
        ancestry = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                merge_commit,
                _ORIGIN_MAIN_REF,
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("materialization_protected_main_invalid") from exc
    if ancestry.returncode != 0:
        raise ValueError("materialization_merge_not_on_protected_main")
    return expected


def emit(args: argparse.Namespace, *, final: bool) -> int:
    binding = parse_final_binding(args.binding) if final else parse_pr_binding(args.binding)
    if binding["artifact_repository"] != args.repository or str(args.request_id) != str(
        binding.get("materialization_request_id")
        if not final else binding["materialization_attestation_id"]
    ):
        raise ValueError("materialization_dispatch_binding_mismatch")
    fields = {
        "artifact_id": binding["artifact_id"],
        "artifact_workflow_run_id": binding["artifact_workflow_run_id"],
        "artifact_name": binding["artifact_name"],
        "artifact_sha256": binding["artifact_sha256"],
        "base_commit": binding["base_commit"],
        "branch_name": binding["branch_name"],
        "candidate_module_path": binding["candidate_module_path"],
    }
    if final:
        fields.update(
            {
                "pull_request_number": str(binding["pull_request_number"]),
                "pull_request_head_commit": binding["pull_request_head_commit"],
            }
        )
    with Path(args.github_output).open("a", encoding="utf-8") as output:
        for key, value in fields.items():
            output.write(f"{key}={value}\n")
    return 0


def _extract(archive: Path, target: Path) -> None:
    if archive.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("materialization_artifact_too_large")
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as handle:
        files = [item for item in handle.infolist() if not item.is_dir()]
        if len(files) != len(_EXPECTED_ARTIFACT_FILES):
            raise ValueError("materialization_artifact_file_set_invalid")
        total = 0
        names: set[str] = set()
        for member in files:
            path = PurePosixPath(member.filename)
            mode = (member.external_attr >> 16) & 0o170000
            if len(path.parts) != 1 or path.name not in _EXPECTED_ARTIFACT_FILES or path.name in names or mode == 0o120000:
                raise ValueError("materialization_artifact_path_invalid")
            if member.file_size > 4 * 1024 * 1024:
                raise ValueError("materialization_artifact_file_too_large")
            total += member.file_size
            if total > 8 * 1024 * 1024:
                raise ValueError("materialization_artifact_contents_too_large")
            payload = handle.read(member)
            if len(payload) != member.file_size:
                raise ValueError("materialization_artifact_size_mismatch")
            (target / path.name).write_bytes(payload)
            names.add(path.name)


def prepare_pr(args: argparse.Namespace) -> int:
    binding = parse_pr_binding(args.binding)
    archive = Path(args.artifact_zip)
    if _sha(archive) != binding["artifact_sha256"]:
        raise ValueError("materialization_artifact_hash_mismatch")
    extracted = Path(args.extracted_dir)
    _extract(archive, extracted)
    if _sha(extracted / "candidate.patch") != binding["patch_sha256"]:
        raise ValueError("materialization_patch_hash_mismatch")
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from scripts.run_foundry_ai_draft_job import _apply_inert_patch_and_hash  # noqa: PLC0415

    source = _apply_inert_patch_and_hash(
        source_repo=Path(args.source_repo), patch_path=extracted / "candidate.patch"
    )
    if (
        source["base_commit"] != binding["base_commit"]
        or source["base_source_tree_sha256"] != binding["base_source_tree_sha256"]
        or source["post_patch_source_tree_sha256"] != binding["post_patch_source_tree_sha256"]
        or source["patch_sha256"] != binding["patch_sha256"]
        or source["changed_paths"] != [binding["candidate_module_path"]]
    ):
        raise ValueError("materialization_source_replay_mismatch")
    module = Path(args.source_repo) / binding["candidate_module_path"]
    output = {
        "schema_version": "standard_astro_materialization_prepared_pr_v1",
        "binding_sha256": hashlib.sha256(_canonical(binding)).hexdigest(),
        "candidate_module_sha256": _sha(module),
        "candidate_code_executed": False,
        **binding,
    }
    Path(args.output).write_bytes(_canonical(output) + b"\n")
    return 0


def make_pr_payload(args: argparse.Namespace) -> int:
    prepared = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
    binding = parse_pr_binding(json.dumps({key: prepared[key] for key in _PR_REQUEST_FIELDS}))
    pr = json.loads(Path(args.pull_request_json).read_text(encoding="utf-8"))
    number = pr.get("number")
    head = str(pr.get("headRefOid") or "").lower()
    if (
        type(number) is not int
        or pr.get("url") != f"https://github.com/{args.repository}/pull/{number}"
        or pr.get("headRefName") != binding["branch_name"]
        or not _GIT_SHA.fullmatch(head)
        or head != str(args.head_commit).lower()
        or pr.get("state") not in {"OPEN", "MERGED"}
        or (pr.get("state") == "OPEN" and pr.get("isDraft") is not True)
    ):
        raise ValueError("materialization_pull_request_invalid")
    payload = {
        "schema_version": "standard_astro_materialization_pr_v1",
        "attestation_id": str(args.attestation_id),
        "materialization_request_id": binding["materialization_request_id"],
        "candidate_id": binding["candidate_id"],
        "origin_candidate_version_id": binding["origin_candidate_version_id"],
        "origin_candidate_version_hash": binding["origin_candidate_version_hash"],
        "draft_run_id": binding["draft_run_id"],
        "artifact_repository": binding["artifact_repository"],
        "artifact_workflow_run_id": binding["artifact_workflow_run_id"],
        "artifact_id": binding["artifact_id"],
        "artifact_name": binding["artifact_name"],
        "artifact_sha256": binding["artifact_sha256"],
        "base_commit": binding["base_commit"],
        "base_source_tree_sha256": binding["base_source_tree_sha256"],
        "post_patch_source_tree_sha256": binding["post_patch_source_tree_sha256"],
        "patch_sha256": binding["patch_sha256"],
        "candidate_module_path": binding["candidate_module_path"],
        "candidate_module_sha256": prepared["candidate_module_sha256"],
        "branch_name": binding["branch_name"],
        "pull_request_number": number,
        "pull_request_state": pr["state"],
        "pull_request_url": pr["url"],
        "pull_request_head_commit": head,
        "pull_request_head_tree_sha256": binding["post_patch_source_tree_sha256"],
        "github_repository": args.repository,
        "github_workflow_ref": args.workflow_ref,
        "github_workflow_sha": args.workflow_sha,
        "github_run_id": str(args.run_id),
        "github_run_attempt": int(args.run_attempt),
        "candidate_code_executed": False,
        "auto_merge_performed": False,
        "opened_at": args.opened_at,
    }
    Path(args.output).write_bytes(_canonical(payload) + b"\n")
    return 0


def verify_final(args: argparse.Namespace) -> int:
    binding = parse_final_binding(args.binding)
    pr = json.loads(Path(args.pull_request_json).read_text(encoding="utf-8"))
    pull_request_identity = _verify_pull_request_merge_identity(
        pr,
        binding=binding,
        expected_repository=args.expected_repository,
        expected_merge_commit=args.merge_commit,
    )
    merge_commit = pull_request_identity["merge_commit"]
    origin_main_commit = _verify_protected_main_ancestry(
        Path(args.protected_main_repo),
        merge_commit=merge_commit,
        protected_main_commit=args.protected_main_commit,
    )
    archive = Path(args.artifact_zip)
    if _sha(archive) != binding["artifact_sha256"]:
        raise ValueError("materialization_artifact_hash_mismatch")
    extracted = Path(args.extracted_dir)
    _extract(archive, extracted)
    if _sha(extracted / "candidate.patch") != binding["patch_sha256"]:
        raise ValueError("materialization_patch_hash_mismatch")
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from scripts.run_foundry_ai_draft_job import _apply_inert_patch_and_hash  # noqa: PLC0415
    from app.services.foundry_source_tree import assert_clean_checkout, tracked_source_tree_hash  # noqa: PLC0415

    replay = _apply_inert_patch_and_hash(
        source_repo=Path(args.base_repo), patch_path=extracted / "candidate.patch"
    )
    if (
        replay["post_patch_source_tree_sha256"] != binding["post_patch_source_tree_sha256"]
        or replay["changed_paths"] != [binding["candidate_module_path"]]
    ):
        raise ValueError("materialization_source_replay_mismatch")
    replay_module = Path(args.base_repo) / binding["candidate_module_path"]
    merged_module = Path(args.merged_repo) / binding["candidate_module_path"]
    if (
        _sha(replay_module) != binding["candidate_module_sha256"]
        or _sha(merged_module) != binding["candidate_module_sha256"]
    ):
        raise ValueError("materialization_module_bytes_changed")
    assert_clean_checkout(args.merged_repo)
    merge_tree, _manifest = tracked_source_tree_hash(args.merged_repo)
    dependency_hash = _sha(Path(args.merged_repo) / "backend/requirements.lock")
    runner_hash = _sha(Path(args.merged_repo) / "backend/Dockerfile.foundry-demo")
    validation_sbom = {
        "schema_version": "standard_astro_candidate_validation_source_sbom_v1",
        "merge_commit": merge_commit,
        "pull_request_base_ref": pull_request_identity["pull_request_base_ref"],
        "pull_request_head_repository": pull_request_identity[
            "pull_request_head_repository"
        ],
        "origin_main_commit": origin_main_commit,
        "merge_commit_is_ancestor_of_origin_main": True,
        "merge_source_tree_sha256": merge_tree,
        "candidate_module_path": binding["candidate_module_path"],
        "candidate_module_sha256": binding["candidate_module_sha256"],
        "dependency_lock_sha256": dependency_hash,
        "runner_definition_sha256": runner_hash,
        "candidate_code_executed": False,
    }
    Path(args.sbom_output).write_bytes(_canonical(validation_sbom) + b"\n")
    result = {
        "schema_version": "standard_astro_materialization_verified_merge_v1",
        "merge_commit": merge_commit,
        "pull_request_base_ref": pull_request_identity["pull_request_base_ref"],
        "pull_request_head_repository": pull_request_identity[
            "pull_request_head_repository"
        ],
        "origin_main_commit": origin_main_commit,
        "merge_commit_is_ancestor_of_origin_main": True,
        "merge_source_tree_sha256": merge_tree,
        "candidate_module_sha256": binding["candidate_module_sha256"],
        "dependency_lock_sha256": dependency_hash,
        "runner_definition_sha256": runner_hash,
        "validation_sbom_sha256": _sha(Path(args.sbom_output)),
        "source_was_merged": True,
        "candidate_code_executed": False,
        **binding,
    }
    Path(args.output).write_bytes(_canonical(result) + b"\n")
    return 0


def make_final_payload(args: argparse.Namespace) -> int:
    verified = json.loads(Path(args.verified).read_text(encoding="utf-8"))
    binding = parse_final_binding(json.dumps({key: verified[key] for key in _FINAL_REQUEST_FIELDS}))
    image = str(args.validation_runner_image_digest).lower()
    if not _IMAGE.fullmatch(image):
        raise ValueError("materialization_validation_image_invalid")
    payload = {
        "schema_version": "standard_astro_materialization_final_v1",
        "receipt_id": str(args.receipt_id),
        "materialization_attestation_id": binding["materialization_attestation_id"],
        "candidate_id": binding["candidate_id"],
        "origin_candidate_version_id": binding["origin_candidate_version_id"],
        "origin_candidate_version_hash": binding["origin_candidate_version_hash"],
        "pull_request_number": binding["pull_request_number"],
        "pull_request_head_commit": binding["pull_request_head_commit"],
        "pull_request_base_ref": verified["pull_request_base_ref"],
        "pull_request_head_repository": verified["pull_request_head_repository"],
        "merge_commit": verified["merge_commit"],
        "origin_main_commit": verified["origin_main_commit"],
        "merge_commit_is_ancestor_of_origin_main": verified[
            "merge_commit_is_ancestor_of_origin_main"
        ],
        "merge_source_tree_sha256": verified["merge_source_tree_sha256"],
        "candidate_module_path": binding["candidate_module_path"],
        "candidate_module_sha256": verified["candidate_module_sha256"],
        "patch_sha256": binding["patch_sha256"],
        "dependency_lock_sha256": verified["dependency_lock_sha256"],
        "runner_definition_sha256": verified["runner_definition_sha256"],
        "validation_sbom_sha256": verified["validation_sbom_sha256"],
        "validation_runner_image_digest": image,
        "github_repository": args.repository,
        "github_workflow_ref": args.workflow_ref,
        "github_workflow_sha": args.workflow_sha,
        "github_run_id": str(args.run_id),
        "github_run_attempt": int(args.run_attempt),
        "source_was_merged": True,
        "candidate_code_executed": False,
        "validation_image_built_without_execution": True,
        "finalized_at": args.finalized_at,
    }
    Path(args.output).write_bytes(_canonical(payload) + b"\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("emit-pr", "emit-final"):
        item = sub.add_parser(name)
        item.add_argument("--request-id", required=True)
        item.add_argument("--binding", required=True)
        item.add_argument("--repository", required=True)
        item.add_argument("--github-output", required=True)
    item = sub.add_parser("prepare-pr")
    item.add_argument("--binding", required=True)
    item.add_argument("--artifact-zip", required=True)
    item.add_argument("--extracted-dir", required=True)
    item.add_argument("--source-repo", required=True)
    item.add_argument("--output", required=True)
    item = sub.add_parser("make-pr-payload")
    for flag in ("prepared", "pull-request-json", "attestation-id", "repository", "workflow-ref", "workflow-sha", "run-id", "run-attempt", "head-commit", "opened-at", "output"):
        item.add_argument(f"--{flag}", required=True)
    item = sub.add_parser("verify-final")
    for flag in (
        "binding", "pull-request-json", "merge-commit", "expected-repository",
        "protected-main-repo", "protected-main-commit", "artifact-zip",
        "extracted-dir", "base-repo", "merged-repo", "sbom-output", "output",
    ):
        item.add_argument(f"--{flag}", required=True)
    item = sub.add_parser("make-final-payload")
    for flag in ("verified", "receipt-id", "validation-runner-image-digest", "repository", "workflow-ref", "workflow-sha", "run-id", "run-attempt", "finalized-at", "output"):
        item.add_argument(f"--{flag}", required=True)
    args = parser.parse_args()
    if args.command == "emit-pr":
        return emit(args, final=False)
    if args.command == "emit-final":
        return emit(args, final=True)
    if args.command == "prepare-pr":
        return prepare_pr(args)
    if args.command == "make-pr-payload":
        return make_pr_payload(args)
    if args.command == "verify-final":
        return verify_final(args)
    return make_final_payload(args)


if __name__ == "__main__":
    raise SystemExit(main())
