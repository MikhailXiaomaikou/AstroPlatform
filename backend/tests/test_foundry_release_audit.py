"""Focused contracts for the offline Foundry formal-release audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "backend/scripts/audit_foundry_formal_release.py"
_POLICY = _ROOT / "backend/foundry_policy/formal-release-policy-v1.json"


def _module():
    spec = importlib.util.spec_from_file_location("foundry_release_audit", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock(name: str = "demo", version: str = "1.0") -> bytes:
    return (
        f"{name}=={version} \\\n"
        f"    --hash=sha256:{'a' * 64}\n"
    ).encode()


def _source_fixture(tmp_path: Path, content: bytes) -> tuple[Path, Path]:
    source = tmp_path / "repo/backend/app/services/foundry_generated/example.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    manifest = {
        "schema": "standard_astro_tracked_source_manifest_v1",
        "entries": [
            {
                "path": "backend/app/services/foundry_generated/example.py",
                "mode": "100644",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        ],
    }
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return source.parents[4], manifest_path


def test_static_audit_requires_hash_pins_and_scans_generated_source(tmp_path: Path):
    module = _module()
    policy, policy_hash = module.load_policy(_POLICY)
    requirements_input = tmp_path / "requirements.txt"
    requirements_lock = tmp_path / "requirements.lock"
    requirements_input.write_text("demo>=1,<2\n", encoding="utf-8")
    requirements_lock.write_bytes(_lock())
    receipt, records = module.audit_dependency_lock(
        requirements_input,
        requirements_lock,
        policy_id=policy["policy_id"],
        policy_sha256=policy_hash,
    )
    assert receipt["status"] == "PASSED"
    assert records[0]["name"] == "demo"
    repo, manifest = _source_fixture(tmp_path, b"VALUE = 1\n")
    secret = module.audit_tracked_source_secrets(
        repo,
        manifest,
        policy=policy,
        policy_sha256=policy_hash,
    )
    assert secret["status"] == "PASSED"
    assert secret["generated_candidate_paths"][0]["path"].endswith("example.py")
    assert secret["unresolved_findings"] == []

    requirements_lock.write_text("demo==1.0\n", encoding="utf-8")
    with pytest.raises(module.ReleaseAuditError, match="requirements_lock_hash_missing"):
        module.audit_dependency_lock(
            requirements_input,
            requirements_lock,
            policy_id=policy["policy_id"],
            policy_sha256=policy_hash,
        )
    requirements_lock.write_bytes(_lock(version="=1.0"))
    with pytest.raises(
        module.ReleaseAuditError, match="requirements_lock_not_exactly_pinned"
    ):
        module.audit_dependency_lock(
            requirements_input,
            requirements_lock,
            policy_id=policy["policy_id"],
            policy_sha256=policy_hash,
        )


def test_secret_scan_fails_closed_and_allowlist_is_test_path_plus_hash(tmp_path: Path):
    module = _module()
    policy, policy_hash = module.load_policy(_POLICY)
    private_key_marker = b"-----" + b"BEGIN PRIVATE KEY-----\nnot-a-real-key\n"
    repo, manifest = _source_fixture(tmp_path, private_key_marker)
    with pytest.raises(module.ReleaseAuditError, match="tracked_source_secret_detected"):
        module.audit_tracked_source_secrets(
            repo,
            manifest,
            policy=policy,
            policy_sha256=policy_hash,
        )

    fixture = tmp_path / "allow/backend/tests/live-shape.fixture"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(private_key_marker)
    fixture_manifest = {
        "schema": "standard_astro_tracked_source_manifest_v1",
        "entries": [
            {
                "path": "backend/tests/live-shape.fixture",
                "mode": "100644",
                "sha256": hashlib.sha256(private_key_marker).hexdigest(),
                "bytes": len(private_key_marker),
            }
        ],
    }
    fixture_manifest_path = tmp_path / "fixture-manifest.json"
    fixture_manifest_path.write_text(json.dumps(fixture_manifest), encoding="utf-8")
    allowed_policy = json.loads(_POLICY.read_text(encoding="utf-8"))
    allowed_policy["secret_policy"]["allowlist"] = [
        {
            "path": "backend/tests/live-shape.fixture",
            "sha256": hashlib.sha256(private_key_marker).hexdigest(),
            "pattern_ids": ["private_key_block"],
        }
    ]
    allowed_path = tmp_path / "allowed-policy.json"
    allowed_path.write_text(json.dumps(allowed_policy), encoding="utf-8")
    loaded, loaded_hash = module.load_policy(allowed_path)
    receipt = module.audit_tracked_source_secrets(
        tmp_path / "allow",
        fixture_manifest_path,
        policy=loaded,
        policy_sha256=loaded_hash,
    )
    assert receipt["unresolved_findings"] == []
    assert receipt["allowed_test_fixture_findings"] == [
        {
            "path": "backend/tests/live-shape.fixture",
            "sha256": hashlib.sha256(private_key_marker).hexdigest(),
            "pattern_id": "private_key_block",
            "line": 1,
        }
    ]

    allowed_policy["secret_policy"]["allowlist"][0]["path"] = "backend/app/live.py"
    invalid_path = tmp_path / "invalid-policy.json"
    invalid_path.write_text(json.dumps(allowed_policy), encoding="utf-8")
    with pytest.raises(module.ReleaseAuditError, match="secret_allowlist_entry_invalid"):
        module.load_policy(invalid_path)


def _inventory(license_expression: str | None) -> list[dict]:
    return [
        {
            "name": "demo",
            "version": "1.0",
            "license_expression": license_expression,
            "license_field_sha256": None,
            "license_field_preview": None,
            "license_classifiers": [],
            "license_files": [],
            "_policy_text": (license_expression or "").lower(),
        }
    ]


def test_environment_audit_checks_installed_pins_pip_and_license_policy(tmp_path: Path):
    module = _module()
    policy, policy_hash = module.load_policy(_POLICY)
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(_lock())
    dependency, license_receipt = module.audit_installed_environment(
        lock,
        policy=policy,
        policy_sha256=policy_hash,
        platform="linux/amd64",
        inventory=_inventory("MIT"),
        pip_check=(0, b"No broken requirements found.\n"),
        runtime_platform="linux/amd64",
    )
    assert dependency["status"] == "PASSED"
    assert dependency["pip_check"]["passed"] is True
    assert license_receipt["status"] == "PASSED"
    assert license_receipt["legal_review_complete"] is False

    with pytest.raises(module.ReleaseAuditError, match="installed_license_policy_failed"):
        module.audit_installed_environment(
            lock,
            policy=policy,
            policy_sha256=policy_hash,
            platform="linux/amd64",
            inventory=_inventory(None),
            pip_check=(0, b"ok\n"),
            runtime_platform="linux/amd64",
        )
    with pytest.raises(module.ReleaseAuditError, match="installed_license_policy_failed"):
        module.audit_installed_environment(
            lock,
            policy=policy,
            policy_sha256=policy_hash,
            platform="linux/amd64",
            inventory=_inventory("AGPL-3.0-only"),
            pip_check=(0, b"ok\n"),
            runtime_platform="linux/amd64",
        )
    with pytest.raises(module.ReleaseAuditError, match="installed_dependency_integrity_failed"):
        module.audit_installed_environment(
            lock,
            policy=policy,
            policy_sha256=policy_hash,
            platform="linux/amd64",
            inventory=[{**_inventory("MIT")[0], "version": "2.0"}],
            pip_check=(0, b"ok\n"),
            runtime_platform="linux/amd64",
        )


def test_policy_states_no_advisory_or_legal_review_claim():
    policy = json.loads(_POLICY.read_text(encoding="utf-8"))
    assert policy["license_policy"]["missing_evidence"] == "DENY"
    assert policy["dependency_policy"]["require_sha256_hashes"] is True
    source = _SCRIPT.read_text(encoding="utf-8")
    assert '"advisory_database_checked": False' in source
    assert '"vulnerability_status": "NOT_EVALUATED"' in source
    assert '"legal_review_complete": False' in source
