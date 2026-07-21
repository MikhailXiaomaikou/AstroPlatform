"""Workflow Foundry Candidate Catalog security and durability tests."""

from __future__ import annotations

import hashlib
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.models.foundry_records import (
    CapabilityRequest,
    FoundryCandidate,
    FoundryCandidateVersion,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
)
from app.models.schemas import User
from app.services.foundry_catalog import (
    FoundryCatalogError,
    _pending_registry_release_request,
    append_candidate_version,
    record_demo_report,
    record_formal_build_attestation,
    register_candidate_version,
    review_candidate_version,
    serialize_capability_gaps,
    serialize_demo_run,
    sha256_json,
    start_validation_run,
)
from app.services.foundry_validation_dispatch import (
    FoundryValidationDispatchError,
    dispatch_candidate_validation,
)


VALIDATION_IMAGE = "sha256:" + "a" * 64
FORMAL_IMAGE = "sha256:" + "b" * 64
OIDC_SUBJECT = "repo:standard-astro/platform:ref:refs/heads/main"


def _bundle(*, version: int = 1, suffix: str = "one") -> dict:
    return {
        "schema_version": 1,
        "candidate_id": f"desi_dr2_candidate_{suffix}",
        "candidate_version": version,
        "proposed_workflow_id": f"desi_dr2_workflow_{suffix}",
        "entrypoint_id": "desi_dr2_official_chain_summary_demo_v1",
        "risk_level": "R1",
        "workflow_spec": {
            "workflow_version": f"1.0.0-candidate.{version}",
            "claim_scope": "published_external_chain_context",
            "output_policy": {"publication_ready": False},
        },
        "source_pins": [
            {
                "key": "desi_dr2_manifest",
                "url": "https://data.desi.lbl.gov/manifest",
                "sha256": "1" * 64,
            }
        ],
        "fixture_hashes": [],
        "dependency_lock_sha256": "2" * 64,
        "runner_definition_sha256": "3" * 64,
        "generation": {
            "kind": "test_fixture",
            "model": "codex",
            "prompt_or_claim_stored": False,
            "generated_code_executed_by_draft_job": False,
        },
        "limitations": ["Non-formal Demo only."],
        "output_policy": {
            "evidence_class": "NON_FORMAL_DEMO",
            "publication_ready": False,
            "claim_eligible": False,
            "evidence_pack_allowed": False,
        },
    }


def _demo_report(version: FoundryCandidateVersion) -> dict:
    started = datetime(2026, 7, 21, tzinfo=timezone.utc)
    completed = started + timedelta(seconds=1)
    environment = {
        "python_version": "3.12.0",
        "entrypoint_id": version.candidate_bundle["entrypoint_id"],
    }
    report = {
        "schema_version": 1,
        "candidate_id": version.candidate_key,
        "candidate_version": version.version_number,
        "demo_run_id": str(uuid.uuid4()),
        "status": "PASSED",
        "evidence_class": "NON_FORMAL_DEMO",
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
        "candidate_bundle_sha256": version.version_hash,
        "workflow_spec_sha256": version.workflow_spec_hash,
        "dependency_lock_sha256": version.dependency_lock_hash,
        "runner_definition_sha256": version.candidate_bundle[
            "runner_definition_sha256"
        ],
        "runner_image_digest": version.validation_runner_image_digest,
        "environment": environment,
        "environment_sha256": sha256_json(environment),
        "generation": version.candidate_bundle["generation"],
        "source_pins": version.candidate_bundle["source_pins"],
        "fixture_hashes": version.candidate_bundle["fixture_hashes"],
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "completed_at": completed.isoformat().replace("+00:00", "Z"),
        "duration_ms": 1000,
        "stdout_sha256": hashlib.sha256(b"").hexdigest(),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "resource_usage": {"user_cpu_seconds": 0.1},
        "failure_class": None,
        "validation_summary": {"checks_passed": 4},
        "limitations": ["Non-formal Demo only."],
        "result": {"official_ready_cells": 1},
    }
    report["demo_report_sha256"] = sha256_json(report)
    return report


