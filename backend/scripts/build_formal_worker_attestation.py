#!/usr/bin/env python3
"""Build and sign the canonical receipt posted by protected formal-Worker CI.

The script never receives a callback bearer secret or a Registry signing key.
It hashes the actual CI outputs and signs the exact build identity with a
dedicated Ed25519 attestation key.  The control plane verifies that signature
offline; a caller-supplied ``verified=true`` flag is never trusted.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_BUNDLE_SCHEMA = "standard_astro_formal_build_attestation_bundle_v2"
_PAYLOAD_SCHEMA = "standard_astro_formal_build_attestation_v2"
_VERIFICATION_METHOD = "github_oidc_cosign_plus_ed25519_callback_v2"
_SIGNING_DOMAIN = b"standard-astro/formal-build-attestation/v2\0"
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW_REF = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/"
    r"[A-Za-z0-9_.-]+\.ya?ml@refs/heads/[A-Za-z0-9._/-]+$"
)
_KEY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_RELEASE_AUDIT_SCHEMA = "standard_astro_formal_release_audit_v1"
_RELEASE_AUDIT_RECEIPTS = {
    "dependency_lock": "static/dependency-lock-receipt.json",
    "secret_scan": "static/secret-scan-receipt.json",
    "static_audit": "static/static-audit-receipt.json",
    "linux_amd64_dependency_integrity": (
        "linux-amd64/dependency-integrity-receipt.json"
    ),
    "linux_amd64_license_policy": "linux-amd64/license-policy-receipt.json",
    "linux_amd64_environment": "linux-amd64/environment-audit-receipt.json",
    "linux_arm64_dependency_integrity": (
        "linux-arm64/dependency-integrity-receipt.json"
    ),
    "linux_arm64_license_policy": "linux-arm64/license-policy-receipt.json",
    "linux_arm64_environment": "linux-arm64/environment-audit-receipt.json",
}


class AttestationInputError(ValueError):
    """A protected-build input is malformed or does not match its bytes."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError as exc:
        raise AttestationInputError(f"required input is unavailable: {path}") from exc
    if not path.is_file() or path.is_symlink() or stat.st_size > _MAX_INPUT_BYTES:
        raise AttestationInputError(f"required input is not a bounded regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_pattern(value: str, pattern: re.Pattern[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if pattern.fullmatch(normalized) is None:
        raise AttestationInputError(f"{label} is invalid")
    return normalized


def _require_uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise AttestationInputError(f"{label} is invalid") from exc


def _parse_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationInputError("built_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AttestationInputError("built_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_object(path: Path, label: str) -> dict[str, Any]:
    sha256_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationInputError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AttestationInputError(f"{label} must be a JSON object")
    return value


def _validated_release_audit(
    directory: Path,
    *,
    source_tree_sha256: str,
    dependency_lock_sha256: str,
    formal_sbom_sha256: str,
) -> dict[str, Any]:
    aggregate_path = directory / "formal-release-audit-receipt.json"
    aggregate = _read_object(aggregate_path, "formal_release_audit")
    required = {
        "schema_version",
        "status",
        "policy_id",
        "policy_sha256",
        "source_tree_sha256",
        "dependency_lock_sha256",
        "formal_sbom_sha256",
        "architectures",
        "receipts",
        "gates",
        "advisory_database_checked",
        "vulnerability_status",
        "legal_review_complete",
    }
    receipts = aggregate.get("receipts")
    if (
        set(aggregate) != required
        or aggregate.get("schema_version") != _RELEASE_AUDIT_SCHEMA
        or aggregate.get("status") != "PASSED"
        or aggregate.get("architectures") != ["linux/amd64", "linux/arm64"]
        or aggregate.get("gates")
        != {
            "dependency_integrity": True,
            "license_inventory_policy": True,
            "tracked_source_secret_scan": True,
        }
        or aggregate.get("advisory_database_checked") is not False
        or aggregate.get("vulnerability_status") != "NOT_EVALUATED"
        or aggregate.get("legal_review_complete") is not False
        or not isinstance(receipts, dict)
        or set(receipts) != set(_RELEASE_AUDIT_RECEIPTS)
        or _HEX64.fullmatch(str(aggregate.get("policy_sha256") or "")) is None
        or not str(aggregate.get("policy_id") or "")
    ):
        raise AttestationInputError("formal release audit shape is not registered")
    if (
        aggregate.get("source_tree_sha256") != source_tree_sha256
        or aggregate.get("dependency_lock_sha256") != dependency_lock_sha256
        or aggregate.get("formal_sbom_sha256") != formal_sbom_sha256
    ):
        raise AttestationInputError("formal release audit source binding mismatch")
    for name, relative in _RELEASE_AUDIT_RECEIPTS.items():
        expected = _require_pattern(
            str(receipts.get(name) or ""),
            _HEX64,
            f"release_audit.receipts.{name}",
        )
        if sha256_file(directory / relative) != expected:
            raise AttestationInputError(
                f"formal release audit receipt hash mismatch: {name}"
            )
    result = dict(aggregate)
    # The aggregate's own byte hash is outside the recursive receipt and is
    # bound by the signed formal-build payload.
    result["aggregate_receipt_sha256"] = sha256_file(aggregate_path)
    return result


def _require_ed25519_seed(value: str) -> bytes:
    try:
        seed = base64.b64decode(str(value or "").strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttestationInputError(
            "formal-build attestation private key must be valid base64"
        ) from exc
    if len(seed) != 32:
        raise AttestationInputError(
            "formal-build attestation private key must encode 32 bytes"
        )
    return seed


def _sign_ed25519(payload: bytes, private_seed_b64: str) -> bytes:
    """Sign with OpenSSL using an RFC 8410 PKCS#8 wrapper around a raw seed.

    GitHub-hosted runners include OpenSSL 3.  Using the system primitive keeps
    the protected signing job independent from candidate dependencies and
    avoids executing anything from the candidate image while the key exists.
    """

    seed = _require_ed25519_seed(private_seed_b64)
    private_der = bytes.fromhex("302e020100300506032b657004220420") + seed
    with tempfile.TemporaryDirectory(prefix="foundry-attestation-") as directory:
        root = Path(directory)
        key_path = root / "private.der"
        payload_path = root / "payload.bin"
        signature_path = root / "signature.bin"
        key_path.write_bytes(private_der)
        os.chmod(key_path, 0o600)
        payload_path.write_bytes(payload)
        try:
            completed = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-rawin",
                    "-inkey",
                    str(key_path),
                    "-keyform",
                    "DER",
                    "-in",
                    str(payload_path),
                    "-out",
                    str(signature_path),
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AttestationInputError("Ed25519 signing primitive unavailable") from exc
        if completed.returncode != 0:
            raise AttestationInputError("Ed25519 signing failed")
        signature = signature_path.read_bytes()
    if len(signature) != 64:
        raise AttestationInputError("Ed25519 signing returned an invalid signature")
    return signature


def build_attestation(args: argparse.Namespace) -> dict[str, Any]:
    candidate_id = _require_uuid(args.candidate_id, "candidate_id")
    candidate_version_id = _require_uuid(
        args.candidate_version_id, "candidate_version_id"
    )
    candidate_version_hash = _require_pattern(
        args.candidate_version_hash, _HEX64, "candidate_version_hash"
    )
    source_receipt = _read_object(Path(args.source_receipt), "source_receipt")
    source_manifest = _read_object(Path(args.source_manifest), "source_manifest")
    if set(source_receipt) != {
        "schema",
        "git_commit",
        "source_tree_sha256",
        "tracked_file_count",
    } or set(source_manifest) != {"schema", "entries"}:
        raise AttestationInputError("source receipt shape is not registered")
    if (
        source_receipt.get("schema")
        != "standard_astro_tracked_source_manifest_v1"
        or source_manifest.get("schema") != source_receipt["schema"]
        or not isinstance(source_manifest.get("entries"), list)
        or type(source_receipt.get("tracked_file_count")) is not int
        or source_receipt["tracked_file_count"] != len(source_manifest["entries"])
    ):
        raise AttestationInputError("source receipt is inconsistent")
    source_tree_hash = sha256_bytes(canonical_json(source_manifest))
    if source_receipt.get("source_tree_sha256") != source_tree_hash:
        raise AttestationInputError("source manifest hash does not match receipt")
    expected_source_tree_hash = _require_pattern(
        args.source_tree_sha256, _HEX64, "source_tree_sha256"
    )
    if source_tree_hash != expected_source_tree_hash:
        raise AttestationInputError("source manifest does not match approved source tree")

    git_commit = _require_pattern(args.git_commit, _GIT_SHA, "git_commit")
    if source_receipt.get("git_commit") != git_commit:
        raise AttestationInputError("source receipt does not match approved commit")
    image_digest = _require_pattern(
        args.formal_worker_image_digest,
        _IMAGE_DIGEST,
        "formal_worker_image_digest",
    )
    oidc_subject = str(args.oidc_subject or "").strip()
    if not oidc_subject or len(oidc_subject) > 2048:
        raise AttestationInputError("oidc_subject is invalid")
    if args.oidc_issuer != _OIDC_ISSUER:
        raise AttestationInputError("oidc_issuer is not trusted")

    github_repository = str(args.github_repository or "").strip()
    github_workflow_ref = str(args.github_workflow_ref or "").strip()
    github_workflow_sha = _require_pattern(
        args.github_workflow_sha, _GIT_SHA, "github_workflow_sha"
    )
    github_run_id = str(args.github_run_id or "").strip()
    if (
        _REPOSITORY.fullmatch(github_repository) is None
        or _WORKFLOW_REF.fullmatch(github_workflow_ref) is None
        or not github_run_id.isdigit()
        or int(github_run_id) < 1
        or type(args.github_run_attempt) is not int
        or args.github_run_attempt < 1
    ):
        raise AttestationInputError("GitHub build identity is invalid")
    subject_image = str(args.subject_image or "").strip().lower()
    if subject_image != f"ghcr.io/{github_repository.lower()}/science-worker":
        raise AttestationInputError("formal image repository is not expected")
    signing_key_id = str(args.signing_key_id or "").strip()
    if _KEY_ID.fullmatch(signing_key_id) is None:
        raise AttestationInputError("formal-build attestation key id is invalid")

    dependency_lock_hash = sha256_file(Path(args.dependency_lock))
    formal_sbom_hash = sha256_file(Path(args.sbom))
    release_audit = _validated_release_audit(
        Path(args.release_audit_dir),
        source_tree_sha256=source_tree_hash,
        dependency_lock_sha256=dependency_lock_hash,
        formal_sbom_sha256=formal_sbom_hash,
    )

    metadata = _read_object(Path(args.build_metadata), "build_metadata")
    expected_metadata = {
        "candidate_id": candidate_id,
        "candidate_version_id": candidate_version_id,
        "candidate_version_hash": candidate_version_hash,
        "source_commit": git_commit,
        "source_tree_sha256": source_tree_hash,
        "formal_worker_image_digest": image_digest,
        "tests_passed": True,
        "platforms": ["linux/amd64", "linux/arm64"],
        "image": subject_image,
        "repository": github_repository,
        "workflow_ref": github_workflow_ref,
        "workflow_sha": github_workflow_sha,
        "run_id": github_run_id,
        "run_attempt": str(args.github_run_attempt),
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise AttestationInputError(f"build_metadata.{key} is not byte-bound")

    payload: dict[str, Any] = {
        "schema_version": _PAYLOAD_SCHEMA,
        "attestation_id": str(uuid.uuid4()),
        "candidate_id": candidate_id,
        "candidate_version_id": candidate_version_id,
        "candidate_version_hash": candidate_version_hash,
        "source_tree_sha256": source_tree_hash,
        "git_commit": git_commit,
        "dependency_lock_sha256": dependency_lock_hash,
        "formal_sbom_sha256": formal_sbom_hash,
        "test_report_sha256": sha256_file(Path(args.test_report)),
        "release_audit": release_audit,
        "tests_passed": True,
        "subject": {"image": subject_image, "digest": image_digest},
        "build_identity": {
            "github_repository": github_repository,
            "github_workflow_ref": github_workflow_ref,
            "github_workflow_sha": github_workflow_sha,
            "github_run_id": github_run_id,
            "github_run_attempt": args.github_run_attempt,
        },
        "sigstore": {
            "oidc_issuer": _OIDC_ISSUER,
            "certificate_identity": oidc_subject,
            "bundle_sha256": sha256_file(Path(args.sigstore_bundle)),
            "verification_record_sha256": sha256_file(
                Path(args.sigstore_verification)
            ),
        },
        "provenance_sha256": sha256_file(Path(args.provenance)),
        "verification_method": _VERIFICATION_METHOD,
        "build_metadata": metadata,
        "built_at": _parse_time(args.built_at),
    }
    canonical_payload = canonical_json(payload)
    signature = _sign_ed25519(
        _SIGNING_DOMAIN + canonical_payload,
        args.signing_private_key,
    )
    envelope: dict[str, Any] = {
        "schema_version": _BUNDLE_SCHEMA,
        "payload": payload,
        "payload_sha256": sha256_bytes(canonical_payload),
        "signature": {
            "algorithm": "ed25519",
            "key_id": signing_key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }
    envelope["attestation_artifact_sha256"] = sha256_bytes(
        canonical_json(envelope)
    )
    return envelope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-version-id", required=True)
    parser.add_argument("--candidate-version-hash", required=True)
    parser.add_argument("--source-receipt", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--source-tree-sha256", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--dependency-lock", required=True)
    parser.add_argument("--sbom", required=True)
    parser.add_argument("--test-report", required=True)
    parser.add_argument("--release-audit-dir", required=True)
    parser.add_argument("--formal-worker-image-digest", required=True)
    parser.add_argument("--oidc-issuer", default=_OIDC_ISSUER)
    parser.add_argument("--oidc-subject", required=True)
    parser.add_argument("--sigstore-bundle", required=True)
    parser.add_argument("--sigstore-verification", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--build-metadata", required=True)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-workflow-ref", required=True)
    parser.add_argument("--github-workflow-sha", required=True)
    parser.add_argument("--github-run-id", required=True)
    parser.add_argument("--github-run-attempt", required=True, type=int)
    parser.add_argument("--subject-image", required=True)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--built-at", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    args.signing_private_key = os.environ.get(
        "FOUNDRY_FORMAL_BUILD_ATTESTATION_PRIVATE_KEY", ""
    )
    report = build_attestation(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(report) + b"\n")
    os.chmod(output, 0o600)
    print(
        json.dumps(
            {
                "attestation_id": report["payload"]["attestation_id"],
                "candidate_version_hash": report["payload"][
                    "candidate_version_hash"
                ],
                "formal_worker_image_digest": report["payload"]["subject"][
                    "digest"
                ],
                "attestation_artifact_sha256": report[
                    "attestation_artifact_sha256"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
