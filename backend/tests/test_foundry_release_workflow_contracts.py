"""Security contracts for protected Foundry build and Registry automation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import subprocess
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_DRAFT = _ROOT / ".github/workflows/foundry-candidate-draft.yml"
_CANDIDATE_DEMO = _ROOT / ".github/workflows/foundry-candidate-demo.yml"
_FORMAL_WORKER = _ROOT / ".github/workflows/foundry-formal-worker.yml"
_REGISTRY_RELEASE = _ROOT / ".github/workflows/foundry-registry-release.yml"


def _load_script(name: str):
    path = _ROOT / "backend/scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(text: str, start: str, end: str | None = None) -> str:
    offset = text.index(start)
    limit = text.index(end, offset) if end is not None else len(text)
    return text[offset:limit]


def _assert_actions_are_commit_pinned(text: str) -> None:
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
    assert uses
    assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", item) for item in uses)


def _assert_dispatch_inputs_are_not_interpolated_in_shell(text: str) -> None:
    """Require workflow_dispatch data to cross shell boundaries via env only."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:\s*(?:[|>][-+]?\s*)?$", line)
        if match is None:
            if "run:" in line:
                assert "${{ inputs." not in line
            continue
        base_indent = len(match.group(1))
        for body_line in lines[index + 1 :]:
            if not body_line.strip():
                continue
            indent = len(body_line) - len(body_line.lstrip())
            if indent <= base_indent:
                break
            assert "${{ inputs." not in body_line


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise AssertionError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def test_foundry_workflows_have_no_duplicate_yaml_keys():
    for path in sorted((_ROOT / ".github/workflows").glob("foundry-*.yml")):
        assert yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_foundry_workflows_pass_dispatch_inputs_through_environment_only():
    for path in sorted((_ROOT / ".github/workflows").glob("foundry-*.yml")):
        _assert_dispatch_inputs_are_not_interpolated_in_shell(
            path.read_text(encoding="utf-8")
        )