async def _user(db_session, name: str) -> User:
    row = User(
        id=uuid.uuid4(),
        username=name,
        email=f"{name}@astro.example",
        password_hash="unused",
        subscription_tier="solo",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _candidate_with_demo(db_session, *, suffix: str = "one"):
    candidate = FoundryCandidate(
        gap_fingerprint=hashlib.sha256(suffix.encode()).hexdigest(),
        gap_code="registered_workflow_missing",
        gap_descriptor={
            "gap_code": "registered_workflow_missing",
            "dataset_key": suffix,
            "research_domain": "cosmology",
        },
        status="BUILDING",
        risk_level="R1",
        generation_route="COMPOSITION",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    version = await append_candidate_version(
        db_session,
        candidate=candidate,
        draft={
            "candidate_bundle": _bundle(suffix=suffix),
            "validation_runner_image_digest": VALIDATION_IMAGE,
            "code_tree_hash": "4" * 64,
            "sbom_hash": "5" * 64,
        },
        actor_kind="AI_SERVICE",
        actor_user_id=None,
    )
    await db_session.commit()
    run = await start_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    report = _demo_report(version)
    demo = await record_demo_report(
        db_session, validation_run_id=run.id, demo_report=report
    )
    await db_session.refresh(candidate)
    return candidate, version, run, demo, report


async def test_capability_request_dedupes_without_leaking_owner_data(
    app_client, db_session, test_user, monkeypatch
):
    owner, owner_token = test_user
    stranger = await _user(db_session, "stranger")
    stranger_token = create_access_token(stranger.id)
    gap = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "next_action": "private owner guidance must not affect dedupe",
    }
    audit = ClaimAudit(
        user_id=owner.id,
        request_hash="1" * 64,
        lifecycle_status="COMPLETED",
        scientific_verdict="CAPABILITY_GAP",
        mode="audit_only",
        claim_text="private claim text",
        source_kind="arxiv",
        source_value="private source value",
        capability_gaps=[gap],
    )
    db_session.add(audit)
    await db_session.commit()
    await db_session.refresh(audit)
    gap_id = serialize_capability_gaps(audit.id, [gap])[0]["gap_id"]
    monkeypatch.setattr(settings, "foundry_gap_tracking_enabled", True)
    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    headers = {"Authorization": f"Bearer {owner_token}"}
    first = await app_client.post(
        f"/api/research/claim-audits/{audit.id}/capability-requests",
        headers=headers,
        json={"gap_id": gap_id},
    )
    assert first.status_code == 201, first.text
    repeated = await app_client.post(
        f"/api/research/claim-audits/{audit.id}/capability-requests",
        headers=headers,
        json={"gap_id": gap_id},
    )
    assert repeated.json()["id"] == first.json()["id"]
    candidate_id = first.json()["candidate_id"]
    denied = await app_client.get(
        f"/api/research/foundry-candidates/{candidate_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert denied.status_code == 404
    stranger_list = await app_client.get(
        "/api/research/capability-requests",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert stranger_list.json() == {"items": [], "total": 0}
    requests = list((await db_session.execute(select(CapabilityRequest))).scalars())
    assert len(requests) == 1


async def test_demo_callback_is_hash_bound_idempotent_and_non_formal(db_session):
    _candidate, version, run, demo, report = await _candidate_with_demo(db_session)
    replay = await record_demo_report(
        db_session, validation_run_id=run.id, demo_report=report
    )
    assert replay.id == demo.id
    view = serialize_demo_run(demo, version_number=1)
    assert view["result"] == {"official_ready_cells": 1}
    assert view["evidence_class"] == "NON_FORMAL_DEMO"
    assert view["publication_ready"] is False
    assert view["claim_eligible"] is False
    assert view["evidence_pack_allowed"] is False
    assert view["validation_runner_image_digest"] == VALIDATION_IMAGE
    assert "stdout" not in view and "stderr" not in view
    forged = dict(report)
    forged["result"] = {"scientific_verdict": "SUPPORTED"}
    forged["demo_report_sha256"] = sha256_json(
        {key: value for key, value in forged.items() if key != "demo_report_sha256"}
    )
    with pytest.raises(FoundryCatalogError, match="non-formal"):
        await record_demo_report(
            db_session, validation_run_id=run.id, demo_report=forged
        )

    version.workflow_version = "mutated"
    with pytest.raises(ValueError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()


async def test_ai_admin_secret_cannot_review_or_register(
    app_client, db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session, suffix="ai_gate"
    )
    monkeypatch.setattr(settings, "admin_secret", "ai-service-secret")
    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    review = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/reviews",
        headers={"X-Admin-Secret": "ai-service-secret"},
        json={
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "review_scope": "SCIENTIFIC",
            "decision": "APPROVED",
            "comment": "AI must not approve",
        },
    )
    assert review.status_code == 403
    register = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/register",
        headers={"X-Admin-Secret": "ai-service-secret"},
        json={
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "build_attestation_id": str(uuid.uuid4()),
        },
    )
    assert register.status_code == 403


async def test_validate_dispatch_records_success_and_retryable_failure(
    app_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "admin_secret", "validation-admin")
    monkeypatch.setattr(settings, "foundry_auto_demo_enabled", True)
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session, suffix="dispatch_ok"
    )

    async def _success(**_kwargs):
        return None

    monkeypatch.setattr("app.api.foundry.dispatch_candidate_validation", _success)
    response = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/validate",
        headers={"X-Admin-Secret": "validation-admin"},
        json={
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "DISPATCHED"
    assert response.json()["retryable"] is False

    failed_candidate, failed_version, *_ = await _candidate_with_demo(
        db_session, suffix="dispatch_fail"
    )

    async def _failure(**_kwargs):
        raise FoundryValidationDispatchError("validation_dispatch_timeout")

    monkeypatch.setattr("app.api.foundry.dispatch_candidate_validation", _failure)
    failed = await app_client.post(
        f"/api/admin/foundry/candidates/{failed_candidate.id}/validate",
        headers={"X-Admin-Secret": "validation-admin"},
        json={
            "candidate_version_id": str(failed_version.id),
            "candidate_version_hash": failed_version.version_hash,
        },
    )
    assert failed.status_code == 202, failed.text
    assert failed.json()["status"] == "DISPATCH_FAILED"
    assert failed.json()["retryable"] is True
    assert failed.json()["failure_class"] == "validation_dispatch_timeout"


async def test_github_dispatch_sends_only_opaque_validation_identifiers(monkeypatch):
    captured = {}

    class _Response:
        status_code = 204

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return _Response()

    run_id = uuid.uuid4()
    monkeypatch.setattr(settings, "foundry_validation_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_validation_github_repository", "standard-astro/platform")
    monkeypatch.setattr(settings, "foundry_validation_github_workflow", "foundry-demo.yml")
    monkeypatch.setattr(settings, "foundry_validation_github_ref", "f" * 40)
    monkeypatch.setattr(settings, "foundry_validation_github_token", "github-" + "t" * 40)
    monkeypatch.setattr("app.services.foundry_validation_dispatch.httpx.AsyncClient", _Client)
    await dispatch_candidate_validation(
        validation_run_id=run_id,
        candidate_key="desi_dr2_candidate_dispatch",
    )
    assert captured["json"] == {
        "ref": "f" * 40,
        "inputs": {
            "candidate_key": "desi_dr2_candidate_dispatch",
            "validation_run_id": str(run_id),
        },
    }
    assert set(captured["json"]["inputs"]) == {
        "candidate_key",
        "validation_run_id",
    }


def _formal_build_report(candidate, version, *, built_at: datetime) -> dict:
    report = {
        "schema_version": 1,
        "attestation_id": str(uuid.uuid4()),
        "candidate_id": str(candidate.id),
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
        "source_tree_sha256": version.code_tree_hash,
        "git_commit": "6" * 40,
        "dependency_lock_sha256": version.dependency_lock_hash,
        "formal_sbom_sha256": "7" * 64,
        "test_report_sha256": "8" * 64,
        "tests_passed": True,
        "formal_worker_image_digest": FORMAL_IMAGE,
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "oidc_subject": OIDC_SUBJECT,
        "sigstore_verified": True,
        "sigstore_bundle_sha256": "9" * 64,
        "provenance_sha256": "a" * 64,
        "verification_method": "protected_ci_callback_after_sigstore_verification",
        "build_metadata": {"runner": "github-hosted"},
        "built_at": built_at.isoformat(),
    }
    report["receipt_sha256"] = sha256_json(report)
    return report


async def test_formal_build_is_protected_and_registration_stays_pending(
    app_client, db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session, suffix="formal"
    )
    reviewer = await _user(db_session, "reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="SCIENTIFIC",
        decision="APPROVED",
        comment="Exact-version human approval",
    )
    await db_session.refresh(candidate)
    report = _formal_build_report(
        candidate, version, built_at=datetime.now(timezone.utc) + timedelta(minutes=1)
    )
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(settings, "foundry_formal_build_result_secret", "build-" + "x" * 40)
    monkeypatch.setattr(settings, "foundry_formal_build_oidc_subject", OIDC_SUBJECT)
    denied = await app_client.post(
        "/api/internal/foundry/formal-build-attestations",
        headers={"Authorization": "Bearer wrong"},
        json=report,
    )
    assert denied.status_code == 403
    attestation = await record_formal_build_attestation(
        db_session,
        attestation_report=report,
        expected_oidc_subject=OIDC_SUBJECT,
    )
    assert attestation.formal_worker_image_digest == FORMAL_IMAGE
    assert version.validation_runner_image_digest == VALIDATION_IMAGE

    fake_registry = types.ModuleType("app.services.workflow_registry_v2")
    fake_registry.registry_snapshot = lambda: {
        "epoch": "2026-07-21.1",
        "registry_hash": "sha256:" + "c" * 64,
    }
    fake_registry.build_registry_entry_from_approved_candidate = lambda payload: {
        "candidate_id": payload["candidate_id"],
        "candidate_version": payload["candidate_version"],
        "candidate_version_hash": payload["candidate_version_hash"],
        "workflow_spec_hash": payload["workflow_spec_hash"],
        "worker_image_digest": payload["worker_image_digest"],
        "workflow": {
            "workflow_id": version.workflow_id,
            "version": "1.0.0",
        },
        "tools": [],
        "registry_entry_hash": "sha256:" + "d" * 64,
        "installation_status": "PENDING_RELEASE",
        "runtime_registry_modified": False,
    }
    monkeypatch.setitem(sys.modules, "app.services.workflow_registry_v2", fake_registry)
    entry, release = await register_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        build_attestation_id=attestation.id,
        registrar_user_id=reviewer.id,
    )
    await db_session.refresh(candidate)
    assert candidate.status == "APPROVED"
    assert entry.status == "PENDING_RELEASE"
    assert entry.worker_image_digest == FORMAL_IMAGE
    assert release.status == "PENDING_SIGNATURE"
    assert release.signature is None and release.key_id is None
    assert release.manifest["runtime_registry_modified"] is False
    assert release.manifest["base_registry_hash"] == "sha256:" + "c" * 64


async def test_pending_release_requests_form_a_complete_deterministic_delta_chain(
    db_session, monkeypatch
):
    actor = await _user(db_session, "release_actor")
    fake_registry = types.ModuleType("app.services.workflow_registry_v2")
    fake_registry.registry_snapshot = lambda: {
        "epoch": "base.1",
        "registry_hash": "sha256:" + "e" * 64,
    }
    monkeypatch.setitem(sys.modules, "app.services.workflow_registry_v2", fake_registry)
    first = await _pending_registry_release_request(
        db_session,
        request_kind="REGISTER_CANDIDATE",
        entries=[{"candidate_version_hash": "1" * 64}],
        status_changes=[],
        context={},
        actor_user_id=actor.id,
    )
    db_session.add(first)
    await db_session.commit()
    second = await _pending_registry_release_request(
        db_session,
        request_kind="REGISTER_CANDIDATE",
        entries=[{"candidate_version_hash": "2" * 64}],
        status_changes=[],
        context={},
        actor_user_id=actor.id,
    )
    db_session.add(second)
    await db_session.commit()
    revoke = await _pending_registry_release_request(
        db_session,
        request_kind="REVOKE_WORKFLOW",
        entries=[],
        status_changes=[{"registry_entry_hash": "sha256:" + "3" * 64, "requested_status": "REVOKED"}],
        context={},
        actor_user_id=actor.id,
    )
    assert second.manifest["previous_request_hash"] == first.manifest_hash
    assert len(second.manifest["operation_sequence"]) == 2
    assert revoke.manifest["previous_request_hash"] == second.manifest_hash
    assert [item["operation"] for item in revoke.manifest["operation_sequence"]] == [
        "UPSERT_ENTRY",
        "UPSERT_ENTRY",
        "SET_ENTRY_STATUS",
    ]
    assert revoke.manifest["operation_sequence_hash"] == "sha256:" + sha256_json(
        revoke.manifest["operation_sequence"]
    )
    assert not list((await db_session.execute(select(WorkflowRegistryEntry))).scalars())
    assert len(list((await db_session.execute(select(WorkflowRegistryRelease))).scalars())) == 2
