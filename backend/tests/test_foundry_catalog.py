"""Workflow Foundry Candidate Catalog security and durability tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.auth import create_access_token
from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.models.foundry_records import (
    CapabilityRequest,
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryFormalBuildAttestation,
    FoundryValidationRun,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
)
from app.models.schemas import User
from app.services.foundry_catalog import (
    FoundryCatalogError,
    _append_event,
    _formal_build_source_binding,
    _pending_registry_release_request,
    append_candidate_version,
    ensure_validation_run,
    record_demo_report,
    record_validation_dispatch,
    record_validation_workflow_failure,
    reconcile_expired_validation_runs,
    record_formal_build_dispatch,
    record_formal_build_attestation,
    register_candidate_version,
    request_formal_build_dispatch,
    review_candidate_version,
    serialize_capability_gaps,
    serialize_demo_run,
    sha256_json,
    start_validation_run,
)
from app.services.foundry_ci_dispatch import FoundryCIDispatchError
from app.services.foundry_validation_dispatch import (
    FoundryValidationDispatchError,
    dispatch_candidate_validation,
    queue_and_dispatch_candidate_validation,
)


VALIDATION_IMAGE = "sha256:" + "a" * 64
FORMAL_IMAGE = "sha256:" + "b" * 64
GITHUB_REPOSITORY = "standard-astro/platform"
GITHUB_WORKFLOW = "foundry-formal-worker.yml"
GITHUB_WORKFLOW_REF = (
    f"{GITHUB_REPOSITORY}/.github/workflows/{GITHUB_WORKFLOW}@refs/heads/main"
)
OIDC_SUBJECT = f"https://github.com/{GITHUB_WORKFLOW_REF}"
ATTESTATION_KEY_ID = "formal-build-test-1"
_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x1d" * 32)
ATTESTATION_PUBLIC_KEY = base64.b64encode(
    _ATTESTATION_PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).decode("ascii")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _bundle(
    *,
    version: int = 1,
    suffix: str = "one",
    workflow_id: str | None = None,
    workflow_version: str | None = None,
    formal_source: bool = False,
    source_materialization_required: bool = False,
) -> dict:
    proposed_workflow_id = workflow_id or f"desi_dr2_workflow_{suffix}"
    return {
        "schema_version": 1,
        "candidate_id": f"desi_dr2_candidate_{suffix}",
        "candidate_version": version,
        "proposed_workflow_id": proposed_workflow_id,
        "entrypoint_id": "desi_dr2_official_chain_summary_demo_v1",
        "risk_level": "R1",
        "workflow_spec": {
            "workflow_id": proposed_workflow_id,
            "workflow_version": workflow_version or f"1.0.0-candidate.{version}",
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
        "generation": (
            {
                "kind": "ai_draft_provider_contract_v1",
                "model": "codex",
                "prompt_or_claim_stored": False,
                "generated_code_executed_by_draft_job": False,
                "source_hash_algorithm": "standard_astro_tracked_source_manifest_v1",
                "source_base_commit": "6" * 40,
                "source_base_tree_sha256": "4" * 64,
                "source_tree_sha256": (
                    "5" * 64 if source_materialization_required else "4" * 64
                ),
                "source_materialization_required": source_materialization_required,
            }
            if formal_source
            else {
                "kind": "test_fixture",
                "model": "codex",
                "prompt_or_claim_stored": False,
                "generated_code_executed_by_draft_job": False,
            }
        ),
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
        "candidate_bundle_sha256": sha256_json(version.candidate_bundle),
        "candidate_version_sha256": version.version_hash,
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
        "artifact_manifest": [
            {
                "path": "stdout.log",
                "kind": "STDOUT",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "bytes": 0,
            },
            {
                "path": "stderr.log",
                "kind": "STDERR",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "bytes": 0,
            },
        ],
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


async def _candidate_version(db_session, suffix: str):
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
    return candidate, version


async def _candidate_with_demo(
    db_session,
    *,
    suffix: str = "one",
    workflow_id: str | None = None,
    workflow_version: str | None = None,
    formal_source: bool = False,
    source_materialization_required: bool = False,
):
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
            "candidate_bundle": _bundle(
                suffix=suffix,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                formal_source=formal_source,
                source_materialization_required=source_materialization_required,
            ),
            "validation_runner_image_digest": VALIDATION_IMAGE,
            "code_tree_hash": (
                "5" * 64 if source_materialization_required else "4" * 64
            ),
            **(
                {
                    "patch_hash": (
                        "6" * 64
                        if source_materialization_required
                        else EMPTY_SHA256
                    )
                }
                if formal_source
                else {}
            ),
            "sbom_hash": "5" * 64,
        },
        actor_kind="AI_DRAFT_JOB" if formal_source else "AI_SERVICE",
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
    await record_validation_dispatch(
        db_session, validation_run_id=run.id, dispatched=True
    )
    report = _demo_report(version)
    demo = await record_demo_report(
        db_session, validation_run_id=run.id, demo_report=report
    )
    await db_session.refresh(candidate)
    return candidate, version, run, demo, report


async def _append_legacy_formal_build_dispatch(
    db_session,
    *,
    candidate: FoundryCandidate,
    version: FoundryCandidateVersion,
) -> FoundryCandidateEvent:
    event = await _append_event(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="FORMAL_BUILD_DISPATCHED",
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload=_formal_build_source_binding(version),
    )
    await db_session.commit()
    return event


async def test_event_append_locks_candidate_before_reading_chain_head() -> None:
    candidate_id = uuid.uuid4()

    class RecordingSession:
        def __init__(self) -> None:
            self.statements = []
            self.added = []

        async def scalar(self, statement):
            self.statements.append(statement)
            # First query locks the owning candidate; the second reads an empty
            # event chain.
            return candidate_id if len(self.statements) == 1 else None

        def add(self, row) -> None:
            self.added.append(row)

        async def flush(self) -> None:
            return None

    db = RecordingSession()
    await _append_event(
        db,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        event_type="CONCURRENT_CALLBACK_TEST",
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={},
    )

    lock_sql = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FROM foundry_candidates" in lock_sql
    assert "FOR UPDATE" in lock_sql
    assert db.added[0].previous_event_hash is None


async def test_event_chain_constraints_reject_genesis_and_parent_forks(
    db_session,
) -> None:
    candidate = FoundryCandidate(
        gap_fingerprint=hashlib.sha256(b"event-chain-constraints").hexdigest(),
        gap_code="registered_workflow_missing",
        gap_descriptor={
            "gap_code": "registered_workflow_missing",
            "research_domain": "cosmology",
        },
        status="DRAFT",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    candidate_id = candidate.id

    genesis_hash = "1" * 64
    db_session.add(
        FoundryCandidateEvent(
            candidate_id=candidate_id,
            event_type="GENESIS",
            actor_kind="CONTROL_PLANE",
            event_payload={},
            previous_event_hash=None,
            event_hash=genesis_hash,
        )
    )
    await db_session.commit()

    db_session.add(
        FoundryCandidateEvent(
            candidate_id=candidate_id,
            event_type="SECOND_GENESIS",
            actor_kind="CONTROL_PLANE",
            event_payload={},
            previous_event_hash=None,
            event_hash="2" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    db_session.add(
        FoundryCandidateEvent(
            candidate_id=candidate_id,
            event_type="FIRST_CHILD",
            actor_kind="CONTROL_PLANE",
            event_payload={},
            previous_event_hash=genesis_hash,
            event_hash="3" * 64,
        )
    )
    await db_session.commit()

    db_session.add(
        FoundryCandidateEvent(
            candidate_id=candidate_id,
            event_type="FORKED_CHILD",
            actor_kind="CONTROL_PLANE",
            event_payload={},
            previous_event_hash=genesis_hash,
            event_hash="4" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


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


async def test_foundry_self_access_reports_current_user_roles_without_403_probe(
    app_client, db_session, monkeypatch
):
    normal = await _user(db_session, "foundry_normal_user")
    named_admin = await _user(db_session, "foundry_named_admin")
    tier_admin = await _user(db_session, "foundry_tier_admin")
    reviewer = await _user(db_session, "foundry_human_reviewer")
    scientific_reviewer = await _user(db_session, "foundry_scientific_reviewer")
    tier_admin.subscription_tier = "admin"
    await db_session.commit()

    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    monkeypatch.setattr(settings, "admin_secret", "configured-admin-secret")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("LOCAL_DEV_NO_AUTH", "false")
    monkeypatch.setenv("ADMIN_USERNAMES", named_admin.username)
    monkeypatch.setenv("FOUNDRY_HUMAN_REVIEWER_USERNAMES", reviewer.username)
    monkeypatch.setenv("SCIENTIFIC_REVIEWER_USERNAMES", scientific_reviewer.username)

    async def self_access(user: User):
        return await app_client.get(
            "/api/research/foundry-access",
            headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
        )

    normal_response = await self_access(normal)
    named_admin_response = await self_access(named_admin)
    tier_admin_response = await self_access(tier_admin)
    reviewer_response = await self_access(reviewer)
    scientific_reviewer_response = await self_access(scientific_reviewer)

    assert normal_response.status_code == 200, normal_response.text
    assert normal_response.json() == {
        "can_administer": False,
        "can_review": False,
    }
    assert named_admin_response.json() == {
        "can_administer": True,
        "can_review": False,
    }
    assert tier_admin_response.json() == {
        "can_administer": True,
        "can_review": False,
    }
    assert reviewer_response.json() == {
        "can_administer": False,
        "can_review": True,
    }
    assert scientific_reviewer_response.json() == {
        "can_administer": False,
        "can_review": True,
    }

    unauthenticated = await app_client.get("/api/research/foundry-access")
    assert unauthenticated.status_code == 401


async def test_demo_callback_is_hash_bound_idempotent_and_non_formal(db_session):
    _candidate, version, run, demo, report = await _candidate_with_demo(db_session)
    replay = await record_demo_report(
        db_session, validation_run_id=run.id, demo_report=report
    )
    assert replay.id == demo.id
    wrong_run = await start_validation_run(
        db_session,
        candidate_id=version.candidate_id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    with pytest.raises(FoundryCatalogError, match="different content or binding"):
        await record_demo_report(
            db_session,
            validation_run_id=wrong_run.id,
            demo_report=report,
        )
    view = serialize_demo_run(demo, version_number=1)
    assert view["result"] == {"official_ready_cells": 1}
    assert view["evidence_class"] == "NON_FORMAL_DEMO"
    assert view["publication_ready"] is False
    assert view["claim_eligible"] is False
    assert view["evidence_pack_allowed"] is False
    assert view["validation_runner_image_digest"] == VALIDATION_IMAGE
    assert "stdout" not in view and "stderr" not in view
    assert view["artifact_receipts"] == [
        {
            "name": "stdout.log",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "size_bytes": 0,
            "media_type": "text/plain; charset=utf-8",
        },
        {
            "name": "stderr.log",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "size_bytes": 0,
            "media_type": "text/plain; charset=utf-8",
        },
    ]
    for escaped_result, escaped_failure_class in (
        ({"scientific_verdict": "SUPPORTED"}, None),
        ({"message": "Result is SUPPORTED"}, None),
        ({"Result is SUPPORTED": None}, None),
        ({"message": "evidence_pack_id=pack-123"}, None),
        ({"Evidence Pack ID": "pack-123"}, None),
        ({}, "Result is SUPPORTED"),
    ):
        forged = dict(report)
        forged["result"] = escaped_result
        forged["failure_class"] = escaped_failure_class
        forged["demo_report_sha256"] = sha256_json(
            {
                key: value
                for key, value in forged.items()
                if key != "demo_report_sha256"
            }
        )
        with pytest.raises(FoundryCatalogError, match="non-formal"):
            await record_demo_report(
                db_session,
                validation_run_id=run.id,
                demo_report=forged,
            )

    version.workflow_version = "mutated"
    with pytest.raises(ValueError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()


async def test_old_version_demo_is_recorded_without_overwriting_new_version_status(
    db_session,
):
    candidate = FoundryCandidate(
        gap_fingerprint=hashlib.sha256(b"version-race").hexdigest(),
        gap_code="registered_workflow_missing",
        gap_descriptor={
            "gap_code": "registered_workflow_missing",
            "dataset_key": "version-race",
            "research_domain": "cosmology",
        },
        status="BUILDING",
        risk_level="R1",
        generation_route="COMPOSITION",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)

    version_one = await append_candidate_version(
        db_session,
        candidate=candidate,
        draft={
            "candidate_bundle": _bundle(version=1, suffix="version_race"),
            "validation_runner_image_digest": VALIDATION_IMAGE,
            "code_tree_hash": "4" * 64,
            "sbom_hash": "5" * 64,
        },
        actor_kind="AI_SERVICE",
        actor_user_id=None,
    )
    await db_session.commit()
    run_one = await start_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version_one.id,
        candidate_version_hash=version_one.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_validation_dispatch(
        db_session, validation_run_id=run_one.id, dispatched=True
    )

    version_two = await append_candidate_version(
        db_session,
        candidate=candidate,
        draft={
            "candidate_bundle": _bundle(version=2, suffix="version_race"),
            "validation_runner_image_digest": VALIDATION_IMAGE,
            "code_tree_hash": "6" * 64,
            "sbom_hash": "7" * 64,
        },
        actor_kind="AI_SERVICE",
        actor_user_id=None,
    )
    await db_session.commit()
    await db_session.refresh(candidate)
    assert candidate.current_version_number == version_two.version_number == 2
    assert candidate.status == "BUILDING"

    demo_one = await record_demo_report(
        db_session,
        validation_run_id=run_one.id,
        demo_report=_demo_report(version_one),
    )
    await db_session.refresh(candidate)
    await db_session.refresh(run_one)

    assert demo_one.candidate_version_id == version_one.id
    assert demo_one.candidate_version_hash == version_one.version_hash
    assert run_one.status == "PASSED"
    assert candidate.current_version_number == 2
    assert candidate.status == "BUILDING"
    recorded_events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == version_one.id,
                    FoundryCandidateEvent.event_type == "DEMO_RECORDED",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(recorded_events) == 1
    assert recorded_events[0].event_payload["demo_run_id"] == str(demo_one.id)


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

    monkeypatch.setattr(
        "app.services.foundry_validation_dispatch.dispatch_candidate_validation",
        _success,
    )
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

    monkeypatch.setattr(
        "app.services.foundry_validation_dispatch.dispatch_candidate_validation",
        _failure,
    )
    failed = await app_client.post(
        f"/api/admin/foundry/candidates/{failed_candidate.id}/validate",
        headers={"X-Admin-Secret": "validation-admin"},
        json={
            "candidate_version_id": str(failed_version.id),
            "candidate_version_hash": failed_version.version_hash,
        },
    )
    assert failed.status_code == 202, failed.text
    assert failed.json()["status"] == "DISPATCH_UNCERTAIN"
    assert failed.json()["retryable"] is True
    assert failed.json()["failure_class"] == "validation_dispatch_timeout"
    assert failed.json()["retry_after"] is not None


def _validation_failure_report(run, version, *, run_attempt="1"):
    return {
        "schema_version": 1,
        "validation_run_id": str(run.id),
        "candidate_id": str(run.candidate_id),
        "candidate_version_id": str(version.id),
        "candidate_version_number": version.version_number,
        "candidate_version_hash": version.version_hash,
        "status": "FAILED",
        "failure_class": "validation_workflow_failed",
        "failed_stage": "isolated_demo",
        "workflow_conclusion": "failure",
        "github_repository": GITHUB_REPOSITORY,
        "github_workflow_ref": (
            f"{GITHUB_REPOSITORY}/.github/workflows/"
            "foundry-candidate-demo.yml@refs/heads/main"
        ),
        "github_workflow_sha": "9" * 40,
        "github_run_id": "12345",
        "github_run_attempt": run_attempt,
    }


async def test_validation_reentry_during_dispatch_reuses_uncertain_attempt(
    db_session, monkeypatch
):
    candidate, version = await _candidate_version(db_session, "dispatch_reentry")
    dispatches = 0

    async def _dispatch(**_kwargs):
        nonlocal dispatches
        dispatches += 1
        active, created = await ensure_validation_run(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
        )
        assert created is False
        assert active.status == "OUTCOME_UNKNOWN"

    monkeypatch.setattr(
        "app.services.foundry_validation_dispatch.dispatch_candidate_validation",
        _dispatch,
    )
    run = await queue_and_dispatch_candidate_validation(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert run.status == "DISPATCHED"
    assert dispatches == 1


async def test_validation_short_long_leases_reconcile_and_bound_attempts(db_session):
    candidate, version = await _candidate_version(db_session, "validation_leases")
    first, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    created_at = first.created_at.replace(tzinfo=timezone.utc)
    same, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
        now=created_at + timedelta(minutes=4),
    )
    assert (same.id, created) == (first.id, False)
    assert await reconcile_expired_validation_runs(
        db_session, now=created_at + timedelta(minutes=6)
    ) == 1
    await db_session.refresh(first)
    assert first.status == "TIMED_OUT"

    second, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
        now=created_at + timedelta(minutes=6),
    )
    assert created is True
    dispatched_at = created_at + timedelta(minutes=7)
    await record_validation_dispatch(
        db_session,
        validation_run_id=second.id,
        dispatched=False,
        failure_class="validation_dispatch_timeout",
        retryable=True,
        delivery_uncertain=True,
        now=dispatched_at,
    )
    same, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
        now=dispatched_at + timedelta(minutes=59),
    )
    assert (same.id, created) == (second.id, False)
    assert await reconcile_expired_validation_runs(
        db_session, now=dispatched_at + timedelta(minutes=61)
    ) == 1

    third, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
        now=dispatched_at + timedelta(minutes=61),
    )
    assert created is True
    await record_validation_dispatch(
        db_session,
        validation_run_id=third.id,
        dispatched=True,
        now=dispatched_at + timedelta(minutes=62),
    )
    assert await reconcile_expired_validation_runs(
        db_session, now=dispatched_at + timedelta(minutes=123)
    ) == 1
    with pytest.raises(FoundryCatalogError, match="bounded attempt limit"):
        await ensure_validation_run(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
            now=dispatched_at + timedelta(minutes=124),
        )


@pytest.mark.parametrize("legacy_retryable", (True, False, None))
async def test_validation_legacy_dispatch_failure_retry_uses_exact_event(
    db_session, legacy_retryable
):
    candidate, version = await _candidate_version(
        db_session, f"legacy_dispatch_{str(legacy_retryable).lower()}"
    )
    legacy = FoundryValidationRun(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        status="DISPATCH_FAILED",
        requested_by_kind="HUMAN_ADMIN",
        validation_summary={},
        failure_class="legacy_dispatch_failed",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(legacy)
    await db_session.flush()
    if legacy_retryable is not None:
        await _append_event(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            event_type="VALIDATION_DISPATCH_FAILED",
            actor_kind="CONTROL_PLANE",
            actor_user_id=None,
            payload={
                "validation_run_id": str(legacy.id),
                "retryable": legacy_retryable,
            },
        )
    await db_session.commit()

    if legacy_retryable is True:
        retry, created = await ensure_validation_run(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
        )
        assert created is True
        assert retry.id != legacy.id
        assert retry.validation_summary["attempt_number"] == 2
    else:
        with pytest.raises(FoundryCatalogError, match="failed permanently"):
            await ensure_validation_run(
                db_session,
                candidate_id=candidate.id,
                candidate_version_id=version.id,
                candidate_version_hash=version.version_hash,
                actor_kind="HUMAN_ADMIN",
                actor_user_id=None,
            )


async def test_validation_reconciler_filters_expired_rows_before_limit(db_session):
    candidate, version = await _candidate_version(db_session, "reconcile_limit")
    now = datetime.now(timezone.utc)
    for index in range(100):
        db_session.add(
            FoundryValidationRun(
                candidate_id=candidate.id,
                candidate_version_id=version.id,
                candidate_version_hash=version.version_hash,
                status="DISPATCHED",
                requested_by_kind="HUMAN_ADMIN",
                validation_summary={"attempt_number": 1},
                created_at=now - timedelta(hours=2, seconds=index),
                started_at=now,
            )
        )
    stale = FoundryValidationRun(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        status="QUEUED",
        requested_by_kind="HUMAN_ADMIN",
        validation_summary={"attempt_number": 1},
        created_at=now - timedelta(minutes=6),
    )
    db_session.add(stale)
    candidate.status = "VALIDATING"
    await db_session.commit()

    assert await reconcile_expired_validation_runs(
        db_session, now=now, limit=100
    ) == 1
    await db_session.refresh(stale)
    assert stale.status == "TIMED_OUT"


async def test_review_waits_for_active_retry_and_late_demo_cannot_downgrade(
    db_session, test_user
):
    reviewer, _token = test_user
    candidate, version, _first_run, _demo, _report = await _candidate_with_demo(
        db_session, suffix="review_retry_race"
    )
    retry, created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=reviewer.id,
    )
    assert created is True
    await record_validation_dispatch(
        db_session, validation_run_id=retry.id, dispatched=True
    )
    with pytest.raises(FoundryCatalogError, match="active validation attempt"):
        await review_candidate_version(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            reviewer_user_id=reviewer.id,
            review_scope="ENGINEERING",
            decision="APPROVED",
            comment="Must wait for the retry.",
        )

    # Defense in depth for a cross-process race: even if a terminal aggregate
    # state is committed after the callback checked its active run, the Demo
    # remains append-only and cannot demote that aggregate state.
    candidate.status = "APPROVED"
    await db_session.commit()
    await record_demo_report(
        db_session,
        validation_run_id=retry.id,
        demo_report=_demo_report(version),
    )
    await db_session.refresh(candidate)
    assert candidate.status == "APPROVED"


async def test_validation_failure_callback_is_exact_idempotent_and_retryable(db_session):
    candidate, version = await _candidate_version(db_session, "workflow_failure")
    run, _ = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_validation_dispatch(
        db_session, validation_run_id=run.id, dispatched=True
    )
    report = _validation_failure_report(run, version)
    recorded, created = await record_validation_workflow_failure(
        db_session,
        validation_run_id=run.id,
        report=report,
        expected_repository=GITHUB_REPOSITORY,
        expected_workflow_ref=report["github_workflow_ref"],
    )
    replay, replay_created = await record_validation_workflow_failure(
        db_session,
        validation_run_id=run.id,
        report=report,
        expected_repository=GITHUB_REPOSITORY,
        expected_workflow_ref=report["github_workflow_ref"],
    )
    assert recorded.status == replay.status == "WORKFLOW_FAILED"
    assert created is True and replay_created is False
    assert recorded.validation_summary["retryable"] is True
    conflicting = dict(report)
    conflicting["github_run_id"] = "12346"
    with pytest.raises(FoundryCatalogError, match="different failure result"):
        await record_validation_workflow_failure(
            db_session,
            validation_run_id=run.id,
            report=conflicting,
            expected_repository=GITHUB_REPOSITORY,
            expected_workflow_ref=report["github_workflow_ref"],
        )
    with pytest.raises(FoundryCatalogError, match="protected attempt binding"):
        await record_validation_workflow_failure(
            db_session,
            validation_run_id=run.id,
            report=_validation_failure_report(run, version, run_attempt="2"),
            expected_repository=GITHUB_REPOSITORY,
            expected_workflow_ref=report["github_workflow_ref"],
        )
    retry, retry_created = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert retry_created is True
    assert retry.id != run.id


async def test_validation_failure_callback_http_contract(
    app_client, db_session, monkeypatch
):
    candidate, version = await _candidate_version(db_session, "failure_http")
    run, _ = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_validation_dispatch(
        db_session, validation_run_id=run.id, dispatched=True
    )
    secret = "validation-result-secret-for-tests-123456"
    monkeypatch.setattr(settings, "foundry_validation_result_secret", secret)
    monkeypatch.setattr(
        settings, "foundry_validation_github_repository", GITHUB_REPOSITORY
    )
    monkeypatch.setattr(
        settings,
        "foundry_validation_github_workflow",
        "foundry-candidate-demo.yml",
    )
    monkeypatch.setattr(settings, "foundry_validation_github_ref", "main")
    report = _validation_failure_report(run, version)
    endpoint = f"/api/internal/foundry/validation-runs/{run.id}/failure"

    monkeypatch.setattr(settings, "foundry_auto_demo_enabled", False)
    disabled = await app_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json=report,
    )
    assert disabled.status_code == 404
    monkeypatch.setattr(settings, "foundry_auto_demo_enabled", True)
    assert (await app_client.post(endpoint, json=report)).status_code == 403
    mismatch = await app_client.post(
        f"/api/internal/foundry/validation-runs/{uuid.uuid4()}/failure",
        headers={"Authorization": f"Bearer {secret}"},
        json=report,
    )
    assert mismatch.status_code == 409
    extra = await app_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json={**report, "unexpected": True},
    )
    assert extra.status_code == 422
    rerun = await app_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json={**report, "github_run_attempt": "2"},
    )
    assert rerun.status_code == 422
    accepted = await app_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json=report,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "FAILED_RECORDED"
    assert accepted.json()["retryable"] is True


async def test_failure_callback_never_overwrites_recorded_demo(db_session):
    _candidate, version, run, demo, _report = await _candidate_with_demo(
        db_session, suffix="demo_wins"
    )
    failure = _validation_failure_report(run, version)
    preserved, created = await record_validation_workflow_failure(
        db_session,
        validation_run_id=run.id,
        report=failure,
        expected_repository=GITHUB_REPOSITORY,
        expected_workflow_ref=failure["github_workflow_ref"],
    )
    assert created is False
    assert preserved.status == demo.status == "PASSED"


async def test_validation_rejects_late_demo_after_reconciled_timeout(db_session):
    candidate, version = await _candidate_version(db_session, "late_demo")
    run, _ = await ensure_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    started = datetime.now(timezone.utc)
    await record_validation_dispatch(
        db_session, validation_run_id=run.id, dispatched=True, now=started
    )
    assert await reconcile_expired_validation_runs(
        db_session, now=started + timedelta(hours=1, seconds=1)
    ) == 1
    with pytest.raises(FoundryCatalogError, match="not active"):
        await record_demo_report(
            db_session,
            validation_run_id=run.id,
            demo_report=_demo_report(version),
        )


def test_validation_dispatch_classifies_known_rejection_and_unknown_delivery():
    rejected = FoundryValidationDispatchError("validation_dispatch_http_401")
    overloaded = FoundryValidationDispatchError("validation_dispatch_http_503")
    throttled = FoundryValidationDispatchError("validation_dispatch_http_429")
    assert (rejected.retryable, rejected.delivery_uncertain) == (False, False)
    assert (overloaded.retryable, overloaded.delivery_uncertain) == (True, True)
    assert (throttled.retryable, throttled.delivery_uncertain) == (True, False)


def test_foundry_validation_reconciler_is_maintenance_scheduled():
    import celery_worker

    schedule = celery_worker._build_beat_schedule()
    entry = schedule["reconcile-stale-foundry-validations"]
    assert entry == {
        "task": "maintenance.reconcile_foundry_validations",
        "schedule": 300.0,
        "options": {"queue": "maintenance"},
    }
    assert "app.tasks.foundry_tasks" in celery_worker.celery_app.conf.include


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
    monkeypatch.setattr(settings, "foundry_validation_github_ref", "main")
    monkeypatch.setattr(settings, "foundry_validation_github_token", "github-" + "t" * 40)
    monkeypatch.setattr("app.services.foundry_validation_dispatch.httpx.AsyncClient", _Client)
    version_binding = {
        "candidate_id": str(uuid.uuid4()),
        "candidate_version_id": str(uuid.uuid4()),
        "candidate_key": "desi_dr2_candidate_dispatch",
        "candidate_version_number": "1",
        "candidate_version_hash": "a" * 64,
        "candidate_bundle_hash": "b" * 64,
        "validation_runner_image_digest": VALIDATION_IMAGE,
    }
    await dispatch_candidate_validation(
        validation_run_id=run_id,
        candidate_key="desi_dr2_candidate_dispatch",
        version_binding=version_binding,
    )
    assert captured["json"] == {
        "ref": "main",
        "inputs": {
            "candidate_key": "desi_dr2_candidate_dispatch",
            "validation_run_id": str(run_id),
            "version_binding": json.dumps(
                version_binding,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        },
    }
    assert set(captured["json"]["inputs"]) == {
        "candidate_key",
        "validation_run_id",
        "version_binding",
    }


async def test_github_validation_dispatch_rejects_non_main_ref(monkeypatch):
    monkeypatch.setattr(settings, "foundry_validation_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_validation_github_repository", "standard-astro/platform")
    monkeypatch.setattr(settings, "foundry_validation_github_workflow", "foundry-demo.yml")
    monkeypatch.setattr(settings, "foundry_validation_github_ref", "feature/untrusted")
    monkeypatch.setattr(settings, "foundry_validation_github_token", "github-" + "t" * 40)
    version_binding = {
        "candidate_id": str(uuid.uuid4()),
        "candidate_version_id": str(uuid.uuid4()),
        "candidate_key": "desi_dr2_candidate_dispatch",
        "candidate_version_number": "1",
        "candidate_version_hash": "a" * 64,
        "candidate_bundle_hash": "b" * 64,
        "validation_runner_image_digest": VALIDATION_IMAGE,
    }

    with pytest.raises(
        FoundryValidationDispatchError,
        match="validation_dispatch_misconfigured",
    ):
        await dispatch_candidate_validation(
            validation_run_id=uuid.uuid4(),
            candidate_key="desi_dr2_candidate_dispatch",
            version_binding=version_binding,
        )


def _resign_formal_build_report(report: dict) -> dict:
    report.pop("attestation_artifact_sha256", None)
    payload_bytes = json.dumps(
        report["payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    report["payload_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    report["signature"] = {
        "algorithm": "ed25519",
        "key_id": ATTESTATION_KEY_ID,
        "value": base64.b64encode(
            _ATTESTATION_PRIVATE_KEY.sign(
                b"standard-astro/formal-build-attestation/v2\0" + payload_bytes
            )
        ).decode("ascii"),
    }
    report["attestation_artifact_sha256"] = sha256_json(report)
    return report


def _formal_build_report(
    candidate,
    version,
    *,
    built_at: datetime,
    formal_build_attempt_id: uuid.UUID | None = None,
) -> dict:
    workflow_sha = "e" * 40
    git_commit = "6" * 40
    receipt_names = (
        "dependency_lock",
        "secret_scan",
        "static_audit",
        "linux_amd64_dependency_integrity",
        "linux_amd64_license_policy",
        "linux_amd64_environment",
        "linux_arm64_dependency_integrity",
        "linux_arm64_license_policy",
        "linux_arm64_environment",
    )
    release_audit = {
        "schema_version": "standard_astro_formal_release_audit_v1",
        "status": "PASSED",
        "policy_id": "standard-astro-formal-release-minimal-v1",
        "policy_sha256": "1" * 64,
        "source_tree_sha256": version.code_tree_hash,
        "dependency_lock_sha256": version.dependency_lock_hash,
        "formal_sbom_sha256": "7" * 64,
        "architectures": ["linux/amd64", "linux/arm64"],
        "receipts": {
            name: format(index + 2, "x") * 64
            for index, name in enumerate(receipt_names)
        },
        "gates": {
            "dependency_integrity": True,
            "license_inventory_policy": True,
            "tracked_source_secret_scan": True,
        },
        "advisory_database_checked": False,
        "vulnerability_status": "NOT_EVALUATED",
        "legal_review_complete": False,
        "aggregate_receipt_sha256": "d" * 64,
    }
    payload = {
        "schema_version": "standard_astro_formal_build_attestation_v2",
        "attestation_id": str(uuid.uuid4()),
        "candidate_id": str(candidate.id),
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
        "source_tree_sha256": version.code_tree_hash,
        "git_commit": git_commit,
        "dependency_lock_sha256": version.dependency_lock_hash,
        "formal_sbom_sha256": "7" * 64,
        "test_report_sha256": "8" * 64,
        "release_audit": release_audit,
        "tests_passed": True,
        "subject": {
            "image": f"ghcr.io/{GITHUB_REPOSITORY}/science-worker",
            "digest": FORMAL_IMAGE,
        },
        "build_identity": {
            "github_repository": GITHUB_REPOSITORY,
            "github_workflow_ref": GITHUB_WORKFLOW_REF,
            "github_workflow_sha": workflow_sha,
            "github_run_id": "123456789",
            "github_run_attempt": 1,
        },
        "sigstore": {
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "certificate_identity": OIDC_SUBJECT,
            "bundle_sha256": "9" * 64,
            "verification_record_sha256": "c" * 64,
        },
        "provenance_sha256": "a" * 64,
        "verification_method": "github_oidc_cosign_plus_ed25519_callback_v2",
        "build_metadata": {
            "candidate_id": str(candidate.id),
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "source_commit": git_commit,
            "source_tree_sha256": version.code_tree_hash,
            "formal_worker_image_digest": FORMAL_IMAGE,
            "tests_passed": True,
            "platforms": ["linux/amd64", "linux/arm64"],
            "image": f"ghcr.io/{GITHUB_REPOSITORY}/science-worker",
            "repository": GITHUB_REPOSITORY,
            "workflow_ref": GITHUB_WORKFLOW_REF,
            "workflow_sha": workflow_sha,
            "run_id": "123456789",
            "run_attempt": "1",
        },
        "built_at": built_at.isoformat(),
    }
    if formal_build_attempt_id is not None:
        payload["build_metadata"]["formal_build_attempt_id"] = str(
            formal_build_attempt_id
        )
    report = {
        "schema_version": "standard_astro_formal_build_attestation_bundle_v2",
        "payload": payload,
    }
    return _resign_formal_build_report(report)


async def test_formal_build_is_protected_and_registration_stays_pending(
    app_client, db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session, suffix="formal", formal_source=True
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
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_failure_result_secret",
        "failure-" + "y" * 40,
    )
    monkeypatch.setattr(settings, "foundry_formal_build_oidc_subject", OIDC_SUBJECT)
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_attestation_verification_keys",
        json.dumps({ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY}),
    )
    monkeypatch.setattr(
        settings, "foundry_formal_build_github_repository", GITHUB_REPOSITORY
    )
    monkeypatch.setattr(settings, "foundry_formal_build_github_workflow", GITHUB_WORKFLOW)
    monkeypatch.setattr(settings, "foundry_formal_build_github_ref", "main")
    denied = await app_client.post(
        "/api/internal/foundry/formal-build-attestations",
        headers={"Authorization": "Bearer wrong"},
        json=report,
    )
    assert denied.status_code == 403
    failure_bearer_denied = await app_client.post(
        "/api/internal/foundry/formal-build-attestations",
        headers={"Authorization": "Bearer " + "failure-" + "y" * 40},
        json=report,
    )
    assert failure_bearer_denied.status_code == 403
    with pytest.raises(FoundryCatalogError) as missing_dispatch:
        await record_formal_build_attestation(
            db_session,
            attestation_report=report,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert missing_dispatch.value.error_class == (
        "formal_build_legacy_dispatch_missing"
    )
    wrong_binding = _formal_build_source_binding(version)
    wrong_binding["source_commit"] = "f" * 40
    await _append_event(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="FORMAL_BUILD_DISPATCHED",
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload=wrong_binding,
    )
    await db_session.commit()
    with pytest.raises(FoundryCatalogError) as mismatched_dispatch:
        await record_formal_build_attestation(
            db_session,
            attestation_report=report,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert mismatched_dispatch.value.error_class == (
        "formal_build_legacy_dispatch_missing"
    )
    await _append_legacy_formal_build_dispatch(
        db_session,
        candidate=candidate,
        version=version,
    )
    accepted = await app_client.post(
        "/api/internal/foundry/formal-build-attestations",
        headers={"Authorization": "Bearer " + "build-" + "x" * 40},
        json=report,
    )
    assert accepted.status_code == 201, accepted.text
    attestation = await record_formal_build_attestation(
        db_session,
        attestation_report=report,
        expected_oidc_subject=OIDC_SUBJECT,
        expected_github_repository=GITHUB_REPOSITORY,
        expected_github_workflow=GITHUB_WORKFLOW,
        expected_github_ref="main",
        trusted_attestation_public_keys={
            ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
        },
    )
    assert attestation.formal_worker_image_digest == FORMAL_IMAGE
    assert attestation.formal_release_audit_hash == "d" * 64
    assert (
        attestation.formal_release_audit_receipts["vulnerability_status"]
        == "NOT_EVALUATED"
    )
    assert version.validation_runner_image_digest == VALIDATION_IMAGE

    fake_registry = types.ModuleType("app.services.workflow_registry_v2")
    fake_registry.builtin_registry_identity = lambda: {
        "registry_epoch": "2026-07-21.1",
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
    static_gate_calls: list[dict] = []
    fake_registry.assert_registry_entry_static_compatible = (
        lambda entry: static_gate_calls.append(entry) or entry
    )
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
    assert release.manifest["context"]["formal_build_release_audit_sha256"] == (
        attestation.formal_release_audit_hash
    )
    assert str(reviewer.id) not in json.dumps(release.manifest, sort_keys=True)
    assert static_gate_calls == [entry.release_entry]


async def test_formal_build_callback_verifies_signature_and_workflow_identity(
    db_session,
):
    candidate, version, *_ = await _candidate_with_demo(
        db_session, suffix="formal_attestation_security"
    )
    reviewer = await _user(db_session, "formal_attestation_security_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="SCIENTIFIC",
        decision="APPROVED",
        comment="Exact-version security fixture approval",
    )
    report = _formal_build_report(
        candidate,
        version,
        built_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    tampered = json.loads(json.dumps(report))
    tampered["payload"]["subject"]["digest"] = "sha256:" + "f" * 64
    tampered["payload_sha256"] = sha256_json(tampered["payload"])
    tampered.pop("attestation_artifact_sha256")
    tampered["attestation_artifact_sha256"] = sha256_json(tampered)
    with pytest.raises(FoundryCatalogError) as invalid_signature:
        await record_formal_build_attestation(
            db_session,
            attestation_report=tampered,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert (
        invalid_signature.value.error_class
        == "formal_build_attestation_signature_invalid"
    )

    wrong_identity = json.loads(json.dumps(report))
    wrong_identity["payload"]["build_identity"]["github_repository"] = (
        "attacker/fork"
    )
    _resign_formal_build_report(wrong_identity)
    with pytest.raises(FoundryCatalogError) as identity_mismatch:
        await record_formal_build_attestation(
            db_session,
            attestation_report=wrong_identity,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert identity_mismatch.value.error_class == "formal_build_identity_mismatch"

    failed_supply_gate = json.loads(json.dumps(report))
    failed_supply_gate["payload"]["release_audit"]["status"] = "FAILED"
    _resign_formal_build_report(failed_supply_gate)
    with pytest.raises(FoundryCatalogError) as supply_gate_rejected:
        await record_formal_build_attestation(
            db_session,
            attestation_report=failed_supply_gate,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert supply_gate_rejected.value.error_class == "formal_release_audit_invalid"


async def test_generated_patch_cannot_bypass_protected_materialization_receipt(
    db_session,
):
    candidate, version, *_ = await _candidate_with_demo(
        db_session,
        suffix="formal_materialization_bypass",
        formal_source=True,
        source_materialization_required=True,
    )
    reviewer = await _user(db_session, "formal_materialization_bypass_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Review the generated patch without materializing it",
    )
    report = _formal_build_report(
        candidate,
        version,
        built_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    # Reproduce the bypass: a manually dispatched protected workflow attests a
    # non-base commit with the Draft's post-patch tree, but no protected
    # materialization receipt ever created an exact materialized version.
    unledgered_commit = "f" * 40
    report["payload"]["git_commit"] = unledgered_commit
    report["payload"]["build_metadata"]["source_commit"] = unledgered_commit
    _resign_formal_build_report(report)

    with pytest.raises(FoundryCatalogError) as callback_rejected:
        await record_formal_build_attestation(
            db_session,
            attestation_report=report,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert (
        callback_rejected.value.error_class
        == "formal_materialization_receipt_required"
    )

    # Registration repeats the source-provenance gate.  Even a row inserted by
    # a legacy/manual path cannot turn the unledgered build into a release.
    payload = report["payload"]
    forged_attestation = FoundryFormalBuildAttestation(
        id=uuid.UUID(payload["attestation_id"]),
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        source_tree_hash=version.code_tree_hash,
        git_commit=unledgered_commit,
        dependency_lock_hash=version.dependency_lock_hash,
        formal_sbom_hash=str(payload["formal_sbom_sha256"]),
        test_report_hash=str(payload["test_report_sha256"]),
        formal_release_audit_hash=str(
            payload["release_audit"]["aggregate_receipt_sha256"]
        ),
        formal_release_audit_receipts=dict(payload["release_audit"]),
        formal_worker_image_digest=FORMAL_IMAGE,
        github_repository=GITHUB_REPOSITORY,
        github_workflow_ref=GITHUB_WORKFLOW_REF,
        github_workflow_sha=str(payload["build_identity"]["github_workflow_sha"]),
        oidc_issuer=str(payload["sigstore"]["oidc_issuer"]),
        oidc_subject=str(payload["sigstore"]["certificate_identity"]),
        attestation_signing_key_id=ATTESTATION_KEY_ID,
        sigstore_bundle_hash=str(payload["sigstore"]["bundle_sha256"]),
        sigstore_verification_record_hash=str(
            payload["sigstore"]["verification_record_sha256"]
        ),
        provenance_hash=str(payload["provenance_sha256"]),
        build_metadata=dict(payload["build_metadata"]),
        receipt_hash=str(report["payload_sha256"]),
        attestation_artifact_hash=str(report["attestation_artifact_sha256"]),
        built_at=datetime.fromisoformat(str(payload["built_at"])),
    )
    db_session.add(forged_attestation)
    await db_session.commit()

    with pytest.raises(FoundryCatalogError) as registration_rejected:
        await register_candidate_version(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            build_attestation_id=forged_attestation.id,
            registrar_user_id=reviewer.id,
        )
    assert (
        registration_rejected.value.error_class
        == "formal_materialization_receipt_required"
    )


async def test_formal_build_dispatch_is_server_bound_and_idempotent(
    app_client, db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session,
        suffix="formal_dispatch",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_dispatch_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve the exact composition candidate",
    )
    monkeypatch.setenv("FOUNDRY_HUMAN_REVIEWER_USERNAMES", reviewer.username)
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_formal_build_dispatch_backend", "github_actions"
    )
    monkeypatch.setattr(
        settings, "foundry_formal_build_github_repository", "astro/platform"
    )
    monkeypatch.setattr(
        settings, "foundry_formal_build_github_workflow", "foundry-formal-worker.yml"
    )
    monkeypatch.setattr(settings, "foundry_formal_build_github_ref", "main")
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_github_token",
        "formal-dispatch-token-" + "x" * 32,
    )
    dispatched: list[dict] = []
    dispatch_started = asyncio.Event()
    allow_dispatch_to_finish = asyncio.Event()

    async def _dispatch(_config, **kwargs):
        dispatched.append(kwargs)
        dispatch_started.set()
        await allow_dispatch_to_finish.wait()

    monkeypatch.setattr("app.api.foundry.dispatch_formal_worker_build", _dispatch)
    payload = {
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
    }
    headers = {"Authorization": f"Bearer {create_access_token(reviewer.id)}"}
    forged = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=headers,
        json={**payload, "source_commit": "f" * 40},
    )
    assert forged.status_code == 422
    assert dispatched == []
    first_task = asyncio.create_task(
        app_client.post(
            f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
            headers=headers,
            json=payload,
        )
    )
    await asyncio.wait_for(dispatch_started.wait(), timeout=2)
    concurrent = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=headers,
        json=payload,
    )
    assert concurrent.status_code == 202, concurrent.text
    assert concurrent.json()["status"] == "DISPATCH_PENDING"
    assert concurrent.json()["attempt_number"] == 1
    assert concurrent.json()["idempotent_replay"] is True
    assert len(dispatched) == 1
    allow_dispatch_to_finish.set()
    first = await first_task
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "DISPATCHED"
    assert first.json()["retry_after"] is not None
    assert first.json()["idempotent_replay"] is False
    attempt_id = first.json()["formal_build_attempt_id"]
    assert first.json()["attempt_number"] == 1
    assert dispatched == [
        {
            "candidate_id": str(candidate.id),
            "candidate_version_id": str(version.id),
            "formal_build_attempt_id": uuid.UUID(attempt_id),
            "candidate_version_hash": version.version_hash,
            "source_commit": "6" * 40,
            "source_tree_sha256": "4" * 64,
        }
    ]
    repeated = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=headers,
        json=payload,
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["status"] == "DISPATCHED"
    assert repeated.json()["idempotent_replay"] is True
    assert repeated.json()["formal_build_attempt_id"] == attempt_id
    assert len(dispatched) == 1
    events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "FORMAL_BUILD_REQUESTED",
                            "FORMAL_BUILD_ATTEMPT_RESERVED",
                            "FORMAL_BUILD_DISPATCHED",
                        }
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    assert [event.event_type for event in events] == [
        "FORMAL_BUILD_REQUESTED",
        "FORMAL_BUILD_ATTEMPT_RESERVED",
        "FORMAL_BUILD_DISPATCHED",
    ]
    attestation = await record_formal_build_attestation(
        db_session,
        attestation_report=_formal_build_report(
            candidate,
            version,
            built_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            formal_build_attempt_id=uuid.UUID(attempt_id),
        ),
        expected_oidc_subject=OIDC_SUBJECT,
        expected_github_repository=GITHUB_REPOSITORY,
        expected_github_workflow=GITHUB_WORKFLOW,
        expected_github_ref="main",
        trusted_attestation_public_keys={
            ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
        },
    )
    assert attestation.build_metadata["formal_build_attempt_id"] == attempt_id


async def test_formal_build_failure_callback_retries_idempotently_and_is_bounded(
    app_client, db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session,
        suffix="formal_retry",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_retry_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve bounded formal build retries",
    )
    monkeypatch.setenv("FOUNDRY_HUMAN_REVIEWER_USERNAMES", reviewer.username)
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_formal_build_dispatch_backend", "github_actions"
    )
    monkeypatch.setattr(
        settings, "foundry_formal_build_github_repository", "astro/platform"
    )
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_github_workflow",
        "foundry-formal-worker.yml",
    )
    monkeypatch.setattr(settings, "foundry_formal_build_github_ref", "main")
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_github_token",
        "formal-dispatch-token-" + "x" * 32,
    )
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_result_secret",
        "formal-success-" + "s" * 32,
    )
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_failure_result_secret",
        "formal-failure-" + "z" * 32,
    )
    dispatched: list[dict] = []

    async def _dispatch(_config, **kwargs):
        dispatched.append(kwargs)

    monkeypatch.setattr("app.api.foundry.dispatch_formal_worker_build", _dispatch)
    payload = {
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
    }
    admin_headers = {"Authorization": f"Bearer {create_access_token(reviewer.id)}"}
    callback_headers = {
        "Authorization": "Bearer " + "formal-failure-" + "z" * 32
    }
    success_callback_headers = {
        "Authorization": "Bearer " + "formal-success-" + "s" * 32
    }

    async def dispatch_attempt(expected_number: int) -> dict:
        response = await app_client.post(
            f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
            headers=admin_headers,
            json=payload,
        )
        assert response.status_code == 202, response.text
        assert response.json()["attempt_number"] == expected_number
        return response.json()

    async def fail_attempt(attempt: dict, run_id: int) -> dict:
        failure = {
            "schema_version": 1,
            "formal_build_attempt_id": attempt["formal_build_attempt_id"],
            "candidate_id": str(candidate.id),
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "source_commit": "6" * 40,
            "source_tree_sha256": "4" * 64,
            "status": "FAILED",
            "failure_class": "formal_build_workflow_failed",
            "failed_stage": "image_build",
            "workflow_conclusion": "failure",
            "github_repository": "astro/platform",
            "github_workflow_ref": (
                "astro/platform/.github/workflows/foundry-formal-worker.yml"
                "@refs/heads/main"
            ),
            "github_workflow_sha": "9" * 40,
            "github_run_id": str(run_id),
            "github_run_attempt": "1",
        }
        response = await app_client.post(
            "/api/internal/foundry/formal-build-attempts/"
            f"{attempt['formal_build_attempt_id']}/failure",
            headers=callback_headers,
            json=failure,
        )
        assert response.status_code == 200, response.text
        return {"response": response.json(), "payload": failure}

    first = await dispatch_attempt(1)
    first_failure = await fail_attempt(first, 1001)
    success_bearer_denied = await app_client.post(
        "/api/internal/foundry/formal-build-attempts/"
        f"{first['formal_build_attempt_id']}/failure",
        headers=success_callback_headers,
        json=first_failure["payload"],
    )
    assert success_bearer_denied.status_code == 403
    replay = await app_client.post(
        "/api/internal/foundry/formal-build-attempts/"
        f"{first['formal_build_attempt_id']}/failure",
        headers=callback_headers,
        json=first_failure["payload"],
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True

    second = await dispatch_attempt(2)
    assert second["formal_build_attempt_id"] != first["formal_build_attempt_id"]
    await fail_attempt(second, 1002)
    third = await dispatch_attempt(3)
    await fail_attempt(third, 1003)
    exhausted = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=admin_headers,
        json=payload,
    )
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"]["error_class"] == (
        "formal_build_attempts_exhausted"
    )
    assert len(dispatched) == 3


async def test_formal_build_api_holds_uncertain_delivery_without_redispatch(
    app_client, db_session, monkeypatch
):
    candidate, version, *_ = await _candidate_with_demo(
        db_session,
        suffix="formal_api_uncertain",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_api_uncertain_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve uncertain delivery handling",
    )
    monkeypatch.setenv("FOUNDRY_HUMAN_REVIEWER_USERNAMES", reviewer.username)
    monkeypatch.setattr(settings, "foundry_registration_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_formal_build_dispatch_backend", "github_actions"
    )
    monkeypatch.setattr(
        settings, "foundry_formal_build_github_repository", "astro/platform"
    )
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_github_workflow",
        "foundry-formal-worker.yml",
    )
    monkeypatch.setattr(settings, "foundry_formal_build_github_ref", "main")
    monkeypatch.setattr(
        settings,
        "foundry_formal_build_github_token",
        "formal-dispatch-token-" + "x" * 32,
    )
    dispatch_calls = 0

    async def _uncertain_dispatch(_config, **_kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise FoundryCIDispatchError(
            "foundry_ci_dispatch_unavailable",
            retryable=True,
            delivery_uncertain=True,
        )

    monkeypatch.setattr(
        "app.api.foundry.dispatch_formal_worker_build", _uncertain_dispatch
    )
    headers = {"Authorization": f"Bearer {create_access_token(reviewer.id)}"}
    payload = {
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
    }
    first = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=headers,
        json=payload,
    )
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "DISPATCH_UNCERTAIN"
    assert first.json()["retry_after"] is not None
    second = await app_client.post(
        f"/api/admin/foundry/candidates/{candidate.id}/formal-build",
        headers=headers,
        json=payload,
    )
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "DISPATCH_UNCERTAIN"
    assert second.json()["formal_build_attempt_id"] == first.json()[
        "formal_build_attempt_id"
    ]
    assert second.json()["idempotent_replay"] is True
    assert dispatch_calls == 1


async def test_stale_formal_build_attempt_times_out_before_one_retry(
    db_session, monkeypatch
):
    candidate, version, _run, _demo, _report = await _candidate_with_demo(
        db_session,
        suffix="formal_timeout",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_timeout_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve timeout recovery",
    )
    started = datetime.now(timezone.utc)
    clock = [started]

    def _clock_tick():
        value = clock[0]
        clock[0] = value + timedelta(microseconds=1)
        return value

    monkeypatch.setattr("app.services.foundry_catalog._utc_now", _clock_tick)
    first = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started,
    )
    assert first.attempt_id is not None
    assert first.dispatch_status == "DISPATCH_PENDING"
    reserved_replay = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started + timedelta(seconds=1),
    )
    assert reserved_replay.attempt_id == first.attempt_id
    assert reserved_replay.already_active is True
    assert reserved_replay.dispatch_status == "DISPATCH_PENDING"
    await record_formal_build_dispatch(
        db_session,
        binding=first.binding,
        attempt_id=first.attempt_id,
        dispatched=True,
    )
    with pytest.raises(FoundryCatalogError) as conflicting_dispatch:
        await record_formal_build_dispatch(
            db_session,
            binding=first.binding,
            attempt_id=first.attempt_id,
            dispatched=False,
            failure_class="foundry_ci_dispatch_unavailable",
            retryable=True,
        )
    assert conflicting_dispatch.value.error_class == (
        "formal_build_dispatch_result_conflict"
    )
    retry_time = started + timedelta(hours=7)
    clock[0] = retry_time
    retry = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=retry_time,
    )
    replay = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=retry_time + timedelta(minutes=1),
    )

    assert retry.attempt_number == 2
    assert retry.attempt_id != first.attempt_id
    assert retry.already_active is False
    assert replay.attempt_id == retry.attempt_id
    assert replay.already_active is True
    events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.event_type
                    == "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].event_payload["formal_build_attempt_id"] == str(
        first.attempt_id
    )
    late_report = _formal_build_report(
        candidate,
        version,
        built_at=started + timedelta(hours=1),
        formal_build_attempt_id=first.attempt_id,
    )
    with pytest.raises(FoundryCatalogError) as late_success:
        await record_formal_build_attestation(
            db_session,
            attestation_report=late_report,
            expected_oidc_subject=OIDC_SUBJECT,
            expected_github_repository=GITHUB_REPOSITORY,
            expected_github_workflow=GITHUB_WORKFLOW,
            expected_github_ref="main",
            trusted_attestation_public_keys={
                ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
            },
        )
    assert late_success.value.error_class == "formal_build_attempt_closed"


async def test_crashed_dispatch_reservation_uses_short_lease_before_retry(
    db_session, monkeypatch
):
    candidate, version, *_ = await _candidate_with_demo(
        db_session,
        suffix="formal_reservation_crash",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_reservation_crash_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve reservation crash recovery",
    )
    started = datetime.now(timezone.utc)
    clock = [started]

    def _clock_tick():
        value = clock[0]
        clock[0] = value + timedelta(microseconds=1)
        return value

    monkeypatch.setattr("app.services.foundry_catalog._utc_now", _clock_tick)
    first = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started,
    )
    pending = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started + timedelta(minutes=4),
    )
    assert pending.attempt_id == first.attempt_id
    assert pending.dispatch_status == "DISPATCH_PENDING"
    assert pending.already_active is True

    clock[0] = started + timedelta(minutes=6)
    retry = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=clock[0],
    )
    assert retry.attempt_number == 2
    assert retry.attempt_id != first.attempt_id
    assert retry.dispatch_status == "DISPATCH_PENDING"
    timeout_event = await db_session.scalar(
        select(FoundryCandidateEvent).where(
            FoundryCandidateEvent.candidate_id == candidate.id,
            FoundryCandidateEvent.event_type
            == "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
        )
    )
    assert timeout_event is not None
    assert timeout_event.event_payload["timeout_phase"] == "DISPATCH_PENDING"
    assert timeout_event.event_payload["timeout_seconds"] == 300


async def test_uncertain_dispatch_waits_on_short_lease_before_retry(
    db_session, monkeypatch
):
    candidate, version, *_ = await _candidate_with_demo(
        db_session,
        suffix="formal_dispatch_uncertain",
        formal_source=True,
    )
    reviewer = await _user(db_session, "formal_dispatch_uncertain_reviewer")
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer.id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Approve uncertain dispatch recovery",
    )
    started = datetime.now(timezone.utc)
    clock = [started]

    def _clock_tick():
        value = clock[0]
        clock[0] = value + timedelta(microseconds=1)
        return value

    monkeypatch.setattr("app.services.foundry_catalog._utc_now", _clock_tick)
    first = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started,
    )
    assert first.attempt_id is not None
    await record_formal_build_dispatch(
        db_session,
        binding=first.binding,
        attempt_id=first.attempt_id,
        dispatched=False,
        failure_class="foundry_ci_dispatch_unavailable",
        retryable=True,
        delivery_uncertain=True,
    )
    active = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=started + timedelta(minutes=4),
    )
    assert active.attempt_id == first.attempt_id
    assert active.dispatch_status == "DISPATCH_UNCERTAIN"
    assert active.already_active is True

    clock[0] = started + timedelta(minutes=6)
    retry = await request_formal_build_dispatch(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_user_id=reviewer.id,
        now=clock[0],
    )
    assert retry.attempt_number == 2
    assert retry.attempt_id != first.attempt_id
    timeout_event = await db_session.scalar(
        select(FoundryCandidateEvent).where(
            FoundryCandidateEvent.candidate_id == candidate.id,
            FoundryCandidateEvent.event_type
            == "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
        )
    )
    assert timeout_event is not None
    assert timeout_event.event_payload["timeout_phase"] == "DISPATCH_UNCERTAIN"


async def test_pending_release_requests_form_a_complete_deterministic_delta_chain(
    db_session, monkeypatch
):
    actor = await _user(db_session, "release_actor")
    fake_registry = types.ModuleType("app.services.workflow_registry_v2")
    fake_registry.builtin_registry_identity = lambda: {
        "registry_epoch": "base.1",
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
    assert first.manifest["requested_by_actor_hash"].startswith("sha256:")
    assert first.manifest["requested_by_actor_hash"] == "sha256:" + sha256_json(
        {
            "actor_user_id": str(actor.id),
            "release_request_id": str(first.id),
            "domain": "foundry_registry_release_request_v1",
        }
    )
    assert str(actor.id) not in str(first.manifest)
    assert not list((await db_session.execute(select(WorkflowRegistryEntry))).scalars())
    assert len(list((await db_session.execute(select(WorkflowRegistryRelease))).scalars())) == 2


async def test_registering_new_workflow_version_atomically_supersedes_old_version(
    db_session, monkeypatch
):
    workflow_id = "candidate_shared_cosmology_workflow"
    old_candidate, old_version, *_ = await _candidate_with_demo(
        db_session,
        suffix="supersede_old",
        workflow_id=workflow_id,
        workflow_version="1.0.0",
        formal_source=True,
    )
    new_candidate, new_version, *_ = await _candidate_with_demo(
        db_session,
        suffix="supersede_new",
        workflow_id=workflow_id,
        workflow_version="2.0.0",
        formal_source=True,
    )
    reviewer = await _user(db_session, "supersede_reviewer")
    for candidate, version in (
        (old_candidate, old_version),
        (new_candidate, new_version),
    ):
        await review_candidate_version(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            candidate_version_hash=version.version_hash,
            reviewer_user_id=reviewer.id,
            review_scope="SCIENTIFIC",
            decision="APPROVED",
            comment="Exact-version scientific approval",
        )
        await _append_legacy_formal_build_dispatch(
            db_session,
            candidate=candidate,
            version=version,
        )
    old_attestation = await record_formal_build_attestation(
        db_session,
        attestation_report=_formal_build_report(
            old_candidate,
            old_version,
            built_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ),
        expected_oidc_subject=OIDC_SUBJECT,
        expected_github_repository=GITHUB_REPOSITORY,
        expected_github_workflow=GITHUB_WORKFLOW,
        expected_github_ref="main",
        trusted_attestation_public_keys={
            ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
        },
    )
    new_attestation = await record_formal_build_attestation(
        db_session,
        attestation_report=_formal_build_report(
            new_candidate,
            new_version,
            built_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        ),
        expected_oidc_subject=OIDC_SUBJECT,
        expected_github_repository=GITHUB_REPOSITORY,
        expected_github_workflow=GITHUB_WORKFLOW,
        expected_github_ref="main",
        trusted_attestation_public_keys={
            ATTESTATION_KEY_ID: ATTESTATION_PUBLIC_KEY
        },
    )

    fake_registry = types.ModuleType("app.services.workflow_registry_v2")
    fake_registry.builtin_registry_identity = lambda: {
        "registry_epoch": "builtin.supersede.1",
        "registry_hash": "sha256:" + "c" * 64,
    }

    def _fake_release_entry(payload):
        spec = payload["workflow_spec"]
        return {
            "candidate_id": payload["candidate_id"],
            "candidate_version": payload["candidate_version"],
            "candidate_version_hash": payload["candidate_version_hash"],
            "workflow_spec_hash": payload["workflow_spec_hash"],
            "approval_attestation_hash": "sha256:" + "a" * 64,
            "build_attestation_hash": "sha256:" + "b" * 64,
            "worker_image_digest": payload["worker_image_digest"],
            "workflow": {
                "workflow_id": spec["workflow_id"],
                "version": spec["workflow_version"],
                "state": "REGISTERED",
            },
            "tools": [],
            "registry_entry_hash": "sha256:" + payload["candidate_version_hash"],
            "installation_status": "PENDING_RELEASE",
            "runtime_registry_modified": False,
        }

    fake_registry.build_registry_entry_from_approved_candidate = _fake_release_entry
    fake_registry.assert_registry_entry_static_compatible = lambda entry: entry
    monkeypatch.setitem(sys.modules, "app.services.workflow_registry_v2", fake_registry)
    old_entry, first_release = await register_candidate_version(
        db_session,
        candidate_id=old_candidate.id,
        candidate_version_id=old_version.id,
        candidate_version_hash=old_version.version_hash,
        build_attestation_id=old_attestation.id,
        registrar_user_id=reviewer.id,
    )
    _new_entry, second_release = await register_candidate_version(
        db_session,
        candidate_id=new_candidate.id,
        candidate_version_id=new_version.id,
        candidate_version_hash=new_version.version_hash,
        build_attestation_id=new_attestation.id,
        registrar_user_id=reviewer.id,
    )
    changes = second_release.manifest["status_changes"]
    assert second_release.manifest["previous_request_hash"] == first_release.manifest_hash
    assert second_release.manifest["request_kind"] == (
        "REGISTER_CANDIDATE_AND_SUPERSEDE"
    )
    assert changes == [
        {
            "registry_entry_id": str(old_entry.id),
            "registry_entry_hash": old_entry.registry_entry_hash,
            "workflow_id": workflow_id,
            "workflow_version": "1.0.0",
            "requested_status": "SUPERSEDED",
            "reason": f"superseded_by={workflow_id}@2.0.0",
            "superseded_by_workflow_id": workflow_id,
            "superseded_by_workflow_version": "2.0.0",
        }
    ]
    assert [
        operation["operation"]
        for operation in second_release.manifest["new_operations"]
    ] == ["UPSERT_ENTRY", "SET_ENTRY_STATUS"]
    await db_session.refresh(old_entry)
    await db_session.refresh(old_candidate)
    assert old_entry.status == "PENDING_RELEASE"
    assert old_candidate.status == "APPROVED"