def test_candidate_draft_host_failure_fallback_is_uploadable_and_bash_valid():
    workflow = yaml.load(
        _CANDIDATE_DRAFT.read_text(encoding="utf-8"),
        Loader=_UniqueKeyLoader,
    )
    jobs = workflow["jobs"]
    build = jobs["materialize-and-build-without-callback"]
    callback = jobs["callback-only"]
    steps = build["steps"]
    by_name = {step.get("name"): step for step in steps if "name" in step}
    fallback = by_name[
        "Freeze a classified failed callback after host packaging failure"
    ]
    upload = by_name["Upload the frozen callback document"]

    assert "always()" in fallback["if"]
    assert "steps.callback_freeze.outcome != 'success'" in fallback["if"]
    assert "finalize-host-failure" in fallback["run"]
    draft_script = _load_script("run_foundry_ai_draft_job.py")
    assert all(
        failure_class in fallback["run"]
        for failure_class in draft_script._HOST_FAILURE_CLASSES
    )
    assert upload["if"] == "${{ always() }}"
    assert callback["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"

    bash_steps = [
        step for step in steps if step.get("shell") == "bash" and "run" in step
    ]
    assert bash_steps
    for step in bash_steps:
        checked = subprocess.run(
            ["bash", "-n"],
            input=step["run"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert checked.returncode == 0, f"{step.get('name')}: {checked.stderr}"


def test_formal_worker_workflow_separates_candidate_code_from_oidc_and_secret():
    text = _FORMAL_WORKER.read_text(encoding="utf-8")
    worker_dockerfile = (_ROOT / "backend/Dockerfile.worker").read_text(
        encoding="utf-8"
    )
    assert "pull_request_target" not in text
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in text
    assert "environment: foundry-formal-worker" in text
    assert "/api/internal/foundry/formal-build-attestations" in text
    assert "linux/amd64,linux/arm64" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text
    assert "hash_foundry_source_tree.py" in text
    assert "audit_foundry_formal_release.py" in text
    assert "formal-release-policy-v1.json" in text
    assert "static-audit-receipt.json" in text
    assert "dependency-integrity-receipt.json" in text
    assert "license-policy-receipt.json" in text
    assert "secret-scan-receipt.json" in text
    assert '--platform "$platform"' in text
    assert '"linux/amd64:linux-amd64"' in text
    assert '"linux/arm64:linux-arm64"' in text
    assert "--release-audit-dir \"$build/release-audit\"" in text
    assert "pip install --no-cache-dir --require-hashes -r requirements.lock" in (
        worker_dockerfile
    )
    assert "git archive" not in text
    assert "cosign sign --yes" in text
    assert "cosign verify" in text
    assert "--certificate-identity \"$oidc_subject\"" in text
    assert "--network none" in text
    assert "--read-only" in text
    assert "--cap-drop ALL" in text
    assert "--security-opt no-new-privileges" in text

    validation = _section(
        text,
        "  validate-without-secrets:",
        "  build-multiarch-without-oidc:",
    )
    build = _section(
        text,
        "  build-multiarch-without-oidc:",
        "  sign-and-report-from-protected-host:",
    )
    signing = _section(text, "  sign-and-report-from-protected-host:")
    assert "id-token: write" not in validation
    assert "id-token: write" not in build
    assert "id-token: write" in signing
    assert "FOUNDRY_FORMAL_BUILD_RESULT_SECRET" not in validation
    assert "FOUNDRY_FORMAL_BUILD_RESULT_SECRET" not in build
    assert text.count("FOUNDRY_FORMAL_BUILD_RESULT_SECRET") == 1
    assert text.count("secrets.FOUNDRY_FORMAL_BUILD_ATTESTATION_PRIVATE_KEY") == 1
    assert "sigstore_verified" not in text
    assert "--github-workflow-ref \"$GITHUB_WORKFLOW_REF\"" in signing
    assert "--subject-image \"$SUBJECT_IMAGE\"" in signing
    assert signing.index("cosign verify") < signing.index(
        "FOUNDRY_FORMAL_BUILD_RESULT_SECRET"
    )
    assert text.index("formal-release-audit-receipt.json") < text.index(
        "cosign sign --yes"
    )
    assert text.index("docker run --rm") < text.index(
        "/api/internal/foundry/formal-build-attestations"
    )
    _assert_dispatch_inputs_are_not_interpolated_in_shell(text)
    _assert_actions_are_commit_pinned(text)


def test_candidate_draft_build_cannot_access_callback_bearer_or_execute_patch():
    text = _CANDIDATE_DRAFT.read_text(encoding="utf-8")
    ai = _section(
        text,
        "  draft-with-ai:",
        "  materialize-and-build-without-callback:",
    )
    build = _section(
        text,
        "  materialize-and-build-without-callback:",
        "  callback-only:",
    )
    callback = _section(text, "  callback-only:")
    dockerfile = (_ROOT / "backend/Dockerfile.foundry-demo").read_text(
        encoding="utf-8"
    )

    assert "environment: foundry-candidate-draft-ai" in ai
    assert "environment: foundry-candidate-draft-build" in build
    assert "environment: foundry-candidate-draft-callback" in callback
    for section in (ai, build, callback):
        assert 'test "$GITHUB_REF" = "refs/heads/main"' in section
    assert text.count("FOUNDRY_DRAFT_RESULT_SECRET") == 1
    assert "FOUNDRY_DRAFT_RESULT_SECRET" not in build
    assert "packages: write" in build
    assert "FOUNDRY_DRAFT_RESULT_SECRET" in callback
    assert "packages: write" not in callback
    assert "contents: none" in callback
    assert "actions: read" in callback
    assert "actions/checkout" not in callback
    assert "candidate-source" not in callback
    assert "foundry-draft-output" not in callback
    assert "docker " not in callback
    assert "run_foundry_ai_draft_job.py" not in callback
    assert "foundry-draft-callback.json" in callback
    # Candidate Python enters the image only after every Dockerfile RUN layer.
    assert dockerfile.rindex("RUN ") < dockerfile.index("COPY app ./app")
    _assert_dispatch_inputs_are_not_interpolated_in_shell(text)
    _assert_actions_are_commit_pinned(text)


def test_candidate_demo_secrets_are_guarded_by_main_only_environment_contract():
    text = _CANDIDATE_DEMO.read_text(encoding="utf-8")
    release_runbook = (
        _ROOT / "docs/runbooks/FOUNDRY_RELEASE_AND_ACTIVATION.zh-CN.md"
    ).read_text(encoding="utf-8")

    assert "environment: foundry-candidate-validation" in text
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in text
    assert text.count("FOUNDRY_VALIDATION_RESULT_SECRET") == 1
    assert text.count("FOUNDRY_VALIDATION_CALLBACK_URL") == 1
    assert "Deployment branches and tags" in release_runbook
    assert "Selected branches and tags" in release_runbook
    assert "只允许 `main`" in release_runbook
    _assert_dispatch_inputs_are_not_interpolated_in_shell(text)
    _assert_actions_are_commit_pinned(text)


def test_registry_signing_key_is_offline_and_never_declared_for_render():
    text = _REGISTRY_RELEASE.read_text(encoding="utf-8")
    signer = (
        _ROOT / "backend/scripts/build_foundry_registry_release.py"
    ).read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in text
    assert "environment: foundry-registry-release" in text
    assert text.count("WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY") == 1
    assert "foundry-registry-ed25519.key" in text
    assert 'trap cleanup EXIT' in text
    assert "unset REGISTRY_PRIVATE_KEY" in text
    assert "/api/internal/foundry/registry-releases/import" in text
    assert "/api/internal/foundry/registry-releases/${RELEASE_REQUEST_ID}/export" in text
    assert text.count("FOUNDRY_REGISTRY_EXPORT_SECRET") == 1
    assert "PENDING request" in text
    assert "release_request_id" in text
    assert "release_request_run_id" not in text
    assert "current-signed-registry.json" not in text
    assert "--allow-empty-base" not in text
    assert "requested_by_user_id" not in signer
    assert "requested_by_actor_hash" in signer
    assert text.index("FOUNDRY_REGISTRY_EXPORT_SECRET") < text.index(
        "WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY"
    )
    assert text.index("build_foundry_registry_release.py") < text.index(
        "FOUNDRY_REGISTRY_IMPORT_RESULT_SECRET"
    )
    _assert_dispatch_inputs_are_not_interpolated_in_shell(text)
    _assert_actions_are_commit_pinned(text)

    for deployment_file in (
        _ROOT / "render.yaml",
        _ROOT / "docker-compose.yml",
        _ROOT / "backend/.env.example",
    ):
        deployment = deployment_file.read_text(encoding="utf-8")
        assert "WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY" not in deployment
        assert "FOUNDRY_FORMAL_BUILD_ATTESTATION_PRIVATE_KEY" not in deployment


def test_formal_build_receipt_hashes_actual_files(tmp_path: Path):
    module = _load_script("build_formal_worker_attestation.py")
    candidate_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    version_hash = "a" * 64
    commit = "b" * 40
    image_digest = "sha256:" + "c" * 64
    source_manifest = tmp_path / "source-manifest.json"
    source_receipt = tmp_path / "source-receipt.json"
    lock = tmp_path / "requirements.lock"
    sbom = tmp_path / "sbom.json"
    tests = tmp_path / "tests.xml"
    bundle = tmp_path / "sigstore.json"
    verification = tmp_path / "sigstore-verification.json"
    provenance = tmp_path / "provenance.json"
    for path, value in (
        (lock, b"locked-dependencies"),
        (sbom, b'{"spdxVersion":"SPDX-2.3"}'),
        (tests, b'<testsuite failures="0"/>'),
        (bundle, b'{"verificationMaterial":{}}'),
        (verification, b'[{"critical":{"identity":{}}}]'),
        (provenance, b'{"buildType":"formal"}'),
    ):
        path.write_bytes(value)
    manifest = {
        "schema": "standard_astro_tracked_source_manifest_v1",
        "entries": [
            {
                "path": "backend/app/example.py",
                "mode": "100644",
                "sha256": hashlib.sha256(b"source-tree").hexdigest(),
                "bytes": len(b"source-tree"),
            }
        ],
    }
    source_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    source_hash = hashlib.sha256(module.canonical_json(manifest)).hexdigest()
    source_receipt.write_text(
        json.dumps(
            {
                "schema": "standard_astro_tracked_source_manifest_v1",
                "git_commit": commit,
                "source_tree_sha256": source_hash,
                "tracked_file_count": 1,
            }
        ),
        encoding="utf-8",
    )
    metadata = {
        "candidate_id": candidate_id,
        "candidate_version_id": version_id,
        "candidate_version_hash": version_hash,
        "source_commit": commit,
        "source_tree_sha256": source_hash,
        "formal_worker_image_digest": image_digest,
        "tests_passed": True,
        "platforms": ["linux/amd64", "linux/arm64"],
        "image": "ghcr.io/example/standard-astro/science-worker",
        "repository": "example/standard-astro",
        "workflow_ref": (
            "example/standard-astro/.github/workflows/"
            "foundry-formal-worker.yml@refs/heads/main"
        ),
        "workflow_sha": "d" * 40,
        "run_id": "12345",
        "run_attempt": "1",
    }
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    release_audit_dir = tmp_path / "release-audit"
    release_receipts = {}
    for name, relative in module._RELEASE_AUDIT_RECEIPTS.items():
        path = release_audit_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            json.dumps({"name": name}, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        release_receipts[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    aggregate = {
        "schema_version": "standard_astro_formal_release_audit_v1",
        "status": "PASSED",
        "policy_id": "standard-astro-formal-release-minimal-v1",
        "policy_sha256": "f" * 64,
        "source_tree_sha256": source_hash,
        "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "formal_sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
        "architectures": ["linux/amd64", "linux/arm64"],
        "receipts": release_receipts,
        "gates": {
            "dependency_integrity": True,
            "license_inventory_policy": True,
            "tracked_source_secret_scan": True,
        },
        "advisory_database_checked": False,
        "vulnerability_status": "NOT_EVALUATED",
        "legal_review_complete": False,
    }
    aggregate_path = release_audit_dir / "formal-release-audit-receipt.json"
    aggregate_path.write_bytes(module.canonical_json(aggregate) + b"\n")
    args = argparse.Namespace(
        candidate_id=candidate_id,
        candidate_version_id=version_id,
        candidate_version_hash=version_hash,
        source_receipt=str(source_receipt),
        source_manifest=str(source_manifest),
        source_tree_sha256=source_hash,
        git_commit=commit,
        dependency_lock=str(lock),
        sbom=str(sbom),
        test_report=str(tests),
        release_audit_dir=str(release_audit_dir),
        formal_worker_image_digest=image_digest,
        oidc_issuer="https://token.actions.githubusercontent.com",
        oidc_subject=(
            "https://github.com/example/standard-astro/.github/workflows/"
            "foundry-formal-worker.yml@refs/heads/main"
        ),
        sigstore_bundle=str(bundle),
        sigstore_verification=str(verification),
        provenance=str(provenance),
        build_metadata=str(metadata_path),
        github_repository="example/standard-astro",
        github_workflow_ref=(
            "example/standard-astro/.github/workflows/"
            "foundry-formal-worker.yml@refs/heads/main"
        ),
        github_workflow_sha="d" * 40,
        github_run_id="12345",
        github_run_attempt=1,
        subject_image="ghcr.io/example/standard-astro/science-worker",
        signing_key_id="formal-build-test-1",
        signing_private_key=base64.b64encode(b"\x11" * 32).decode("ascii"),
        built_at="2026-07-21T12:00:00Z",
    )
    report = module.build_attestation(args)
    artifact_hash = report.pop("attestation_artifact_sha256")
    assert artifact_hash == hashlib.sha256(module.canonical_json(report)).hexdigest()
    payload = report["payload"]
    assert report["payload_sha256"] == hashlib.sha256(
        module.canonical_json(payload)
    ).hexdigest()
    assert payload["dependency_lock_sha256"] == hashlib.sha256(
        lock.read_bytes()
    ).hexdigest()
    assert payload["formal_sbom_sha256"] == hashlib.sha256(
        sbom.read_bytes()
    ).hexdigest()
    assert payload["test_report_sha256"] == hashlib.sha256(
        tests.read_bytes()
    ).hexdigest()
    assert payload["release_audit"]["aggregate_receipt_sha256"] == hashlib.sha256(
        aggregate_path.read_bytes()
    ).hexdigest()
    assert payload["release_audit"]["receipts"] == release_receipts
    assert payload["release_audit"]["advisory_database_checked"] is False
    assert payload["release_audit"]["vulnerability_status"] == "NOT_EVALUATED"
    assert "sigstore_verified" not in payload
    assert payload["subject"] == {
        "image": "ghcr.io/example/standard-astro/science-worker",
        "digest": image_digest,
    }
    signature = base64.b64decode(report["signature"]["value"], validate=True)
    public_key = Ed25519PrivateKey.from_private_bytes(b"\x11" * 32).public_key()
    public_key.verify(
        signature,
        b"standard-astro/formal-build-attestation/v2\0"
        + module.canonical_json(payload),
    )
    first_receipt = release_audit_dir / next(
        iter(module._RELEASE_AUDIT_RECEIPTS.values())
    )
    first_receipt.write_bytes(b'{"tampered":true}\n')
    with pytest.raises(module.AttestationInputError, match="receipt hash mismatch"):
        module.build_attestation(args)


def _entry(
    module,
    *,
    state: str = "REGISTERED",
    reason: str | None = None,
    workflow_id: str = "candidate.test_workflow.v1",
    candidate_id: str = "candidate-1",
) -> dict:
    workflow = {
        "workflow_id": workflow_id,
        "version": "1.0.0",
        "state": state,
        "revocation_reason": reason,
    }
    tools = [{"tool_id": "candidate.test_tool.v1", "version": "1.0.0"}]
    release_binding = {
        "binding_kind": "signed_candidate_release",
        "candidate_id": candidate_id,
        "candidate_version": 1,
        "candidate_version_hash": "d" * 64,
        "approved_worker_image_digest": "sha256:" + "f" * 64,
        "approval_attestation_hash": "sha256:" + "a" * 64,
        "build_attestation_hash": "sha256:" + "b" * 64,
    }
    return {
        "candidate_id": candidate_id,
        "candidate_version": 1,
        "candidate_version_hash": "d" * 64,
        "workflow_spec_hash": "sha256:" + "e" * 64,
        "worker_image_digest": "sha256:" + "f" * 64,
        "approval_attestation_hash": "sha256:" + "a" * 64,
        "build_attestation_hash": "sha256:" + "b" * 64,
        "workflow": workflow,
        "tools": tools,
        "registry_entry_hash": "sha256:"
        + module.sha256(
            module.canonical_json(
                {
                    "workflow": workflow,
                    "tools": tools,
                    "release_binding": release_binding,
                }
            )
        ),
        "installation_status": "PENDING_RELEASE",
        "runtime_registry_modified": False,
    }


def _request(
    module,
    operations: list[dict],
    *,
    epoch: str,
    previous=None,
    base_hash: str = "sha256:" + "1" * 64,
    new_operation_count: int | None = None,
) -> dict:
    context = {"release_test": True}
    new_count = len(operations) if new_operation_count is None else new_operation_count
    assert 1 <= new_count <= len(operations)
    prior_count = len(operations) - new_count
    bound_operations = [
        {
            **operation,
            "context": (
                context if index >= prior_count else {"prior_release": index + 1}
            ),
        }
        for index, operation in enumerate(operations)
    ]
    new_operations = bound_operations[-new_count:]
    request = {
        "schema_version": "standard_astro_registry_release_request_v1",
        "request_id": str(uuid.uuid4()),
        "request_kind": "REGISTER_CANDIDATE",
        "request_epoch": "pending.test",
        "request_status": "PENDING_SIGNATURE",
        "requested_at": "2026-07-21T12:00:00+00:00",
        "requested_by_actor_hash": "sha256:" + "9" * 64,
        "base_registry_epoch": epoch,
        "base_registry_hash": base_hash,
        "previous_request_hash": previous,
        "new_operations": new_operations,
        "operation_sequence": bound_operations,
        "operation_sequence_hash": "sha256:"
        + module.sha256(module.canonical_json(bound_operations)),
        "entries": [
            operation["entry"]
            for operation in new_operations
            if operation["operation"] == "UPSERT_ENTRY"
        ],
        "status_changes": [
            operation["status_change"]
            for operation in new_operations
            if operation["operation"] == "SET_ENTRY_STATUS"
        ],
        "context": context,
        "runtime_registry_modified": False,
        "signature_required": True,
    }
    return request


def test_registry_signer_builds_complete_signed_overlay_and_applies_revocation(
    tmp_path: Path,
):
    module = _load_script("build_foundry_registry_release.py")
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_path = tmp_path / "private.key"
    private_path.write_text(base64.b64encode(private_raw).decode("ascii"))

    entry = _entry(module)
    first_operations = [{"operation": "UPSERT_ENTRY", "entry": entry}]
    first_request = _request(module, first_operations, epoch="builtin.1")
    first_path = tmp_path / "first-request.json"
    first_path.write_text(json.dumps(first_request))
    first_args = argparse.Namespace(
        request=str(first_path),
        request_sha256=module.sha256(module.canonical_json(first_request)),
        private_key_file=str(private_path),
        key_id="registry-test-key",
        registry_epoch="foundry.release.1",
    )
    first_snapshot, first_import = module.build_release(first_args)
    retry_snapshot, retry_import = module.build_release(first_args)
    assert retry_snapshot == first_snapshot
    assert retry_import == first_import
    assert first_import["complete_entry_count"] == 1
    assert first_snapshot["payload"]["base_registry_epoch"] == "builtin.1"
    assert first_snapshot["payload"]["operation_sequence"] == 1
    signature = base64.b64decode(first_snapshot["signature"]["value"])
    Ed25519PublicKey.from_public_bytes(public_raw).verify(
        signature, module.jcs_canonicalize(first_snapshot["payload"])
    )

    suspended_change = {
        "registry_entry_id": str(uuid.uuid4()),
        "registry_entry_hash": entry["registry_entry_hash"],
        "workflow_id": entry["workflow"]["workflow_id"],
        "workflow_version": entry["workflow"]["version"],
        "requested_status": "SUSPENDED",
        "reason": "temporary scientific review",
    }
    revoked_change = {
        "registry_entry_id": suspended_change["registry_entry_id"],
        "registry_entry_hash": entry["registry_entry_hash"],
        "workflow_id": entry["workflow"]["workflow_id"],
        "workflow_version": entry["workflow"]["version"],
        "requested_status": "REVOKED",
        "reason": "scientific contract retired",
    }
    second_operations = [
        {"operation": "UPSERT_ENTRY", "entry": entry},
        {"operation": "SET_ENTRY_STATUS", "status_change": suspended_change},
        {"operation": "SET_ENTRY_STATUS", "status_change": revoked_change},
    ]
    second_request = _request(
        module,
        second_operations,
        epoch="builtin.1",
        previous="sha256:" + "2" * 64,
        new_operation_count=1,
    )
    second_path = tmp_path / "second-request.json"
    second_path.write_text(json.dumps(second_request))
    second_args = argparse.Namespace(
        request=str(second_path),
        request_sha256=module.sha256(module.canonical_json(second_request)),
        private_key_file=str(private_path),
        key_id="registry-test-key",
        registry_epoch="foundry.release.2",
    )
    second_snapshot, second_import = module.build_release(second_args)
    assert second_import["complete_entry_count"] == 1
    assert second_snapshot["payload"]["base_registry_epoch"] == "builtin.1"
    assert second_snapshot["payload"]["operation_sequence"] == 3
    released = second_snapshot["payload"]["entries"][0]
    assert released["workflow"]["state"] == "REVOKED"
    assert released["workflow"]["revocation_reason"] == "scientific contract retired"
    assert private_raw not in module.jcs_canonicalize(second_import)


def test_registry_replay_supports_explicit_superseding_workflow():
    module = _load_script("build_foundry_registry_release.py")
    original = _entry(module)
    successor = _entry(
        module,
        workflow_id="candidate.test_workflow.v2",
        candidate_id="candidate-2",
    )
    context = {"release_test": True}
    operations = [
        {"operation": "UPSERT_ENTRY", "entry": original, "context": context},
        {"operation": "UPSERT_ENTRY", "entry": successor, "context": context},
        {
            "operation": "SET_ENTRY_STATUS",
            "status_change": {
                "registry_entry_id": str(uuid.uuid4()),
                "registry_entry_hash": original["registry_entry_hash"],
                "workflow_id": original["workflow"]["workflow_id"],
                "workflow_version": original["workflow"]["version"],
                "requested_status": "SUPERSEDED",
                "reason": "replaced by a reviewed workflow",
                "superseded_by_workflow_id": successor["workflow"]["workflow_id"],
                "superseded_by_workflow_version": successor["workflow"]["version"],
            },
            "context": context,
        },
    ]
    replayed = module._apply_operations([], operations)
    by_id = {entry["workflow"]["workflow_id"]: entry for entry in replayed}
    assert by_id["candidate.test_workflow.v1"]["workflow"]["state"] == "SUPERSEDED"
    assert by_id["candidate.test_workflow.v2"]["workflow"]["state"] == "REGISTERED"


def test_registration_static_gate_rejects_demo_only_or_unshipped_entrypoint():
    from app.services import workflow_registry_v2 as runtime

    candidate_hash = "6" * 64
    workflow = deepcopy(runtime._FORMAL_WORKFLOWS[0].to_dict())
    referenced_identities = {
        (node["tool_id"], node["tool_version"])
        for node in workflow["tool_dag"]
    }
    tools = [
        deepcopy(tool.to_dict())
        for tool in runtime._FORMAL_TOOLS
        if (tool.tool_id, tool.version) in referenced_identities
    ]
    original_entrypoint = workflow["primary_entrypoint_id"]
    unshipped_entrypoint = "candidate.generated.formal.v1"
    for tool in tools:
        if tool["entrypoint_id"] == original_entrypoint:
            tool["entrypoint_id"] = unshipped_entrypoint
    for node in workflow["tool_dag"]:
        if node["entrypoint_id"] == original_entrypoint:
            node["entrypoint_id"] = unshipped_entrypoint
    workflow["primary_entrypoint_id"] = unshipped_entrypoint
    workflow_hash = "sha256:" + hashlib.sha256(
        runtime.jcs_canonicalize(workflow)
    ).hexdigest()
    reviews = [
        {
            "reviewer_id": "engineering-reviewer",
            "reviewer_type": "human",
            "review_role": "engineering",
            "decision": "APPROVED",
            "candidate_version_hash": candidate_hash,
        },
        {
            "reviewer_id": "scientific-reviewer",
            "reviewer_type": "human",
            "review_role": "scientific",
            "decision": "APPROVED",
            "candidate_version_hash": candidate_hash,
        }
    ]
    with pytest.raises(
        runtime.WorkflowRegistryError,
        match="workflow_execution_adapter_not_static",
    ):
        runtime.build_registry_entry_from_approved_candidate(
            {
                "candidate_id": str(uuid.uuid4()),
                "candidate_version": 1,
                "candidate_version_hash": candidate_hash,
                "approved_candidate_version_hash": candidate_hash,
                "status": "APPROVED",
                "workflow_spec": workflow,
                "workflow_spec_hash": workflow_hash,
                "worker_image_digest": "sha256:" + "8" * 64,
                "tool_specs": tools,
                "reviews": reviews,
            }
        )


def test_offline_signer_output_is_accepted_by_runtime_registry(tmp_path: Path):
    from app.services import workflow_registry_v2 as runtime

    module = _load_script("build_foundry_registry_release.py")
    candidate_hash = "7" * 64
    workflow = runtime._FORMAL_WORKFLOWS[0].to_dict()
    workflow_hash = "sha256:" + module.sha256(
        runtime.jcs_canonicalize(workflow)
    )
    reviews = [
        {
            "reviewer_id": "engineering-reviewer",
            "reviewer_type": "human",
            "review_role": "engineering",
            "decision": "APPROVED",
            "candidate_version_hash": candidate_hash,
        },
        {
            "reviewer_id": "science-reviewer",
            "reviewer_type": "human",
            "review_role": "scientific",
            "decision": "APPROVED",
            "candidate_version_hash": candidate_hash,
        },
    ]
    entry = runtime.build_registry_entry_from_approved_candidate(
        {
            "candidate_id": str(uuid.uuid4()),
            "candidate_version": 1,
            "candidate_version_hash": candidate_hash,
            "approved_candidate_version_hash": candidate_hash,
            "status": "APPROVED",
            "workflow_spec": workflow,
            "workflow_spec_hash": workflow_hash,
            "worker_image_digest": "sha256:" + "8" * 64,
            "tool_specs": [item.to_dict() for item in runtime._FORMAL_TOOLS],
            "reviews": reviews,
        }
    )
    assert runtime.assert_registry_entry_static_compatible(entry) == entry
    base = runtime.registry_snapshot()
    request = _request(
        module,
        [{"operation": "UPSERT_ENTRY", "entry": entry}],
        epoch=base["epoch"],
        base_hash=base["registry_hash"],
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))

    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_path = tmp_path / "private.key"
    private_path.write_text(base64.b64encode(private_raw).decode("ascii"))
    args = argparse.Namespace(
        request=str(request_path),
        request_sha256=module.sha256(module.canonical_json(request)),
        private_key_file=str(private_path),
        key_id="runtime-contract-test",
        registry_epoch="foundry.runtime.contract.1",
    )
    snapshot, import_receipt = module.build_release(args)

    assert set(snapshot["payload"]) == {
        "schema_version",
        "registry_epoch",
        "base_registry_epoch",
        "base_registry_hash",
        "operation_sequence",
        "entries",
    }
    verified = runtime.verify_signed_registry_snapshot(
        snapshot,
        {"runtime-contract-test": base64.b64encode(public_raw).decode("ascii")},
    )
    assert verified == snapshot["payload"]
    loaded = runtime.load_verified_registry_release(
        snapshot,
        {"runtime-contract-test": base64.b64encode(public_raw).decode("ascii")},
    )
    assert loaded.epoch == "foundry.runtime.contract.1"
    assert loaded.workflow_release_bindings
    assert set(import_receipt) == {
        "schema_version",
        "release_request_id",
        "release_request_sha256",
        "base_registry_epoch",
        "base_registry_hash",
        "registry_epoch",
        "registry_snapshot_sha256",
        "signing_key_id",
        "signing_public_key_sha256",
        "complete_entry_count",
        "generated_at",
        "import_mode",
        "signed_snapshot",
        "receipt_sha256",
    }
