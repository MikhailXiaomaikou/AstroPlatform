"""Registry import-to-activation delivery is boot-only and append-only."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.foundry_activation_records import (
    WorkflowRegistryActivationReceipt,
)
from app.models.foundry_records import (
    FoundryCandidate,
    WorkflowRegistryEntry,
    WorkflowRegistryReleaseImport,
)
from app.config import settings
from app.services.foundry_registry_activation import (
    RegistryActivationError,
    assert_persisted_activation_not_rollback,
    confirm_registry_activation,
    export_verified_activation_material,
    preflight_registry_activation,
    read_packaged_activation,
    registry_activation_readiness,
)
from app.services.foundry_registry_import import (
    record_signed_registry_release_import,
)
from scripts.prepare_foundry_registry_activation import prepare
from tests.test_foundry_registry_import import (
    _case,
    _mark_candidate_bundle_legacy_invalid,
    _pending_request,
    _successor_manifest,
)


COMMIT = "a" * 40


def _render_blueprint(path: Path) -> None:
    path.write_text(
        """services:
  - type: web
    name: standard-astro-backend
    autoDeployTrigger: off
    envVars: []
  - type: worker
    name: standard-astro-celery-worker
    autoDeployTrigger: off
    envVars: []
  - type: worker
    name: standard-astro-celery-beat
    autoDeployTrigger: off
    envVars: []
