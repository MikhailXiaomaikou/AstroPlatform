"""Locks for the compiled formal-workflow registry and release signatures."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.services.workflow_registry_v2 as registry_module
from app.services.evidence_pack_v2 import jcs_canonicalize
from app.services.workflow_registry_v2 import (
    CONTROL_IMAGE_BUILD_SPEC_SHA256,
    DESI_DR2_MATRIX_ENTRYPOINT_ID,
    DESI_DR2_MATRIX_WORKFLOW_ID,
    REGISTRY_EPOCH,
    REQUIREMENTS_LOCK_SHA256,
    UNION3_PRIMARY_ENTRYPOINT_ID,
    UNION3_REPRODUCTION_WORKFLOW_ID,
    WORKER_IMAGE_BUILD_SPEC_SHA256,
    WorkflowRegistryError,
    activate_verified_registry_release,
    assert_workflow_executable,
    build_registry_status_entry,
    build_signed_registry_snapshot,
    get_formal_workflow,
    get_formal_workflow_spec,
    get_static_execution_adapter_binding,
    get_worker_execution_binding,
    list_formal_workflows,
    list_worker_execution_bindings,
    load_verified_registry_release,
    registry_snapshot,
    validate_candidate_registration,
    verify_signed_registry_snapshot,
)


def _candidate_payload() -> dict:
    snapshot = registry_snapshot()
    workflow = next(
        item
        for item in snapshot["workflows"]
        if item["workflow_id"] == UNION3_REPRODUCTION_WORKFLOW_ID
    )
    referenced = {
        (node["tool_id"], node["tool_version"])
        for node in workflow["tool_dag"]
    }
    tools = [
        item
        for item in snapshot["tools"]
        if (item["tool_id"], item["version"]) in referenced
    ]
    workflow_hash = "sha256:" + hashlib.sha256(
        jcs_canonicalize(workflow)
    ).hexdigest()
    candidate_hash = "sha256:" + "c" * 64
    return {
        "workflow": workflow,
        "workflow_hash": workflow_hash,
        "tools": tools,
        "candidate_hash": candidate_hash,
        "reviews": [
            {
                "reviewer_id": "engineer-1",
                "reviewer_type": "human",
                "review_role": "engineering",
                "decision": "APPROVED",
                "candidate_version_hash": candidate_hash,
            },
            {
                "reviewer_id": "scientist-1",
                "reviewer_type": "human",
                "review_role": "scientific",
                "decision": "APPROVED",
                "candidate_version_hash": candidate_hash,
            },
        ],
    }


def _signing_keys() -> tuple[str, str]:
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
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def test_registry_compiles_union3_and_desi_without_python_import_paths():
    catalog = list_formal_workflows()
    assert [item["workflow_id"] for item in catalog] == [
        UNION3_REPRODUCTION_WORKFLOW_ID,
    ]
    inactive = list_formal_workflows(include_inactive=True)
    desi = next(
        item for item in inactive if item["workflow_id"] == DESI_DR2_MATRIX_WORKFLOW_ID
    )
    assert desi["state"] == "SUSPENDED"
    snapshot = registry_snapshot()
    assert snapshot == registry_snapshot()
    assert snapshot["epoch"] == REGISTRY_EPOCH
    assert snapshot["registry_hash"].startswith("sha256:")
    entrypoints = {tool["entrypoint_id"] for tool in snapshot["tools"]}
    assert UNION3_PRIMARY_ENTRYPOINT_ID in entrypoints
    assert DESI_DR2_MATRIX_ENTRYPOINT_ID in entrypoints
    assert all(not entrypoint.startswith("app.") for entrypoint in entrypoints)


def test_formal_registry_compatibility_snapshot():
    snapshot = registry_snapshot()
    assert snapshot["registry_hash"] == (
        "sha256:4627bf677949e1ae7fabc0b424922b46d8c66647e307e3873e62414f7660a11d"
    )
    assert snapshot["workflow_entry_hashes"] == {
        "desi_dr2_dark_energy_matrix_v1@1.0.0": (
            "sha256:d0ace32b0bd7a20828e8abe900195040001239fbe030d05660fd26cd6b72d7bb"
        ),
        "union3_flat_lcdm_sn_only_v1@1.0.0": (
            "sha256:7037abb698d20eb49deb38d2a57509028b2f5f9a32a7b7c73027d51970e28be0"
        ),
    }


def test_tool_build_and_dependency_pins_match_release_files():
    backend = Path(__file__).resolve().parents[1]

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    assert digest(backend / "requirements.lock") == REQUIREMENTS_LOCK_SHA256
    assert digest(backend / "Dockerfile") == CONTROL_IMAGE_BUILD_SPEC_SHA256
    assert digest(backend / "Dockerfile.worker") == WORKER_IMAGE_BUILD_SPEC_SHA256


def test_catalog_is_safe_defensive_and_bound_to_entry_hash():
    union3 = get_formal_workflow(UNION3_REPRODUCTION_WORKFLOW_ID)
    assert set(union3).isdisjoint(
        {"tool_dag", "science_contract", "legacy_contract", "dataset_pins"}
    )
    assert union3["publication_ready"] is False
    assert union3["registry_entry_hash"].startswith("sha256:")
    union3["allowed_claims"].append("forged")
    assert "forged" not in get_formal_workflow(
        UNION3_REPRODUCTION_WORKFLOW_ID
    )["allowed_claims"]
    typed = get_formal_workflow_spec(UNION3_REPRODUCTION_WORKFLOW_ID)
    typed.science_contract["grid_step"] = "forged"
    assert (
        get_formal_workflow_spec(UNION3_REPRODUCTION_WORKFLOW_ID).science_contract[
            "grid_step"
        ]
        == "0.0005"
    )


def test_executable_gate_rejects_stale_bindings():
    binding = get_worker_execution_binding(UNION3_REPRODUCTION_WORKFLOW_ID)
    accepted = assert_workflow_executable(
        UNION3_REPRODUCTION_WORKFLOW_ID,
        binding["workflow_version"],
        registry_epoch=binding["registry_epoch"],
        registry_entry_hash=binding["registry_entry_hash"],
    )
    assert accepted["state"] == "REGISTERED"
    with pytest.raises(
        WorkflowRegistryError, match="workflow_registry_entry_hash_mismatch"
    ):
        assert_workflow_executable(
            UNION3_REPRODUCTION_WORKFLOW_ID,
            registry_entry_hash="sha256:" + "0" * 64,
        )


def test_desi_matrix_uses_the_existing_fixed_scientific_arguments():
    workflow = get_formal_workflow_spec(DESI_DR2_MATRIX_WORKFLOW_ID)
    assert workflow.primary_entrypoint_id == "run_dark_energy_evidence_matrix"
    assert workflow.science_contract["tool_arguments"] == {
        "model": "w0wa_cdm",
        "supernova_sets": ["pantheon_plus", "union3", "des_sn5yr"],
        "include_desi_dr1_reference": False,
    }
    assert workflow.output_policy["publication_ready"] is False
    assert len(workflow.dataset_pins) > 20


def test_approved_candidate_build_is_pure_and_requires_exact_human_review():
    candidate = _candidate_payload()
    before = registry_snapshot()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    assert entry["installation_status"] == "PENDING_RELEASE"
    assert entry["runtime_registry_modified"] is False
    assert registry_snapshot() == before

    forged_reviews = copy.deepcopy(candidate["reviews"])
    for review in forged_reviews:
        review["reviewer_type"] = "ai"
    with pytest.raises(
        WorkflowRegistryError, match="candidate_human_approval_missing"
    ):
        validate_candidate_registration(
            candidate["workflow"],
            candidate["workflow_hash"],
            "sha256:" + "d" * 64,
            candidate_id="candidate-1",
            candidate_version=1,
            candidate_version_hash=candidate["candidate_hash"],
            approved_candidate_version_hash=candidate["candidate_hash"],
            tool_specs=candidate["tools"],
            reviews=forged_reviews,
        )


def test_signed_candidate_snapshot_round_trip_and_tamper_rejection():
    candidate = _candidate_payload()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    private_key, public_key = _signing_keys()
    signed = build_signed_registry_snapshot([entry], private_key, "registry-2026-01")
    assert signed["payload"]["base_registry_epoch"] == REGISTRY_EPOCH
    assert signed["payload"]["base_registry_hash"] == registry_snapshot()[
        "registry_hash"
    ]
    assert signed["payload"]["operation_sequence"] == 1
    verified = verify_signed_registry_snapshot(
        signed, {"registry-2026-01": public_key}
    )
    assert verified["entries"][0]["registry_entry_hash"] == entry[
        "registry_entry_hash"
    ]

    tampered = copy.deepcopy(signed)
    tampered["payload"]["entries"][0]["worker_image_digest"] = (
        "sha256:" + "e" * 64
    )
    # Pin the exact gate: a payload edit without re-signing must die on the
    # payload-hash check, not merely on "some" registry error.
    with pytest.raises(
        WorkflowRegistryError, match="registry_snapshot_payload_hash_mismatch"
    ):
        verify_signed_registry_snapshot(tampered, {"registry-2026-01": public_key})


def test_missing_registry_signing_key_is_service_unavailable():
    candidate = _candidate_payload()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    with pytest.raises(WorkflowRegistryError) as error:
        build_signed_registry_snapshot([entry], "", "registry-2026-01")
    assert error.value.code == "registry_signing_key_unavailable"
    assert error.value.status_code == 503


def test_release_loader_merges_signed_composition_from_mapping_and_file(tmp_path):
    candidate = _candidate_payload()
    candidate["workflow"] = copy.deepcopy(candidate["workflow"])
    candidate["workflow"]["workflow_id"] = "union3_reproduction_composition_v1"
    candidate["workflow"]["display_name"] = "Union3 composition release test"
    candidate["workflow_hash"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(candidate["workflow"])
    ).hexdigest()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-composition-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    private_key, public_key = _signing_keys()
    signed = build_signed_registry_snapshot(
        [entry],
        private_key,
        "registry-2026-01",
        epoch="2026-07-21.release-test",
    )

    loaded = load_verified_registry_release(
        signed,
        {"registry-2026-01": public_key},
    )
    assert loaded.epoch == "2026-07-21.release-test"
    assert {workflow.workflow_id for workflow in loaded.workflows} == {
        DESI_DR2_MATRIX_WORKFLOW_ID,
        UNION3_REPRODUCTION_WORKFLOW_ID,
        "union3_reproduction_composition_v1",
    }
    release_binding = loaded.workflow_release_bindings[
        "union3_reproduction_composition_v1@1.0.0"
    ]
    assert release_binding["candidate_version_hash"] == candidate["candidate_hash"]
    assert release_binding["approved_worker_image_digest"] == "sha256:" + "d" * 64

    release_path = tmp_path / "workflow_registry.release.json"
    release_path.write_text(json.dumps(signed), encoding="utf-8")
    from_file = load_verified_registry_release(
        release_path,
        {"registry-2026-01": public_key},
    )
    assert from_file.to_dict() == loaded.to_dict()


def test_signed_alias_is_static_adapter_compatible_and_image_filtered(monkeypatch):
    candidate = _candidate_payload()
    candidate["workflow"] = copy.deepcopy(candidate["workflow"])
    candidate["workflow"]["workflow_id"] = "union3_registered_alias_v1"
    candidate["workflow"]["version"] = "2.0.0"
    candidate["workflow"]["display_name"] = "Union3 registered alias"
    candidate["workflow"]["summary"] = "Same science, signed alias identity."
    candidate["workflow_hash"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(candidate["workflow"])
    ).hexdigest()
    image_digest = "sha256:" + "7" * 64
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        image_digest,
        candidate_id="candidate-alias-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    private_key, public_key = _signing_keys()
    signed = build_signed_registry_snapshot(
        [entry], private_key, "registry-alias-key", epoch="2026-07-21.alias"
    )
    for name in (
        "_SNAPSHOT",
        "_WORKFLOWS_BY_IDENTITY",
        "_WORKFLOWS_BY_ID",
        "_TOOLS_BY_ENTRYPOINT",
        "_ACTIVE_RELEASE_TRUST",
        "_REGISTRY_LOOKUP_STARTED",
        "_REGISTRY_ACTIVATED",
    ):
        monkeypatch.setattr(registry_module, name, getattr(registry_module, name))
    monkeypatch.setattr(registry_module, "_REGISTRY_LOOKUP_STARTED", False)
    monkeypatch.setattr(registry_module, "_REGISTRY_ACTIVATED", False)
    activate_verified_registry_release(signed, {"registry-alias-key": public_key})

    adapter = get_static_execution_adapter_binding(
        "union3_registered_alias_v1", "2.0.0"
    )
    assert adapter["canonical_workflow_id"] == UNION3_REPRODUCTION_WORKFLOW_ID
    matching = list_worker_execution_bindings(worker_image_digest=image_digest)
    assert {item["workflow_id"] for item in matching} == {
        UNION3_REPRODUCTION_WORKFLOW_ID,
        "union3_registered_alias_v1",
    }
    other = list_worker_execution_bindings(
        worker_image_digest="sha256:" + "8" * 64
    )
    assert {item["workflow_id"] for item in other} == {
        UNION3_REPRODUCTION_WORKFLOW_ID
    }


def test_activated_worker_binding_records_outer_registry_signature(
    monkeypatch,
):
    import app.services.workflow_registry_v2 as registry_module

    candidate = _candidate_payload()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-release-trust",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    private_key, public_key = _signing_keys()
    signed = build_signed_registry_snapshot(
        [entry], private_key, "registry-trust-key", epoch="2026-07-21.trust"
    )
    monkeypatch.setattr(registry_module, "_SNAPSHOT", registry_module._SNAPSHOT)
    monkeypatch.setattr(
        registry_module,
        "_WORKFLOWS_BY_IDENTITY",
        registry_module._WORKFLOWS_BY_IDENTITY,
    )
    monkeypatch.setattr(
        registry_module, "_WORKFLOWS_BY_ID", registry_module._WORKFLOWS_BY_ID
    )
    monkeypatch.setattr(
        registry_module,
        "_TOOLS_BY_ENTRYPOINT",
        registry_module._TOOLS_BY_ENTRYPOINT,
    )
    monkeypatch.setattr(
        registry_module,
        "_ACTIVE_RELEASE_TRUST",
        registry_module._ACTIVE_RELEASE_TRUST,
    )
    monkeypatch.setattr(registry_module, "_REGISTRY_LOOKUP_STARTED", False)
    monkeypatch.setattr(registry_module, "_REGISTRY_ACTIVATED", False)
    activate_verified_registry_release(
        signed, {"registry-trust-key": public_key}
    )

    binding = get_worker_execution_binding(UNION3_REPRODUCTION_WORKFLOW_ID)
    assert binding["registry_release_kind"] == "signed_registry_release"
    assert binding["registry_release_signature_algorithm"] == "ed25519"
    assert binding["registry_release_key_id"] == "registry-trust-key"
    assert binding["registry_release_payload_sha256"] == signed["payload_sha256"]
    assert binding["registry_hash"].startswith("sha256:")


def test_release_loader_defaults_to_defensive_builtins():
    loaded = load_verified_registry_release()
    loaded.workflow_entry_hashes["forged"] = "sha256:" + "0" * 64
    assert "forged" not in registry_snapshot()["workflow_entry_hashes"]


def test_release_loader_rejects_unshipped_static_entrypoint():
    candidate = _candidate_payload()
    candidate["workflow"] = copy.deepcopy(candidate["workflow"])
    candidate["tools"] = copy.deepcopy(candidate["tools"])
    candidate["workflow"]["workflow_id"] = "union3_unshipped_entrypoint_v1"
    candidate["workflow"]["primary_entrypoint_id"] = "candidate.unshipped.v1"
    candidate["workflow"]["tool_dag"][0]["entrypoint_id"] = (
        "candidate.unshipped.v1"
    )
    primary_tool = next(
        tool
        for tool in candidate["tools"]
        if tool["entrypoint_id"] == UNION3_PRIMARY_ENTRYPOINT_ID
    )
    primary_tool["entrypoint_id"] = "candidate.unshipped.v1"
    candidate["workflow_hash"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(candidate["workflow"])
    ).hexdigest()
    with pytest.raises(
        WorkflowRegistryError,
        match="workflow_execution_adapter_not_static",
    ):
        validate_candidate_registration(
            candidate["workflow"],
            candidate["workflow_hash"],
            "sha256:" + "d" * 64,
            candidate_id="candidate-unshipped-1",
            candidate_version=1,
            candidate_version_hash=candidate["candidate_hash"],
            approved_candidate_version_hash=candidate["candidate_hash"],
            tool_specs=candidate["tools"],
            reviews=candidate["reviews"],
        )


def test_release_loader_rejects_conflicting_builtin_identity():
    candidate = _candidate_payload()
    candidate["workflow"] = copy.deepcopy(candidate["workflow"])
    candidate["workflow"]["science_contract"]["grid_step"] = "0.001"
    candidate["workflow_hash"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(candidate["workflow"])
    ).hexdigest()
    with pytest.raises(
        WorkflowRegistryError,
        match="workflow_execution_adapter_not_static",
    ):
        validate_candidate_registration(
            candidate["workflow"],
            candidate["workflow_hash"],
            "sha256:" + "d" * 64,
            candidate_id="candidate-conflict-1",
            candidate_version=1,
            candidate_version_hash=candidate["candidate_hash"],
            approved_candidate_version_hash=candidate["candidate_hash"],
            tool_specs=candidate["tools"],
            reviews=candidate["reviews"],
        )


def test_activation_applies_signed_revocation_and_blocks_execution(monkeypatch):
    candidate = _candidate_payload()
    entry = validate_candidate_registration(
        candidate["workflow"],
        candidate["workflow_hash"],
        "sha256:" + "d" * 64,
        candidate_id="candidate-revoke-1",
        candidate_version=1,
        candidate_version_hash=candidate["candidate_hash"],
        approved_candidate_version_hash=candidate["candidate_hash"],
        tool_specs=candidate["tools"],
        reviews=candidate["reviews"],
    )
    entry = build_registry_status_entry(
        entry,
        "REVOKED",
        reason="scientific_contract_retired",
    )
    private_key, public_key = _signing_keys()
    signed = build_signed_registry_snapshot(
        [entry],
        private_key,
        "registry-key",
        epoch="2026-07-21.revoked",
    )

    monkeypatch.setattr(registry_module, "_SNAPSHOT", registry_module._SNAPSHOT)
    monkeypatch.setattr(
        registry_module,
        "_WORKFLOWS_BY_IDENTITY",
        registry_module._WORKFLOWS_BY_IDENTITY,
    )
    monkeypatch.setattr(
        registry_module,
        "_WORKFLOWS_BY_ID",
        registry_module._WORKFLOWS_BY_ID,
    )
    monkeypatch.setattr(
        registry_module,
        "_TOOLS_BY_ENTRYPOINT",
        registry_module._TOOLS_BY_ENTRYPOINT,
    )
    monkeypatch.setattr(registry_module, "_REGISTRY_LOOKUP_STARTED", False)
    monkeypatch.setattr(registry_module, "_REGISTRY_ACTIVATED", False)
    monkeypatch.setattr(
        registry_module,
        "_ACTIVE_RELEASE_TRUST",
        registry_module._ACTIVE_RELEASE_TRUST,
    )
    activated = activate_verified_registry_release(
        signed,
        {"registry-key": public_key},
    )
    assert next(
        workflow
        for workflow in activated.workflows
        if workflow.workflow_id == UNION3_REPRODUCTION_WORKFLOW_ID
    ).state == "REVOKED"
    with pytest.raises(WorkflowRegistryError, match="workflow_revoked"):
        assert_workflow_executable(UNION3_REPRODUCTION_WORKFLOW_ID)
    assert UNION3_REPRODUCTION_WORKFLOW_ID not in {
        item["workflow_id"] for item in list_formal_workflows()
    }


def test_unversioned_lookup_prefers_the_numerically_highest_version():
    # Regression (2026-07-23 review): plain string sort ranked "1.10.0"
    # below "1.2.0", so the unversioned catalog lookup returned the older
    # inactive version. Only display/lookup order was affected — the
    # executability gates key on exact (id, version) — but the catalog
    # should still surface the newest version.
    from app.services.workflow_registry_v2 import _version_sort_key

    versions = ["1.2.0", "1.10.0", "1.9.9"]
    assert max(versions, key=_version_sort_key) == "1.10.0"
    assert sorted(versions, key=_version_sort_key) == ["1.2.0", "1.9.9", "1.10.0"]
