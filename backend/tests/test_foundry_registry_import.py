"""Protected signed Registry import is exact, append-only, and non-activating."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.models.foundry_records import (
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryFormalBuildAttestation,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)
from app.models.schemas import User
from app.auth import create_access_token
from app.config import settings
from app.services.evidence_pack_v2 import jcs_canonicalize
from app.services.foundry_catalog import (
    FoundryCatalogError,
    change_registered_candidate_status,
    sha256_json,
)
from app.services.foundry_registry_import import (
    RegistryReleaseImportError,
    export_pending_registry_release_request,
    reconcile_active_registry_projection,
    record_signed_registry_release_import,
)
from app.services.foundry_registry_dispatch import (
    FoundryRegistryDispatchError,
    dispatch_registry_release,
)
from app.services.workflow_registry_v2 import (
    builtin_registry_identity,
    build_registry_status_entry,
    build_signed_registry_snapshot,
    load_verified_registry_release,
    registry_snapshot,
    validate_candidate_registration,
)


FORMAL_IMAGE = "sha256:" + "b" * 64
CANDIDATE_HASH = "c" * 64


def _keys() -> tuple[str, str]:
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


def _formal_entry(candidate_id: uuid.UUID) -> tuple[dict, dict]:
    snapshot = registry_snapshot()
    workflow = copy.deepcopy(
        next(
            item
            for item in snapshot["workflows"]
            if item["workflow_id"] == "union3_flat_lcdm_sn_only_v1"
        )
    )
    workflow["workflow_id"] = "candidate.registry_import_test.v1"
    workflow["display_name"] = "Registry import test"
    referenced = {
        (node["tool_id"], node["tool_version"])
        for node in workflow["tool_dag"]
    }
    tools = [
        copy.deepcopy(item)
        for item in snapshot["tools"]
        if (item["tool_id"], item["version"]) in referenced
    ]
    workflow_hash = "sha256:" + hashlib.sha256(
        jcs_canonicalize(workflow)
    ).hexdigest()
    reviews = [
        {
            "reviewer_id": "engineer-1",
            "reviewer_type": "human",
            "review_role": "engineering",
            "decision": "APPROVED",
            "candidate_version_hash": CANDIDATE_HASH,
        },
        {
            "reviewer_id": "scientist-1",
            "reviewer_type": "human",
            "review_role": "scientific",
            "decision": "APPROVED",
            "candidate_version_hash": CANDIDATE_HASH,
        },
    ]
    entry = validate_candidate_registration(
        workflow,
        workflow_hash,
        FORMAL_IMAGE,
        candidate_id=str(candidate_id),
        candidate_version=1,
        candidate_version_hash=CANDIDATE_HASH,
        approved_candidate_version_hash=CANDIDATE_HASH,
        tool_specs=tools,
        reviews=reviews,
    )
    return entry, workflow


async def _catalog_rows(db_session, entry: dict, workflow: dict):
    candidate_id = uuid.UUID(entry["candidate_id"])
    candidate = FoundryCandidate(
        id=candidate_id,
        gap_fingerprint="1" * 64,
        gap_code="registry_import_test",
        gap_descriptor={"gap_code": "registry_import_test"},
        status="APPROVED",
        risk_level="R2",
        generation_route="COMPOSITION",
        current_version_number=1,
    )
    version = FoundryCandidateVersion(
        candidate_id=candidate_id,
        candidate_key="candidate.registry_import_test.v1",
        version_number=1,
        version_hash=CANDIDATE_HASH,
        workflow_id=workflow["workflow_id"],
        workflow_version=workflow["version"],
        candidate_bundle={},
        workflow_spec=workflow,
        workflow_spec_hash=entry["workflow_spec_hash"].removeprefix("sha256:"),
        code_tree_hash="2" * 64,
        patch_hash="3" * 64,
        dependency_lock_hash="4" * 64,
        sbom_hash="5" * 64,
        fixture_hashes=[],
        data_hashes={},
        ai_model="test",
        ai_generation_config={},
        validation_runner_image_digest="sha256:" + "6" * 64,
        created_by_kind="AI_DRAFT",
    )
    db_session.add_all([candidate, version])
    await db_session.flush()
    attestation = FoundryFormalBuildAttestation(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        source_tree_hash=version.code_tree_hash,
        git_commit="7" * 40,
        dependency_lock_hash=version.dependency_lock_hash,
        formal_sbom_hash="8" * 64,
        test_report_hash="9" * 64,
        formal_release_audit_hash="1" * 64,
        formal_release_audit_receipts={"aggregate_receipt_sha256": "1" * 64},
        formal_worker_image_digest=FORMAL_IMAGE,
        github_repository="standard-astro/platform",
        github_workflow_ref=(
            "standard-astro/platform/.github/workflows/"
            "foundry-formal-worker.yml@refs/heads/main"
        ),
        github_workflow_sha="c" * 40,
        oidc_issuer="https://token.actions.githubusercontent.com",
        oidc_subject="repo:test",
        attestation_signing_key_id="formal-build-test-1",
        sigstore_bundle_hash="a" * 64,
        sigstore_verification_record_hash="e" * 64,
        provenance_hash="b" * 64,
        build_metadata={},
        receipt_hash="d" * 64,
        attestation_artifact_hash="f" * 64,
        built_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    db_session.add(attestation)
    await db_session.flush()
    catalog_entry = WorkflowRegistryEntry(
        workflow_id=workflow["workflow_id"],
        workflow_version=workflow["version"],
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        formal_build_attestation_id=attestation.id,
        registry_entry_hash=entry["registry_entry_hash"],
        workflow_spec=workflow,
        release_entry=entry,
        risk_level="R2",
        worker_image_digest=FORMAL_IMAGE,
        status="PENDING_RELEASE",
        registered_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    db_session.add(catalog_entry)
    await db_session.flush()
    return catalog_entry, attestation


def _manifest(
    request_id: uuid.UUID,
    entry: dict,
    *,
    context: dict,
    status_change: dict | None = None,
) -> dict:
    operations = [
        {"operation": "UPSERT_ENTRY", "entry": entry, "context": context}
    ]
    entries = [entry]
    status_changes: list[dict] = []
    if status_change is not None:
        operations.append(
            {
                "operation": "SET_ENTRY_STATUS",
                "status_change": status_change,
                "context": context,
            }
        )
        status_changes.append(status_change)
    base = builtin_registry_identity()
    return {
        "schema_version": "standard_astro_registry_release_request_v1",
        "request_id": str(request_id),
        "request_kind": "REGISTER_CANDIDATE",
        "request_epoch": f"pending.{request_id.hex}",
        "request_status": "PENDING_SIGNATURE",
        "requested_at": "2026-07-21T12:00:00+00:00",
        "requested_by_actor_hash": "sha256:" + "a" * 64,
        "base_registry_epoch": base["registry_epoch"],
        "base_registry_hash": base["registry_hash"],
        "previous_request_hash": None,
        "new_operations": operations,
        "operation_sequence": operations,
        "operation_sequence_hash": "sha256:" + sha256_json(operations),
        "entries": entries,
        "status_changes": status_changes,
        "context": context,
        "runtime_registry_modified": False,
        "signature_required": True,
    }


def _successor_manifest(
    prior: WorkflowRegistryRelease,
    *,
    new_operations: list[dict],
    entries: list[dict] | None = None,
    status_changes: list[dict] | None = None,
    context: dict,
) -> dict:
    request_id = uuid.uuid4()
    operations = list(prior.manifest["operation_sequence"]) + new_operations
    return {
        "schema_version": "standard_astro_registry_release_request_v1",
        "request_id": str(request_id),
        "request_kind": "REGISTRY_SUCCESSOR",
        "request_epoch": f"pending.{request_id.hex}",
        "request_status": "PENDING_SIGNATURE",
        "requested_at": "2026-07-21T12:05:00+00:00",
        "requested_by_actor_hash": "sha256:" + "e" * 64,
        "base_registry_epoch": prior.manifest["base_registry_epoch"],
        "base_registry_hash": prior.manifest["base_registry_hash"],
        "previous_request_hash": prior.manifest_hash,
        "new_operations": new_operations,
        "operation_sequence": operations,
        "operation_sequence_hash": "sha256:" + sha256_json(operations),
        "entries": entries or [],
        "status_changes": status_changes or [],
        "context": context,
        "runtime_registry_modified": False,
        "signature_required": True,
    }


async def _pending_request(db_session, manifest: dict) -> WorkflowRegistryRelease:
    row = WorkflowRegistryRelease(
        id=uuid.UUID(manifest["request_id"]),
        epoch=manifest["request_epoch"],
        status="PENDING_SIGNATURE",
        manifest=manifest,
        manifest_hash="sha256:" + sha256_json(manifest),
        signature=None,
        key_id=None,
        public_key_fingerprint=None,
    )
    db_session.add(row)
    await db_session.commit()
    return row


def _receipt(
    request: WorkflowRegistryRelease,
    signed: dict,
    public_key: str,
) -> dict:
    public_raw = base64.b64decode(public_key)
    body = {
        "schema_version": "standard_astro_registry_release_import_v1",
        "release_request_id": str(request.id),
        "release_request_sha256": request.manifest_hash,
        "base_registry_epoch": request.manifest["base_registry_epoch"],
        "base_registry_hash": request.manifest["base_registry_hash"],
        "registry_epoch": signed["payload"]["registry_epoch"],
        "registry_snapshot_sha256": signed["payload_sha256"],
        "signing_key_id": signed["signature"]["key_id"],
        "signing_public_key_sha256": "sha256:"
        + hashlib.sha256(public_raw).hexdigest(),
        "complete_entry_count": len(signed["payload"]["entries"]),
        "generated_at": "2026-07-21T12:00:01Z",
        "import_mode": "protected_offline_registry_release",
        "signed_snapshot": signed,
    }
    body["receipt_sha256"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(body)
    ).hexdigest()
    return body


async def _case(db_session, *, status: str | None = None):
    candidate_id = uuid.uuid4()
    entry, workflow = _formal_entry(candidate_id)
    catalog_entry, attestation = await _catalog_rows(db_session, entry, workflow)
    status_change = None
    signed_entry = entry
    if status is not None:
        status_change = {
            "registry_entry_id": str(catalog_entry.id),
            "registry_entry_hash": entry["registry_entry_hash"],
            "workflow_id": workflow["workflow_id"],
            "workflow_version": workflow["version"],
            "requested_status": status,
            "reason": "scientific contract retired",
        }
        signed_entry = build_registry_status_entry(
            entry,
            status,  # type: ignore[arg-type]
            reason=status_change["reason"],
        )
    request_id = uuid.uuid4()
    context = {
        "candidate_db_id": str(catalog_entry.candidate_id),
        "candidate_version_db_id": str(catalog_entry.candidate_version_id),
        "formal_build_attestation_id": str(attestation.id),
        "formal_build_attestation_receipt_sha256": attestation.receipt_hash,
        "formal_build_attestation_artifact_sha256": (
            attestation.attestation_artifact_hash
        ),
        "formal_build_git_commit": attestation.git_commit,
        "formal_build_sbom_sha256": attestation.formal_sbom_hash,
        "formal_build_test_report_sha256": attestation.test_report_hash,
        "formal_build_release_audit_sha256": (
            attestation.formal_release_audit_hash
        ),
        "formal_build_release_audit_receipts": (
            attestation.formal_release_audit_receipts
        ),
        "formal_build_sigstore_bundle_sha256": attestation.sigstore_bundle_hash,
        "formal_build_provenance_sha256": attestation.provenance_hash,
    }
    manifest = _manifest(
        request_id,
        entry,
        context=context,
        status_change=status_change,
    )
    request = await _pending_request(db_session, manifest)
    private_key, public_key = _keys()
    signed = build_signed_registry_snapshot(
        [signed_entry],
        private_key,
        "registry-test-key",
        epoch=f"foundry.{request_id.hex}.1",
        operation_sequence=len(manifest["operation_sequence"]),
    )
    receipt = _receipt(request, signed, public_key)
    return request, receipt, public_key


@pytest.mark.asyncio
async def test_signed_import_is_append_only_idempotent_and_does_not_activate(db_session):
    request, receipt, public_key = await _case(db_session)
    before = registry_snapshot()
    row, created = await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    assert created is True
    assert row.status == "SIGNED_READY_FOR_DEPLOYMENT"
    assert row.runtime_registry_modified is False
    assert registry_snapshot() == before

    replay, created = await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    assert replay.id == row.id
    assert created is False
    assert request.status == "PENDING_SIGNATURE"

    row.status = "REGISTERED"
    with pytest.raises(ValueError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        # Pin the exact gate each forgery dies on, so a refactor cannot
        # silently swap e.g. the signature check for a mere hash check.
        ("unsigned", "registry_snapshot_signature"),
        ("wrong_key", "registry_snapshot_signature_invalid"),
        ("wrong_request", "registry_release_request_not_found"),
    ],
)
async def test_import_rejects_unsigned_wrong_key_and_wrong_request(
    db_session, failure, expected_code
):
    _request, receipt, public_key = await _case(db_session)
    trusted = {"registry-test-key": public_key}
    forged = copy.deepcopy(receipt)
    if failure == "unsigned":
        forged["signed_snapshot"].pop("signature")
    elif failure == "wrong_key":
        _other_private, other_public = _keys()
        trusted = {"registry-test-key": other_public}
    else:
        forged["release_request_id"] = str(uuid.uuid4())
    unsigned_body = {key: value for key, value in forged.items() if key != "receipt_sha256"}
    forged["receipt_sha256"] = "sha256:" + hashlib.sha256(
        jcs_canonicalize(unsigned_body)
    ).hexdigest()
    with pytest.raises(RegistryReleaseImportError, match=expected_code):
        await record_signed_registry_release_import(
            db_session,
            receipt=forged,
            trusted_public_keys=trusted,
        )


@pytest.mark.asyncio
async def test_import_replays_and_checks_the_complete_status_delta(db_session):
    _request, receipt, public_key = await _case(db_session, status="REVOKED")
    row, created = await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    assert created is True
    assert row.signed_snapshot["payload"]["entries"][0]["workflow"]["state"] == (
        "REVOKED"
    )


@pytest.mark.asyncio
async def test_import_rejects_signed_status_that_differs_from_request(db_session):
    request, receipt, public_key = await _case(db_session, status="REVOKED")
    entry = request.manifest["entries"][0]
    private_key, _matching_public = _keys()
    wrong_signed = build_signed_registry_snapshot(
        [entry],
        private_key,
        "other-key",
        epoch="foundry.wrong-status.1",
        operation_sequence=len(request.manifest["operation_sequence"]),
    )
    # Trust the new key so this test reaches the semantic status-delta gate.
    private_raw = base64.b64decode(private_key)
    private = Ed25519PrivateKey.from_private_bytes(private_raw)
    wrong_public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    wrong_public = base64.b64encode(wrong_public_raw).decode("ascii")
    forged = _receipt(request, wrong_signed, wrong_public)
    with pytest.raises(
        RegistryReleaseImportError,
        match="registry_release_signed_entries_mismatch",
    ):
        await record_signed_registry_release_import(
            db_session,
            receipt=forged,
            trusted_public_keys={"other-key": wrong_public},
        )


@pytest.mark.asyncio
async def test_same_request_with_different_valid_snapshot_is_conflict(db_session):
    request, receipt, public_key = await _case(db_session)
    await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    private_key, second_public = _keys()
    second = build_signed_registry_snapshot(
        request.manifest["entries"],
        private_key,
        "registry-second-key",
        epoch="foundry.second-valid-snapshot.1",
        operation_sequence=1,
    )
    conflicting = _receipt(request, second, second_public)
    with pytest.raises(
        RegistryReleaseImportError, match="registry_release_import_conflict"
    ):
        await record_signed_registry_release_import(
            db_session,
            receipt=conflicting,
            trusted_public_keys={"registry-second-key": second_public},
        )


@pytest.mark.asyncio
async def test_offline_signer_receipt_crosses_the_protected_import_gate(
    db_session, tmp_path
):
    request, _receipt_from_runtime_helper, _public = await _case(db_session)
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_foundry_registry_release.py"
    spec = importlib.util.spec_from_file_location("foundry_registry_signer", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    private_key, public_key = _keys()
    request_path = tmp_path / "pending-release-request.json"
    private_path = tmp_path / "registry.key"
    request_path.write_text(json.dumps(request.manifest), encoding="utf-8")
    private_path.write_text(private_key, encoding="utf-8")
    args = argparse.Namespace(
        request=str(request_path),
        request_sha256=request.manifest_hash,
        private_key_file=str(private_path),
        key_id="registry-offline-key",
        registry_epoch=f"foundry.{request.id.hex}.offline",
    )
    _signed, signer_receipt = module.build_release(args)
    assert jcs_canonicalize(
        signer_receipt["signed_snapshot"]["payload"]["entries"]
    ) == jcs_canonicalize(json.loads(json.dumps(request.manifest["entries"])))
    row, created = await record_signed_registry_release_import(
        db_session,
        receipt=signer_receipt,
        trusted_public_keys={"registry-offline-key": public_key},
    )
    assert created is True
    assert row.receipt_hash == signer_receipt["receipt_sha256"]
    assert row.status == "SIGNED_READY_FOR_DEPLOYMENT"


@pytest.mark.asyncio
async def test_internal_import_endpoint_uses_dedicated_secret_and_forbids_extras(
    db_session, app_client, monkeypatch
):
    _request, receipt, public_key = await _case(db_session)
    callback_secret = "registry-import-callback-secret-32-bytes"
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_registry_import_result_secret", callback_secret
    )
    monkeypatch.setattr(
        settings,
        "workflow_registry_verification_keys",
        json.dumps({"registry-test-key": public_key}),
    )

    unauthorized = await app_client.post(
        "/api/internal/foundry/registry-releases/import", json=receipt
    )
    assert unauthorized.status_code == 403

    forged_shape = {**receipt, "candidate_claim": "must never enter this API"}
    invalid = await app_client.post(
        "/api/internal/foundry/registry-releases/import",
        json=forged_shape,
        headers={"Authorization": f"Bearer {callback_secret}"},
    )
    assert invalid.status_code == 422

    accepted = await app_client.post(
        "/api/internal/foundry/registry-releases/import",
        json=receipt,
        headers={"Authorization": f"Bearer {callback_secret}"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "SIGNED_READY_FOR_DEPLOYMENT"
    assert accepted.json()["runtime_registry_modified"] is False


@pytest.mark.asyncio
async def test_registry_export_is_exact_private_head_only_and_replayable(
    db_session, app_client, monkeypatch
):
    request, _receipt_value, _public_key = await _case(db_session)
    export_secret = "registry-export-read-secret-32-bytes"
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(settings, "foundry_registry_export_secret", export_secret)
    endpoint = f"/api/internal/foundry/registry-releases/{request.id}/export"

    unauthorized = await app_client.get(
        endpoint,
        params={"release_request_hash": request.manifest_hash},
    )
    assert unauthorized.status_code == 403
    wrong_hash = await app_client.get(
        endpoint,
        params={"release_request_hash": "sha256:" + "0" * 64},
        headers={"Authorization": f"Bearer {export_secret}"},
    )
    assert wrong_hash.status_code == 409

    first = await app_client.get(
        endpoint,
        params={"release_request_hash": request.manifest_hash},
        headers={"Authorization": f"Bearer {export_secret}"},
    )
    replay = await app_client.get(
        endpoint,
        params={"release_request_hash": request.manifest_hash},
        headers={"Authorization": f"Bearer {export_secret}"},
    )
    assert first.status_code == 200
    assert replay.json() == first.json()
    exported = first.json()
    assert set(exported) == {
        "schema_version",
        "release_request_id",
        "release_request_sha256",
        "pending_release_request",
    }
    assert jcs_canonicalize(exported["pending_release_request"]) == (
        jcs_canonicalize(request.manifest)
    )
    serialized = json.dumps(exported, sort_keys=True)
    assert "requested_by_user_id" not in serialized
    assert "current_signed_registry" not in serialized
    assert "signed_snapshot" not in serialized

    context = dict(request.manifest["context"])
    new_operation = {
        "operation": "UPSERT_ENTRY",
        "entry": request.manifest["entries"][0],
        "context": context,
    }
    successor = await _pending_request(
        db_session,
        _successor_manifest(
            request,
            new_operations=[new_operation],
            entries=[request.manifest["entries"][0]],
            context=context,
        ),
    )
    stale = await app_client.get(
        endpoint,
        params={"release_request_hash": request.manifest_hash},
        headers={"Authorization": f"Bearer {export_secret}"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_class"] == (
        "registry_release_request_not_chain_head"
    )
    current = await export_pending_registry_release_request(
        db_session,
        release_request_id=successor.id,
        release_request_hash=successor.manifest_hash,
    )
    assert current["release_request_id"] == str(successor.id)


@pytest.mark.asyncio
async def test_first_import_of_a_stale_release_request_is_rejected(db_session):
    request, receipt, public_key = await _case(db_session)
    context = dict(request.manifest["context"])
    operation = {
        "operation": "UPSERT_ENTRY",
        "entry": request.manifest["entries"][0],
        "context": context,
    }
    await _pending_request(
        db_session,
        _successor_manifest(
            request,
            new_operations=[operation],
            entries=[request.manifest["entries"][0]],
            context=context,
        ),
    )
    with pytest.raises(
        RegistryReleaseImportError,
        match="registry_release_request_not_chain_head",
    ):
        await record_signed_registry_release_import(
            db_session,
            receipt=receipt,
            trusted_public_keys={"registry-test-key": public_key},
        )


@pytest.mark.asyncio
async def test_fixed_base_register_suspend_revoke_import_chain_deploys_each_state(
    db_session,
):
    request, receipt, public_key = await _case(db_session)
    await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    first_runtime = load_verified_registry_release(
        receipt["signed_snapshot"],
        {"registry-test-key": public_key},
    )
    identity = (
        request.manifest["entries"][0]["workflow"]["workflow_id"],
        request.manifest["entries"][0]["workflow"]["version"],
    )
    first_workflow = next(
        workflow
        for workflow in first_runtime.workflows
        if (workflow.workflow_id, workflow.version) == identity
    )
    assert first_workflow.state == "REGISTERED"

    catalog_entry = await db_session.scalar(
        select(WorkflowRegistryEntry).where(
            WorkflowRegistryEntry.workflow_id == identity[0],
            WorkflowRegistryEntry.workflow_version == identity[1],
        )
    )
    candidate = await db_session.get(FoundryCandidate, catalog_entry.candidate_id)
    actor = User(
        id=uuid.uuid4(),
        username="registry_status_actor",
        email="registry-status-actor@astro.example",
        password_hash="unused",
        subscription_tier="admin",
    )
    db_session.add(actor)
    catalog_entry.status = "REGISTERED"
    candidate.status = "PROMOTED"
    await db_session.commit()

    _candidate, _entry, suspend_request = await change_registered_candidate_status(
        db_session,
        candidate_id=candidate.id,
        target_status="SUSPENDED",
        reason="temporary scientific review",
        actor_user_id=actor.id,
    )
    suspended_entry = build_registry_status_entry(
        request.manifest["entries"][0],
        "SUSPENDED",
        reason="temporary scientific review",
    )
    second_private, second_public = _keys()
    second_signed = build_signed_registry_snapshot(
        [suspended_entry],
        second_private,
        "registry-second-release-key",
        epoch=f"foundry.{suspend_request.id.hex}.2",
        operation_sequence=len(suspend_request.manifest["operation_sequence"]),
    )
    second_receipt = _receipt(suspend_request, second_signed, second_public)
    await record_signed_registry_release_import(
        db_session,
        receipt=second_receipt,
        trusted_public_keys={"registry-second-release-key": second_public},
    )
    second_runtime = load_verified_registry_release(
        second_signed,
        {"registry-second-release-key": second_public},
    )
    second_workflow = next(
        workflow
        for workflow in second_runtime.workflows
        if (workflow.workflow_id, workflow.version) == identity
    )
    assert second_workflow.state == "SUSPENDED"
    assert second_signed["payload"]["base_registry_epoch"] == (
        receipt["signed_snapshot"]["payload"]["base_registry_epoch"]
    )
    assert second_signed["payload"]["operation_sequence"] == 2

    catalog_entry.status = "SUSPENDED"
    candidate.status = "SUSPENDED"
    await db_session.commit()
    _candidate, _entry, revoke_request = await change_registered_candidate_status(
        db_session,
        candidate_id=candidate.id,
        target_status="REVOKED",
        reason="registered contract retired",
        actor_user_id=actor.id,
    )
    revoked_entry = build_registry_status_entry(
        suspended_entry,
        "REVOKED",
        reason="registered contract retired",
    )
    third_private, third_public = _keys()
    third_signed = build_signed_registry_snapshot(
        [revoked_entry],
        third_private,
        "registry-third-release-key",
        epoch=f"foundry.{revoke_request.id.hex}.3",
        operation_sequence=len(revoke_request.manifest["operation_sequence"]),
    )
    third_receipt = _receipt(revoke_request, third_signed, third_public)
    await record_signed_registry_release_import(
        db_session,
        receipt=third_receipt,
        trusted_public_keys={"registry-third-release-key": third_public},
    )
    third_runtime = load_verified_registry_release(
        third_signed,
        {"registry-third-release-key": third_public},
    )
    third_workflow = next(
        workflow
        for workflow in third_runtime.workflows
        if (workflow.workflow_id, workflow.version) == identity
    )
    assert third_workflow.state == "REVOKED"
    assert revoke_request.manifest["previous_request_hash"] == (
        suspend_request.manifest_hash
    )
    assert third_signed["payload"]["operation_sequence"] == 3
    imports = list(
        (await db_session.execute(select(WorkflowRegistryReleaseImport)))
        .scalars()
        .all()
    )
    assert len(imports) == 3


@pytest.mark.asyncio
async def test_pending_status_transition_is_checked_against_chain_head(db_session):
    _request, _receipt_value, _public_key = await _case(db_session)
    catalog_entry = await db_session.scalar(select(WorkflowRegistryEntry))
    candidate = await db_session.get(FoundryCandidate, catalog_entry.candidate_id)
    actor = User(
        id=uuid.uuid4(),
        username="registry_pending_status_actor",
        email="registry-pending-status-actor@astro.example",
        password_hash="unused",
        subscription_tier="admin",
    )
    db_session.add(actor)
    catalog_entry.status = "REGISTERED"
    candidate.status = "PROMOTED"
    await db_session.commit()

    await change_registered_candidate_status(
        db_session,
        candidate_id=candidate.id,
        target_status="SUSPENDED",
        reason="temporary safety hold",
        actor_user_id=actor.id,
    )
    with pytest.raises(
        FoundryCatalogError,
        match="request-chain head already makes this status transition invalid",
    ) as exc_info:
        await change_registered_candidate_status(
            db_session,
            candidate_id=candidate.id,
            target_status="SUSPENDED",
            reason="duplicate safety hold",
            actor_user_id=actor.id,
        )
    assert exc_info.value.error_class == "registry_status_transition_pending"


@pytest.mark.asyncio
async def test_restart_reconciliation_projects_only_the_active_signed_release(
    db_session, monkeypatch
):
    request, receipt, public_key = await _case(db_session)
    await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    catalog_entry = await db_session.scalar(select(WorkflowRegistryEntry))
    candidate = await db_session.get(FoundryCandidate, catalog_entry.candidate_id)

    before_restart = await reconcile_active_registry_projection(
        db_session,
        trusted_public_keys={"registry-test-key": public_key},
    )
    await db_session.refresh(catalog_entry)
    await db_session.refresh(candidate)
    assert before_restart["status"] == "BUILTIN_REGISTRY_ACTIVE"
    assert catalog_entry.status == "PENDING_RELEASE"
    assert candidate.status == "APPROVED"

    loaded = load_verified_registry_release(
        receipt["signed_snapshot"],
        {"registry-test-key": public_key},
    )
    workflow_id = request.manifest["entries"][0]["workflow"]["workflow_id"]
    workflow_version = request.manifest["entries"][0]["workflow"]["version"]
    entry_key = f"{workflow_id}@{workflow_version}"
    monkeypatch.setattr(
        "app.services.foundry_registry_import.active_registry_identity",
        lambda: {
            "schema_version": loaded.schema_version,
            "registry_epoch": loaded.epoch,
            "registry_hash": loaded.registry_hash,
            "status": loaded.status,
            "release_kind": "signed_registry_release",
            "signature_algorithm": "ed25519",
            "signature_key_id": "registry-test-key",
            "signed_payload_sha256": receipt["signed_snapshot"]["payload_sha256"],
        },
    )
    monkeypatch.setattr(
        "app.services.foundry_registry_import.list_formal_workflows",
        lambda **_kwargs: [
            {
                "workflow_id": workflow_id,
                "workflow_version": workflow_version,
                "state": "REGISTERED",
                "registry_entry_hash": loaded.workflow_entry_hashes[entry_key],
                "registry_epoch": loaded.epoch,
            }
        ],
    )
    after_restart = await reconcile_active_registry_projection(
        db_session,
        trusted_public_keys={"registry-test-key": public_key},
    )
    await db_session.refresh(catalog_entry)
    await db_session.refresh(candidate)
    assert after_restart["updated_entries"] == 1
    assert catalog_entry.status == "REGISTERED"
    assert candidate.status == "PROMOTED"
    events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.event_type
                    == "REGISTRY_RUNTIME_RECONCILED"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    replay = await reconcile_active_registry_projection(
        db_session,
        trusted_public_keys={"registry-test-key": public_key},
    )
    assert replay["updated_entries"] == 0


@pytest.mark.asyncio
async def test_admin_registry_view_requires_admin_and_redacts_release_material(
    db_session, app_client, test_user, monkeypatch
):
    user, token = test_user
    request, receipt, public_key = await _case(db_session)
    await record_signed_registry_release_import(
        db_session,
        receipt=receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    monkeypatch.setattr(settings, "admin_secret", "configured-admin-secret")

    forbidden = await app_client.get(
        "/api/admin/foundry/registry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403
    user.subscription_tier = "admin"
    await db_session.commit()
    admin_token = create_access_token(user.id)
    response = await app_client.get(
        "/api/admin/foundry/registry",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"runtime", "pending_entries", "releases"}
    assert set(payload["runtime"]) == {
        "registry_epoch",
        "registry_hash",
        "release_kind",
        "signing_key_id",
        "entries",
    }
    release = next(item for item in payload["releases"] if item["id"] == str(request.id))
    assert set(release) == {
        "id",
        "epoch",
        "status",
        "manifest_hash",
        "key_id",
        "public_key_fingerprint",
        "created_at",
        "signed_import",
    }
    assert set(release["signed_import"]) == {
        "registry_epoch",
        "registry_snapshot_hash",
        "signing_key_id",
        "signing_public_key_fingerprint",
        "status",
        "runtime_registry_modified",
        "imported_at",
    }
    def _all_keys(value):
        if isinstance(value, dict):
            return set(value) | {
                key
                for item in value.values()
                for key in _all_keys(item)
            }
        if isinstance(value, list):
            return {key for item in value for key in _all_keys(item)}
        return set()

    response_keys = _all_keys(payload)
    for forbidden_key in (
        "signed_snapshot",
        "signature",
        "manifest",
        "workflow_spec",
        "created_by_user_id",
        "requested_by_user_id",
    ):
        assert forbidden_key not in response_keys


@pytest.mark.asyncio
async def test_registry_dispatch_sends_only_exact_request_binding_to_main(
    monkeypatch,
):
    captured = {}

    class _Response:
        status_code = 204

    class _Client:
        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return _Response()

    request_id = uuid.uuid4()
    request_hash = "sha256:" + "f" * 64
    monkeypatch.setattr(settings, "foundry_registry_dispatch_backend", "github_actions")
    monkeypatch.setattr(
        settings, "foundry_registry_github_repository", "standard-astro/platform"
    )
    monkeypatch.setattr(
        settings,
        "foundry_registry_github_workflow",
        "foundry-registry-release.yml",
    )
    monkeypatch.setattr(settings, "foundry_registry_github_ref", "main")
    monkeypatch.setattr(
        settings, "foundry_registry_github_token", "github-registry-" + "x" * 40
    )
    await dispatch_registry_release(
        release_request_id=request_id,
        release_request_hash=request_hash,
        client=_Client(),
    )
    assert captured["json"] == {
        "ref": "main",
        "inputs": {
            "release_request_id": str(request_id),
            "release_request_sha256": request_hash,
        },
    }
    assert set(captured["json"]["inputs"]) == {
        "release_request_id",
        "release_request_sha256",
    }
    assert captured["headers"]["Authorization"].startswith("Bearer ")

    monkeypatch.setattr(settings, "foundry_registry_github_ref", "f" * 40)
    with pytest.raises(
        FoundryRegistryDispatchError,
        match="registry_dispatch_misconfigured",
    ):
        await dispatch_registry_release(
            release_request_id=request_id,
            release_request_hash=request_hash,
            client=_Client(),
        )


@pytest.mark.asyncio
async def test_post_commit_release_dispatch_is_durable_and_idempotent(
    db_session, monkeypatch
):
    from app.api.foundry import _dispatch_pending_registry_release

    request, _receipt_value, _public_key = await _case(db_session)
    entry = await db_session.scalar(select(WorkflowRegistryEntry))
    calls = []

    async def _dispatch(**binding):
        calls.append(binding)

    monkeypatch.setattr(settings, "foundry_registry_dispatch_backend", "github_actions")
    monkeypatch.setattr(
        "app.services.foundry_registry_dispatch.dispatch_registry_release",
        _dispatch,
    )
    first = await _dispatch_pending_registry_release(
        db=db_session,
        candidate_id=entry.candidate_id,
        candidate_version_id=entry.candidate_version_id,
        release=request,
    )
    second = await _dispatch_pending_registry_release(
        db=db_session,
        candidate_id=entry.candidate_id,
        candidate_version_id=entry.candidate_version_id,
        release=request,
    )
    assert first == {"status": "DISPATCHED", "idempotent_replay": False}
    assert second == {"status": "DISPATCHED", "idempotent_replay": True}
    assert calls == [
        {
            "release_request_id": request.id,
            "release_request_hash": request.manifest_hash,
        }
    ]
    events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.event_type
                    == "REGISTRY_RELEASE_DISPATCHED"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