""",
        encoding="utf-8",
    )


async def _prepared_case(db_session, tmp_path: Path):
    request, import_receipt, public_key = await _case(db_session)
    imported, created = await record_signed_registry_release_import(
        db_session,
        receipt=import_receipt,
        trusted_public_keys={"registry-test-key": public_key},
    )
    assert created is True
    export = await export_verified_activation_material(
        db_session,
        release_request_id=request.id,
        release_request_hash=request.manifest_hash,
        trusted_public_keys={"registry-test-key": public_key},
    )
    export_path = tmp_path / "activation-export.json"
    export_path.write_text(
        json.dumps(export, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    blueprint = tmp_path / "render.yaml"
    _render_blueprint(blueprint)
    output = tmp_path / "registry_releases"
    result = prepare(
        argparse.Namespace(
            export=export_path,
            output_dir=output,
            prepared_from_git_commit=COMMIT,
            render_blueprint=blueprint,
        )
    )
    return request, import_receipt, imported, output, result


@pytest.mark.asyncio
async def test_legacy_signed_import_fails_current_policy_at_activation_boundaries(
    db_session,
    tmp_path,
):
    request, _receipt, imported, output, _result = await _prepared_case(
        db_session,
        tmp_path,
    )
    _legacy_version = await _mark_candidate_bundle_legacy_invalid(
        db_session, request
    )

    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_candidate_policy_invalid",
    ):
        await export_verified_activation_material(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            trusted_public_keys={
                imported.signing_key_id: (
                    read_packaged_activation(
                        output / "activation-manifest.json"
                    ).trusted_keyring[imported.signing_key_id]
                )
            },
        )

    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_candidate_policy_invalid",
    ):
        await preflight_registry_activation(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            registry_snapshot_hash=imported.registry_snapshot_hash,
            target_git_commit=COMMIT,
        )

    packaged = read_packaged_activation(output / "activation-manifest.json")
    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_candidate_policy_invalid",
    ):
        await assert_persisted_activation_not_rollback(
            db_session,
            packaged=packaged,
        )


@pytest.mark.asyncio
async def test_prepare_public_bundle_does_not_activate_import(db_session, tmp_path):
    request, _receipt, imported, output, result = await _prepared_case(
        db_session, tmp_path
    )
    packaged = read_packaged_activation(output / "activation-manifest.json")

    assert result["release_request_id"] == str(request.id)
    assert packaged.manifest["registry_release_import_id"] == str(imported.id)
    assert packaged.manifest["runtime_activation_mode"] == "process_boot_only"
    assert set(path.name for path in output.iterdir()) == {
        "activation-manifest.json",
        "active-signed-registry.json",
        "trusted-registry-keyring.json",
    }
    assert await db_session.scalar(
        select(WorkflowRegistryActivationReceipt)
    ) is None


@pytest.mark.asyncio
async def test_public_bundle_tampering_fails_closed(db_session, tmp_path):
    _request, _receipt, _imported, output, _result = await _prepared_case(
        db_session, tmp_path
    )
    snapshot_path = output / "active-signed-registry.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["payload"]["registry_epoch"] = "forged.epoch"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(
        RegistryActivationError, match="registry_activation_file_hash_mismatch"
    ):
        read_packaged_activation(output / "activation-manifest.json")


@pytest.mark.asyncio
async def test_confirmation_requires_new_exact_process_and_is_append_only(
    db_session, tmp_path, monkeypatch
):
    request, import_receipt, imported, output, prepared = await _prepared_case(
        db_session, tmp_path
    )
    entry = await db_session.scalar(select(WorkflowRegistryEntry))
    candidate = await db_session.get(FoundryCandidate, entry.candidate_id)
    entry.status = "REGISTERED"
    candidate.status = "PROMOTED"
    await db_session.commit()

    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_RELEASE_PATH",
        str(output / "active-signed-registry.json"),
    )
    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH",
        str(output / "trusted-registry-keyring.json"),
    )
    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_ACTIVATION_MANIFEST_PATH",
        str(output / "activation-manifest.json"),
    )
    monkeypatch.delenv("WORKFLOW_REGISTRY_TRUSTED_KEYRING_JSON", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", COMMIT)
    monkeypatch.setattr(
        "app.services.foundry_registry_activation.active_registry_identity",
        lambda: {
            "release_kind": "signed_registry_release",
            "status": "ACTIVE",
            "signature_algorithm": "ed25519",
            "signature_key_id": "registry-test-key",
            "registry_epoch": import_receipt["registry_epoch"],
            "signed_payload_sha256": import_receipt[
                "registry_snapshot_sha256"
            ],
        },
    )

    async def _roles(target_commit: str):
        assert target_commit == COMMIT
        return {
            "backend_commit": COMMIT,
            "control_worker_count": 1,
            "control_workers_match": True,
            "verification_queue": "ready",
            "beat": "ok",
        }

    monkeypatch.setattr(
        "app.services.foundry_registry_activation.probe_activation_control_roles",
        _roles,
    )
    readiness = await registry_activation_readiness(
        db_session,
        release_request_id=request.id,
        release_request_hash=request.manifest_hash,
        target_git_commit=COMMIT,
    )
    assert readiness["status"] == "ACTIVATION_READY"
    assert readiness["projection"]["candidate_status_counts"] == {"PROMOTED": 1}

    deployments = [
        {
            "role": role,
            "service_id_sha256": "sha256:" + character * 64,
            "deploy_id": f"dep-{role}",
            "git_commit": COMMIT,
        }
        for role, character in (
            ("backend", "1"),
            ("control_worker", "2"),
            ("beat", "3"),
        )
    ]
    row, created = await confirm_registry_activation(
        db_session,
        release_request_id=request.id,
        release_request_hash=request.manifest_hash,
        target_git_commit=COMMIT,
        activation_manifest_hash=prepared["activation_manifest_sha256"],
        deployments=deployments,
    )
    assert created is True
    assert row.release_import_id == imported.id
    assert row.runtime_hot_switched is False
    assert row.projection_summary["candidate_status_counts"] == {"PROMOTED": 1}

    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_release_already_active",
    ):
        await preflight_registry_activation(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            registry_snapshot_hash=imported.registry_snapshot_hash,
            target_git_commit=COMMIT,
        )

    replay, created = await confirm_registry_activation(
        db_session,
        release_request_id=request.id,
        release_request_hash=request.manifest_hash,
        target_git_commit=COMMIT,
        activation_manifest_hash=prepared["activation_manifest_sha256"],
        deployments=deployments,
    )
    assert created is False
    assert replay.id == row.id

    conflicting = copy.deepcopy(deployments)
    conflicting[0]["deploy_id"] = "dep-other"
    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_confirmation_conflict",
    ):
        await confirm_registry_activation(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            target_git_commit=COMMIT,
            activation_manifest_hash=prepared["activation_manifest_sha256"],
            deployments=conflicting,
        )

    row.status = "REVOKED"
    with pytest.raises(ValueError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_preflight_and_startup_reject_stale_never_activated_import(
    db_session, tmp_path
):
    request, _receipt, imported, output, _prepared = await _prepared_case(
        db_session, tmp_path
    )
    context = dict(request.manifest["context"])
    operation = {
        "operation": "UPSERT_ENTRY",
        "entry": request.manifest["entries"][0],
        "context": context,
    }
    successor = await _pending_request(
        db_session,
        _successor_manifest(
            request,
            new_operations=[operation],
            entries=[request.manifest["entries"][0]],
            context=context,
        ),
    )
    successor_import = WorkflowRegistryReleaseImport(
        release_request_id=successor.id,
        release_request_hash=successor.manifest_hash,
        base_registry_epoch=imported.base_registry_epoch,
        base_registry_hash=imported.base_registry_hash,
        registry_epoch=f"foundry.{successor.id.hex}.2",
        registry_snapshot_hash="sha256:" + "0" * 64,
        signing_key_id=imported.signing_key_id,
        signing_public_key_fingerprint=imported.signing_public_key_fingerprint,
        signed_snapshot=dict(imported.signed_snapshot),
        receipt_hash="sha256:" + "9" * 64,
        status="SIGNED_READY_FOR_DEPLOYMENT",
        runtime_registry_modified=False,
    )
    db_session.add(successor_import)
    await db_session.commit()

    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_import_not_chain_head",
    ):
        await preflight_registry_activation(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            registry_snapshot_hash=imported.registry_snapshot_hash,
            target_git_commit=COMMIT,
        )
    packaged = read_packaged_activation(output / "activation-manifest.json")
    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_startup_import_stale",
    ):
        await assert_persisted_activation_not_rollback(
            db_session,
            packaged=packaged,
        )

    active_original = WorkflowRegistryActivationReceipt(
        release_import_id=imported.id,
        release_request_id=request.id,
        release_request_hash=request.manifest_hash,
        registry_epoch=imported.registry_epoch,
        registry_snapshot_hash=imported.registry_snapshot_hash,
        activation_manifest_hash="sha256:" + "3" * 64,
        signed_snapshot_file_hash="sha256:" + "2" * 64,
        trusted_keyring_file_hash="sha256:" + "1" * 64,
        target_git_commit=COMMIT,
        deployment_provider="render_api_exact_commit",
        deployment_receipts=[],
        deployment_set_hash="sha256:" + "a" * 64,
        projection_summary={"projection_verified": True},
        status="ACTIVE",
        runtime_hot_switched=False,
        projection_verified=True,
        receipt_hash="sha256:" + "b" * 64,
    )
    db_session.add(active_original)
    await db_session.commit()
    restart = await assert_persisted_activation_not_rollback(
        db_session,
        packaged=packaged,
    )
    assert restart["status"] == "PERSISTED_ACTIVE_RESTART"

    active_successor = WorkflowRegistryActivationReceipt(
        release_import_id=successor_import.id,
        release_request_id=successor.id,
        release_request_hash=successor.manifest_hash,
        registry_epoch=successor_import.registry_epoch,
        registry_snapshot_hash=successor_import.registry_snapshot_hash,
        activation_manifest_hash="sha256:" + "8" * 64,
        signed_snapshot_file_hash="sha256:" + "7" * 64,
        trusted_keyring_file_hash="sha256:" + "6" * 64,
        target_git_commit="b" * 40,
        deployment_provider="render_api_exact_commit",
        deployment_receipts=[],
        deployment_set_hash="sha256:" + "5" * 64,
        projection_summary={"projection_verified": True},
        status="ACTIVE",
        runtime_hot_switched=False,
        projection_verified=True,
        receipt_hash="sha256:" + "4" * 64,
    )
    db_session.add(active_successor)
    await db_session.commit()
    with pytest.raises(
        RegistryActivationError,
        match="registry_activation_startup_rollback_detected",
    ):
        await assert_persisted_activation_not_rollback(
            db_session,
            packaged=packaged,
        )


@pytest.mark.asyncio
async def test_status_rejects_a_different_runtime_commit(
    db_session, tmp_path, monkeypatch
):
    request, _receipt, _imported, output, _prepared = await _prepared_case(
        db_session, tmp_path
    )
    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_RELEASE_PATH",
        str(output / "active-signed-registry.json"),
    )
    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH",
        str(output / "trusted-registry-keyring.json"),
    )
    monkeypatch.setenv(
        "WORKFLOW_REGISTRY_ACTIVATION_MANIFEST_PATH",
        str(output / "activation-manifest.json"),
    )
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    packaged = read_packaged_activation(output / "activation-manifest.json")
    monkeypatch.setattr(
        "app.services.foundry_registry_activation.active_registry_identity",
        lambda: {
            "release_kind": "signed_registry_release",
            "status": "ACTIVE",
            "signature_algorithm": "ed25519",
            "signature_key_id": packaged.manifest["signing_key_id"],
            "registry_epoch": packaged.manifest["registry_epoch"],
            "signed_payload_sha256": packaged.manifest[
                "registry_snapshot_sha256"
            ],
        },
    )
    with pytest.raises(
        RegistryActivationError, match="registry_activation_runtime_binding_mismatch"
    ):
        await registry_activation_readiness(
            db_session,
            release_request_id=request.id,
            release_request_hash=request.manifest_hash,
            target_git_commit=COMMIT,
        )


@pytest.mark.asyncio
async def test_internal_activation_status_requires_dedicated_secret(
    app_client, monkeypatch
):
    request_id = "11111111-1111-4111-8111-111111111111"
    request_hash = "sha256:" + "1" * 64
    secret = "activation-secret-that-is-independent-12345"
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_registry_activation_result_secret", secret
    )

    async def _ready(_db, **kwargs):
        assert str(kwargs["release_request_id"]) == request_id
        return {
            "status": "ACTIVATION_READY",
            "runtime_hot_switched": False,
            "target_git_commit": kwargs["target_git_commit"],
        }

    monkeypatch.setattr(
        "app.api.foundry_activation.registry_activation_readiness", _ready
    )
    path = f"/api/internal/foundry/registry-activations/{request_id}/status"
    params = {
        "release_request_hash": request_hash,
        "target_git_commit": COMMIT,
    }
    forbidden = await app_client.get(path, params=params)
    assert forbidden.status_code == 403

    accepted = await app_client.get(
        path,
        params=params,
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACTIVATION_READY"
