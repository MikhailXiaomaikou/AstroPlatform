"""AI Draft lane: privacy-minimized dispatch and immutable host ingestion."""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.models.foundry_records import (
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryDemoRun,
    FoundryValidationRun,
)
from app.services.foundry_catalog import (
    AI_DRAFT_MAX_ATTEMPTS,
    AI_DRAFT_WORKFLOW_LEASE,
    FoundryCatalogError,
    queue_ai_draft,
    reconcile_expired_ai_draft_runs,
    record_ai_draft_dispatch,
    record_ai_draft_result,
    serialize_capability_gaps,
    sha256_json,
)
from app.services.foundry_candidate_identity import candidate_version_sha256
from app.services.foundry_demo_runner import (
    run_candidate_demo,
    validate_candidate_bundle,
)
from app.services.foundry_draft_dispatch import (
    FoundryDraftDispatchError,
    dispatch_candidate_draft,
)
from app.services.foundry_validation_dispatch import FoundryValidationDispatchError
from app.services.foundry_source_tree import (
    FoundrySourceTreeError,
    assert_clean_checkout,
    tracked_source_tree_hash,
)
from scripts import run_foundry_ai_draft_job
from scripts import prepare_foundry_candidate_validation


VALIDATION_IMAGE = "sha256:" + "a" * 64
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


async def _candidate(db_session, suffix: str) -> FoundryCandidate:
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": suffix,
        "research_domain": "cosmology",
    }
    candidate = FoundryCandidate(
        gap_fingerprint=sha256_json(descriptor),
        gap_code="registered_workflow_missing",
        gap_descriptor=descriptor,
        status="BUILDING",
        risk_level="R1",
        generation_route="DATA_ADAPTER",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    return candidate


def _candidate_version() -> dict:
    return {
        "candidate_bundle": {
            "schema_version": 1,
            "candidate_id": "desi_dr2_ai_draft_v1",
            # A Draft cannot choose its immutable sequence number.
            "candidate_version": 0,
            "proposed_workflow_id": "desi_dr2_ai_draft_workflow_v1",
            "entrypoint_id": "desi_dr2_official_chain_summary_demo_v1",
            "risk_level": "R1",
            "workflow_spec": {
                "workflow_id": "desi_dr2_ai_draft_workflow_v1",
                "workflow_version": "1.0.0-candidate.1",
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
                "kind": "ai_draft_provider_contract_v1",
                "model": "provider/model-v1",
                "prompt_or_claim_stored": False,
                "generated_code_executed_by_draft_job": False,
                "source_hash_algorithm": "standard_astro_tracked_source_manifest_v1",
                "source_base_commit": "a" * 40,
                "source_base_tree_sha256": "4" * 64,
                "source_tree_sha256": "4" * 64,
                "source_materialization_required": False,
            },
            "limitations": ["Non-formal candidate; validation has not run."],
            "output_policy": {
                "evidence_class": "NON_FORMAL_DEMO",
                "publication_ready": False,
                "claim_eligible": False,
                "evidence_pack_allowed": False,
            },
        },
        "validation_runner_image_digest": VALIDATION_IMAGE,
        "code_tree_hash": "4" * 64,
        "patch_hash": EMPTY_SHA256,
        "sbom_hash": "6" * 64,
    }


def _report(candidate: FoundryCandidate, draft_run_id: uuid.UUID) -> dict:
    report = {
        "schema_version": 1,
        "draft_run_id": str(draft_run_id),
        "candidate_id": str(candidate.id),
        "gap_fingerprint": candidate.gap_fingerprint,
        "gap_code": candidate.gap_code,
        "gap_descriptor": candidate.gap_descriptor,
        "generation_route": candidate.generation_route,
        "risk_level": candidate.risk_level,
        "status": "SUCCEEDED",
        "candidate_version": _candidate_version(),
        "provider_receipt": {
            "contract_version": 1,
            "provider": "provider",
            "model": "model-v1",
            "request_id_sha256": "7" * 64,
            "prompt_or_user_data_stored": False,
            "generated_code_executed": False,
            "tests_executed": False,
        },
        "artifact_manifest": [
            {
                "path": "candidate.json",
                "kind": "CANDIDATE_BUNDLE",
                "sha256": "8" * 64,
                "bytes": 1024,
            },
            {
                "path": "candidate.patch",
                "kind": "PATCH",
                "sha256": EMPTY_SHA256,
                "bytes": 0,
            },
            {
                "path": "sbom.json",
                "kind": "SBOM",
                "sha256": "6" * 64,
                "bytes": 2,
            },
        ],
        "artifact_receipt": {
            "repository": "astro/platform",
            "workflow_run_id": "123",
            "artifact_id": "456",
            "artifact_name": f"foundry-draft-{draft_run_id}",
            "artifact_sha256": "9" * 64,
        },
        "source_receipt": {
            "hash_algorithm": "standard_astro_tracked_source_manifest_v1",
            "base_commit": "a" * 40,
            "base_source_tree_sha256": "4" * 64,
            "post_patch_source_tree_sha256": "4" * 64,
            "patch_sha256": EMPTY_SHA256,
            "patch_applied": False,
            "changed_paths": [],
            "dependency_lock_sha256": "2" * 64,
            "runner_definition_sha256": "3" * 64,
        },
        "failure_class": None,
    }
    report["draft_result_sha256"] = sha256_json(report)
    return report


async def test_ai_draft_callback_appends_exact_version_and_is_idempotent(
    db_session,
):
    candidate = await _candidate(db_session, "accepted")
    queued, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )
    report = _report(candidate, queued.id)
    version, accepted = await record_ai_draft_result(
        db_session, draft_run_id=queued.id, draft_result=report
    )
    assert version is not None
    assert version.version_number == 1
    assert version.candidate_bundle["candidate_version"] == 1
    assert version.created_by_kind == "AI_DRAFT_JOB"
    assert accepted.actor_kind == "AI_DRAFT_JOB"
    assert accepted.event_type == "AI_DRAFT_RESULT_ACCEPTED"

    replay_version, replay_event = await record_ai_draft_result(
        db_session, draft_run_id=queued.id, draft_result=report
    )
    assert replay_version.id == version.id
    assert replay_event.id == accepted.id

    changed = dict(report)
    changed["artifact_manifest"] = []
    changed["draft_result_sha256"] = sha256_json(
        {key: value for key, value in changed.items() if key != "draft_result_sha256"}
    )
    with pytest.raises(FoundryCatalogError, match="different immutable result"):
        await record_ai_draft_result(
            db_session, draft_run_id=queued.id, draft_result=changed
        )


async def test_failed_dispatch_is_append_only_and_can_be_retried(db_session):
    candidate = await _candidate(db_session, "retry")
    first, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    failure = await record_ai_draft_dispatch(
        db_session,
        draft_run_id=first.id,
        dispatched=False,
        failure_class="draft_dispatch_unavailable",
        retryable=True,
    )
    assert failure.event_type == "AI_DRAFT_DISPATCH_FAILED"
    assert failure.event_payload["retryable"] is True

    retried, retried_created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert retried_created is True
    assert retried.id != first.id


async def test_nonretryable_dispatch_failure_blocks_new_run(db_session):
    candidate = await _candidate(db_session, "terminal-dispatch-failure")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session,
        draft_run_id=queued.id,
        dispatched=False,
        failure_class="draft_dispatch_rejected",
        retryable=False,
    )

    with pytest.raises(FoundryCatalogError, match="failed permanently"):
        await queue_ai_draft(
            db_session,
            candidate_id=candidate.id,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
        )


async def test_uncertain_dispatch_preserves_run_and_accepts_exact_callback(
    db_session,
):
    candidate = await _candidate(db_session, "dispatch-unknown")
    queued, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    outcome = await record_ai_draft_dispatch(
        db_session,
        draft_run_id=queued.id,
        dispatched=False,
        failure_class="draft_dispatch_outcome_unknown",
        retryable=True,
        delivery_uncertain=True,
    )
    assert outcome.event_type == "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN"
    assert outcome.event_payload["delivery_uncertain"] is True

    active, active_created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert active_created is False
    assert active.id == queued.id

    version, accepted = await record_ai_draft_result(
        db_session,
        draft_run_id=queued.id,
        draft_result=_report(candidate, queued.id),
    )
    assert version is not None
    assert accepted.event_type == "AI_DRAFT_RESULT_ACCEPTED"
    replay_version, replay_event = await record_ai_draft_result(
        db_session,
        draft_run_id=queued.id,
        draft_result=_report(candidate, queued.id),
    )
    assert replay_version.id == version.id
    assert replay_event.id == accepted.id


async def test_expired_uncertain_dispatch_retries_and_rejects_late_callback(
    db_session,
):
    candidate = await _candidate(db_session, "expired-dispatch-unknown")
    first, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    unknown = await record_ai_draft_dispatch(
        db_session,
        draft_run_id=first.id,
        dispatched=False,
        failure_class="draft_dispatch_outcome_unknown",
        retryable=True,
        delivery_uncertain=True,
    )
    retry, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
        now=unknown.created_at + AI_DRAFT_WORKFLOW_LEASE + timedelta(seconds=1),
    )

    assert created is True
    assert retry.id != first.id
    assert retry.event_payload["attempt_number"] == 2
    with pytest.raises(FoundryCatalogError, match="lease expired"):
        await record_ai_draft_result(
            db_session,
            draft_run_id=first.id,
            draft_result=_report(candidate, first.id),
        )


async def test_maintenance_reconciles_expired_uncertain_draft(db_session):
    candidate = await _candidate(db_session, "maintenance-expiry")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    unknown = await record_ai_draft_dispatch(
        db_session,
        draft_run_id=queued.id,
        dispatched=False,
        failure_class="draft_dispatch_outcome_unknown",
        retryable=True,
        delivery_uncertain=True,
    )
    reconcile_at = (
        unknown.created_at + AI_DRAFT_WORKFLOW_LEASE + timedelta(seconds=1)
    )

    assert (
        await reconcile_expired_ai_draft_runs(db_session, now=reconcile_at) == 1
    )
    assert (
        await reconcile_expired_ai_draft_runs(db_session, now=reconcile_at) == 0
    )
    expired = await db_session.scalar(
        select(FoundryCandidateEvent).where(
            FoundryCandidateEvent.candidate_id == candidate.id,
            FoundryCandidateEvent.event_type == "AI_DRAFT_LEASE_EXPIRED",
        )
    )
    assert expired is not None
    assert expired.event_payload["draft_run_id"] == str(queued.id)
    assert expired.event_payload["retryable"] is True


async def test_ai_draft_attempts_are_bounded(db_session):
    candidate = await _candidate(db_session, "bounded-attempts")
    latest = None
    for attempt_number in range(1, AI_DRAFT_MAX_ATTEMPTS + 1):
        latest, created = await queue_ai_draft(
            db_session,
            candidate_id=candidate.id,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
        )
        assert created is True
        assert latest.event_payload["attempt_number"] == attempt_number
        await record_ai_draft_dispatch(
            db_session,
            draft_run_id=latest.id,
            dispatched=False,
            failure_class="draft_dispatch_unavailable",
            retryable=True,
        )

    with pytest.raises(FoundryCatalogError, match="bounded attempt limit"):
        await queue_ai_draft(
            db_session,
            candidate_id=candidate.id,
            actor_kind="HUMAN_ADMIN",
            actor_user_id=None,
        )


async def test_known_failed_dispatch_rejects_late_callback(db_session):
    candidate = await _candidate(db_session, "known-dispatch-failure")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session,
        draft_run_id=queued.id,
        dispatched=False,
        failure_class="draft_dispatch_rejected",
        retryable=True,
    )

    with pytest.raises(FoundryCatalogError, match="failed Draft dispatch"):
        await record_ai_draft_result(
            db_session,
            draft_run_id=queued.id,
            draft_result=_report(candidate, queued.id),
        )


async def test_draft_internal_callback_requires_independent_secret(
    app_client, db_session, monkeypatch
):
    candidate = await _candidate(db_session, "callback")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )
    report = _report(candidate, queued.id)
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "foundry_draft_result_secret", "draft-" + "x" * 40)
    denied = await app_client.post(
        f"/api/internal/foundry/draft-runs/{queued.id}/result",
        headers={"Authorization": "Bearer wrong"},
        json=report,
    )
    assert denied.status_code == 403
    accepted = await app_client.post(
        f"/api/internal/foundry/draft-runs/{queued.id}/result",
        headers={"Authorization": "Bearer " + "draft-" + "x" * 40},
        json=report,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "VERSION_APPENDED"
    assert accepted.json()["actor_kind"] == "AI_DRAFT_JOB"


async def test_authenticated_callback_closes_post_dispatch_ledger_crash_window(
    app_client, db_session, monkeypatch
):
    candidate = await _candidate(db_session, "queued-callback-proof")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    # Simulate GitHub accepting workflow_dispatch immediately before the API
    # process crashes, leaving only the durable QUEUED reservation.
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "foundry_draft_result_secret", "draft-" + "z" * 40)
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    url = f"/api/internal/foundry/draft-runs/{queued.id}/result"
    report = _report(candidate, queued.id)

    denied = await app_client.post(
        url,
        headers={"Authorization": "Bearer wrong"},
        json=report,
    )
    accepted = await app_client.post(
        url,
        headers={"Authorization": "Bearer " + "draft-" + "z" * 40},
        json=report,
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "VERSION_APPENDED"
    assert accepted.json()["candidate_version_id"] is not None


async def test_callback_first_makes_late_dispatch_receipt_a_noop(db_session):
    candidate = await _candidate(db_session, "callback-before-receipt")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    version, accepted = await record_ai_draft_result(
        db_session,
        draft_run_id=queued.id,
        draft_result=_report(candidate, queued.id),
        authenticated_callback_proof=True,
    )
    assert version is not None
    candidate.status = "VALIDATING"
    await db_session.commit()

    late = await record_ai_draft_dispatch(
        db_session,
        draft_run_id=queued.id,
        dispatched=True,
    )

    assert late.id == accepted.id
    await db_session.refresh(candidate)
    assert candidate.status == "VALIDATING"
    dispatch_events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.event_type == "AI_DRAFT_DISPATCHED",
                )
            )
        )
        .scalars()
        .all()
    )
    assert dispatch_events == []


async def test_successful_draft_callback_automatically_dispatches_one_demo(
    app_client, db_session, monkeypatch
):
    candidate = await _candidate(db_session, "auto_demo")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )
    report = _report(candidate, queued.id)
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "foundry_auto_demo_enabled", True)
    monkeypatch.setattr(settings, "foundry_draft_result_secret", "draft-" + "x" * 40)
    dispatches: list[dict[str, object]] = []

    async def _dispatch(**kwargs):
        dispatches.append(kwargs)

    monkeypatch.setattr(
        "app.services.foundry_validation_dispatch.dispatch_candidate_validation",
        _dispatch,
    )
    url = f"/api/internal/foundry/draft-runs/{queued.id}/result"
    headers = {"Authorization": "Bearer " + "draft-" + "x" * 40}
    first = await app_client.post(url, headers=headers, json=report)
    replay = await app_client.post(url, headers=headers, json=report)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["status"] == "VERSION_APPENDED"
    assert first.json()["auto_demo"]["status"] == "DISPATCHED"
    assert replay.json()["auto_demo"] == first.json()["auto_demo"]
    assert len(dispatches) == 1
    runs = list(
        (
            await db_session.execute(
                select(FoundryValidationRun).where(
                    FoundryValidationRun.candidate_id == candidate.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].requested_by_kind == "CONTROL_PLANE"
    assert str(runs[0].candidate_version_id) == first.json()["candidate_version_id"]


async def test_auto_demo_dispatch_failure_is_recorded_without_implicit_retry(
    app_client, db_session, monkeypatch
):
    candidate = await _candidate(db_session, "auto_demo_failure")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )
    report = _report(candidate, queued.id)
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "foundry_auto_demo_enabled", True)
    monkeypatch.setattr(settings, "foundry_draft_result_secret", "draft-" + "y" * 40)
    dispatch_count = 0

    async def _dispatch_failure(**_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        raise FoundryValidationDispatchError("validation_dispatch_timeout")

    monkeypatch.setattr(
        "app.services.foundry_validation_dispatch.dispatch_candidate_validation",
        _dispatch_failure,
    )
    url = f"/api/internal/foundry/draft-runs/{queued.id}/result"
    headers = {"Authorization": "Bearer " + "draft-" + "y" * 40}
    first = await app_client.post(url, headers=headers, json=report)
    replay = await app_client.post(url, headers=headers, json=report)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["candidate_version_id"] is not None
    assert first.json()["auto_demo"]["status"] == "DISPATCH_UNCERTAIN"
    assert first.json()["auto_demo"]["failure_class"] == "validation_dispatch_timeout"
    assert replay.json()["auto_demo"] == first.json()["auto_demo"]
    assert dispatch_count == 1
    await db_session.refresh(candidate)
    assert candidate.current_version_number == 1
    demos = list(
        (
            await db_session.execute(
                select(FoundryDemoRun).where(FoundryDemoRun.candidate_id == candidate.id)
            )
        )
        .scalars()
        .all()
    )
    assert demos == []


async def test_github_draft_dispatch_has_no_user_research_content(monkeypatch):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(204)

    monkeypatch.setattr(settings, "foundry_draft_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    monkeypatch.setattr(
        settings, "foundry_draft_github_workflow", "foundry-candidate-draft.yml"
    )
    monkeypatch.setattr(settings, "foundry_draft_github_ref", "main")
    monkeypatch.setattr(settings, "foundry_draft_github_token", "token-" + "t" * 40)
    draft_run_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "model": "w0wa_cdm",
        "research_domain": "cosmology",
    }
    fingerprint = sha256_json(descriptor)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dispatch_candidate_draft(
            draft_run_id=draft_run_id,
            candidate_id=candidate_id,
            gap_fingerprint=fingerprint,
            gap_code="registered_workflow_missing",
            gap_descriptor=descriptor,
            generation_route="COMPOSITION",
            risk_level="R1",
            client=client,
        )
    inputs = seen["payload"]["inputs"]
    assert seen["payload"]["ref"] == "main"
    assert inputs == {
        "draft_run_id": str(draft_run_id),
        "candidate_id": str(candidate_id),
        "gap_fingerprint": fingerprint,
        "gap_code": "registered_workflow_missing",
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": "COMPOSITION",
        "risk_level": "R1",
    }
    serialized = json.dumps(inputs).lower()
    assert all(
        forbidden not in serialized
        for forbidden in ("claim_text", "prompt", "workspace", "user_id", "doi")
    )


@pytest.mark.parametrize(
    ("status_code", "delivery_uncertain", "retryable"),
    [(503, True, True), (429, False, True), (422, False, False)],
)
async def test_github_draft_dispatch_classifies_delivery_outcome(
    monkeypatch,
    status_code: int,
    delivery_uncertain: bool,
    retryable: bool,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    monkeypatch.setattr(settings, "foundry_draft_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    monkeypatch.setattr(
        settings, "foundry_draft_github_workflow", "foundry-candidate-draft.yml"
    )
    monkeypatch.setattr(settings, "foundry_draft_github_ref", "main")
    monkeypatch.setattr(settings, "foundry_draft_github_token", "token-" + "t" * 40)
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FoundryDraftDispatchError) as captured:
            await dispatch_candidate_draft(
                draft_run_id=uuid.uuid4(),
                candidate_id=uuid.uuid4(),
                gap_fingerprint=sha256_json(descriptor),
                gap_code="registered_workflow_missing",
                gap_descriptor=descriptor,
                generation_route="COMPOSITION",
                risk_level="R1",
                client=client,
            )
    assert captured.value.delivery_uncertain is delivery_uncertain
    assert captured.value.retryable is retryable


async def test_github_draft_protocol_error_has_unknown_delivery(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("connection closed", request=request)

    monkeypatch.setattr(settings, "foundry_draft_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    monkeypatch.setattr(
        settings, "foundry_draft_github_workflow", "foundry-candidate-draft.yml"
    )
    monkeypatch.setattr(settings, "foundry_draft_github_ref", "main")
    monkeypatch.setattr(settings, "foundry_draft_github_token", "token-" + "t" * 40)
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FoundryDraftDispatchError) as captured:
            await dispatch_candidate_draft(
                draft_run_id=uuid.uuid4(),
                candidate_id=uuid.uuid4(),
                gap_fingerprint=sha256_json(descriptor),
                gap_code="registered_workflow_missing",
                gap_descriptor=descriptor,
                generation_route="COMPOSITION",
                risk_level="R1",
                client=client,
            )
    assert captured.value.delivery_uncertain is True
    assert captured.value.retryable is True


async def test_github_draft_connect_error_is_known_retryable_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns unavailable", request=request)

    monkeypatch.setattr(settings, "foundry_draft_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    monkeypatch.setattr(
        settings, "foundry_draft_github_workflow", "foundry-candidate-draft.yml"
    )
    monkeypatch.setattr(settings, "foundry_draft_github_ref", "main")
    monkeypatch.setattr(settings, "foundry_draft_github_token", "token-" + "t" * 40)
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FoundryDraftDispatchError) as captured:
            await dispatch_candidate_draft(
                draft_run_id=uuid.uuid4(),
                candidate_id=uuid.uuid4(),
                gap_fingerprint=sha256_json(descriptor),
                gap_code="registered_workflow_missing",
                gap_descriptor=descriptor,
                generation_route="COMPOSITION",
                risk_level="R1",
                client=client,
            )
    assert captured.value.failure_class == "draft_dispatch_unavailable"
    assert captured.value.delivery_uncertain is False
    assert captured.value.retryable is True


async def test_github_draft_dispatch_rejects_non_main_ref(monkeypatch):
    monkeypatch.setattr(settings, "foundry_draft_dispatch_backend", "github_actions")
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    monkeypatch.setattr(
        settings, "foundry_draft_github_workflow", "foundry-candidate-draft.yml"
    )
    monkeypatch.setattr(settings, "foundry_draft_github_ref", "feature/untrusted")
    monkeypatch.setattr(settings, "foundry_draft_github_token", "token-" + "t" * 40)
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }

    with pytest.raises(FoundryDraftDispatchError, match="draft_dispatch_misconfigured"):
        await dispatch_candidate_draft(
            draft_run_id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            gap_fingerprint=sha256_json(descriptor),
            gap_code="registered_workflow_missing",
            gap_descriptor=descriptor,
            generation_route="COMPOSITION",
            risk_level="R1",
        )


async def test_triage_automatically_dispatches_only_the_safe_gap_binding(
    app_client, db_session, test_user, monkeypatch
):
    owner, owner_token = test_user
    gap = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "private-dataset-label",
        "next_action": "private user-facing explanation",
    }
    audit = ClaimAudit(
        user_id=owner.id,
        request_hash="d" * 64,
        lifecycle_status="COMPLETED",
        scientific_verdict="CAPABILITY_GAP",
        mode="audit_only",
        claim_text="private claim must never enter dispatch",
        source_kind="arxiv",
        source_value="private-source-id",
        capability_gaps=[gap],
    )
    db_session.add(audit)
    await db_session.commit()
    await db_session.refresh(audit)
    gap_id = serialize_capability_gaps(audit.id, [gap])[0]["gap_id"]
    monkeypatch.setattr(settings, "foundry_gap_tracking_enabled", True)
    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "admin_secret", "foundry-draft-admin")
    created = await app_client.post(
        f"/api/research/claim-audits/{audit.id}/capability-requests",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"gap_id": gap_id},
    )
    assert created.status_code == 201, created.text
    dispatched: dict[str, object] = {}

    async def _dispatch(**kwargs):
        dispatched.update(kwargs)

    monkeypatch.setattr("app.api.foundry.dispatch_candidate_draft", _dispatch)
    response = await app_client.post(
        f"/api/admin/foundry/requests/{created.json()['id']}/triage",
        headers={"X-Admin-Secret": "foundry-draft-admin"},
        json={"generation_route": "DATA_ADAPTER", "risk_level": "R1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["draft_run"]["status"] == "DISPATCHED"
    assert set(dispatched) == {
        "draft_run_id",
        "candidate_id",
        "gap_fingerprint",
        "gap_code",
        "gap_descriptor",
        "generation_route",
        "risk_level",
    }
    serialized = json.dumps(dispatched, default=str).lower()
    assert "private claim" not in serialized
    assert "private-source" not in serialized
    assert dispatched["gap_descriptor"] == {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "private-dataset-label",
        "research_domain": "cosmology",
    }

    events = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id
                    == uuid.UUID(response.json()["candidate_id"]),
                    FoundryCandidateEvent.event_type == "AI_DRAFT_QUEUED",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert set(events[0].event_payload) == {
        "candidate_id",
        "gap_fingerprint",
        "gap_code",
        "gap_descriptor",
        "generation_route",
        "risk_level",
        "attempt_number",
        "max_attempts",
        "dispatch_lease_seconds",
        "workflow_lease_seconds",
        "lease_expires_at",
    }


async def test_triage_preserves_uncertain_dispatch_without_duplicate_run(
    app_client, db_session, test_user, monkeypatch
):
    owner, owner_token = test_user
    gap = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
    }
    audit = ClaimAudit(
        user_id=owner.id,
        request_hash="e" * 64,
        lifecycle_status="COMPLETED",
        scientific_verdict="CAPABILITY_GAP",
        mode="audit_only",
        claim_text="private claim",
        source_kind="arxiv",
        source_value="private-source-id",
        capability_gaps=[gap],
    )
    db_session.add(audit)
    await db_session.commit()
    await db_session.refresh(audit)
    gap_id = serialize_capability_gaps(audit.id, [gap])[0]["gap_id"]
    monkeypatch.setattr(settings, "foundry_gap_tracking_enabled", True)
    monkeypatch.setattr(settings, "foundry_candidate_catalog_enabled", True)
    monkeypatch.setattr(settings, "foundry_ai_drafting_enabled", True)
    monkeypatch.setattr(settings, "admin_secret", "foundry-draft-admin")
    created = await app_client.post(
        f"/api/research/claim-audits/{audit.id}/capability-requests",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"gap_id": gap_id},
    )
    assert created.status_code == 201, created.text
    dispatch_count = 0

    async def _uncertain_dispatch(**_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        raise FoundryDraftDispatchError(
            "draft_dispatch_outcome_unknown",
            retryable=True,
            delivery_uncertain=True,
        )

    monkeypatch.setattr(
        "app.api.foundry.dispatch_candidate_draft", _uncertain_dispatch
    )
    url = f"/api/admin/foundry/requests/{created.json()['id']}/triage"
    headers = {"X-Admin-Secret": "foundry-draft-admin"}
    payload = {"generation_route": "DATA_ADAPTER", "risk_level": "R1"}
    first = await app_client.post(url, headers=headers, json=payload)
    second = await app_client.post(url, headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert first.json()["draft_run"]["status"] == "OUTCOME_UNKNOWN"
    assert first.json()["draft_run"]["delivery_uncertain"] is True
    assert second.status_code == 200, second.text
    assert second.json()["draft_run"]["status"] == "OUTCOME_UNKNOWN"
    assert second.json()["draft_run"]["delivery_uncertain"] is True
    assert second.json()["draft_run"]["retryable"] is True
    assert second.json()["draft_run"]["idempotent_replay"] is True
    assert second.json()["draft_run"]["retry_after"] is not None
    assert second.json()["draft_run"]["draft_run_id"] == first.json()["draft_run"][
        "draft_run_id"
    ]
    assert dispatch_count == 1

    manual_version = await app_client.post(
        url,
        headers=headers,
        json={**payload, "candidate_version": _candidate_version()},
    )
    changed_binding = await app_client.post(
        url,
        headers=headers,
        json={"generation_route": "SCIENCE_CODE", "risk_level": "R2"},
    )
    assert manual_version.status_code == 409, manual_version.text
    assert changed_binding.status_code == 409, changed_binding.text
    candidate = await db_session.get(
        FoundryCandidate, uuid.UUID(first.json()["candidate_id"])
    )
    assert candidate is not None
    assert candidate.generation_route == "DATA_ADAPTER"
    assert candidate.risk_level == "R1"
    assert candidate.current_version_number is None

    monkeypatch.setattr(settings, "foundry_draft_result_secret", "draft-" + "q" * 40)
    monkeypatch.setattr(settings, "foundry_draft_github_repository", "astro/platform")
    draft_run_id = uuid.UUID(first.json()["draft_run"]["draft_run_id"])
    callback = await app_client.post(
        f"/api/internal/foundry/draft-runs/{draft_run_id}/result",
        headers={"Authorization": "Bearer " + "draft-" + "q" * 40},
        json=_report(candidate, draft_run_id),
    )
    assert callback.status_code == 200, callback.text
    candidate.status = "VALIDATING"
    await db_session.commit()

    after_acceptance = await app_client.post(url, headers=headers, json=payload)
    assert after_acceptance.status_code == 409, after_acceptance.text
    await db_session.refresh(candidate)
    assert candidate.status == "VALIDATING"


def test_provider_command_unavailable_fails_closed_and_is_finalizable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON", raising=False)
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }
    binding = {
        "draft_run_id": str(uuid.uuid4()),
        "candidate_id": str(uuid.uuid4()),
        "gap_fingerprint": sha256_json(descriptor),
        "gap_code": "registered_workflow_missing",
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": "DATA_ADAPTER",
        "risk_level": "R1",
    }
    generate_args = argparse.Namespace(**binding, output_dir=str(tmp_path))
    assert run_foundry_ai_draft_job.generate(generate_args) == 1
    provider_result = json.loads((tmp_path / "provider-result.json").read_text())
    assert provider_result["status"] == "FAILED"
    assert provider_result["failure_class"] == "draft_provider_command_unconfigured"

    callback = tmp_path / "callback.json"
    finalize_args = argparse.Namespace(
        **binding,
        input_dir=str(tmp_path),
        output=str(callback),
        validation_runner_image_digest="",
    )
    assert run_foundry_ai_draft_job.finalize(finalize_args) == 0
    report = json.loads(callback.read_text())
    assert report["status"] == "FAILED"
    assert report["candidate_version"] is None
    assert report["provider_receipt"]["generated_code_executed"] is False


def test_provider_receives_only_the_canonical_safe_gap_descriptor(
    tmp_path: Path, monkeypatch
):
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "source_profile_key": "desi_dr2_chains_v1",
        "claim_type": "parameter_interval_report",
        "model": "w0wa_cdm",
        "research_domain": "cosmology",
    }
    binding = {
        "draft_run_id": str(uuid.uuid4()),
        "candidate_id": str(uuid.uuid4()),
        "gap_fingerprint": sha256_json(descriptor),
        "gap_code": "registered_workflow_missing",
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": "DATA_ADAPTER",
        "risk_level": "R1",
    }
    seen: dict[str, object] = {}

    def _provider(command, *, input, **_kwargs):
        seen["command"] = command
        seen["request"] = json.loads(input)
        response = {
            "schema_version": 1,
            "candidate_bundle": _candidate_version()["candidate_bundle"],
            "patch": "",
            "sbom": {"components": []},
            "provider": {
                "provider": "provider",
                "model": "model-v1",
                "request_id": "request-1",
            },
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(response).encode("utf-8"),
        )

    monkeypatch.setenv(
        "FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON",
        '["/trusted/provider", "draft"]',
    )
    monkeypatch.setattr(run_foundry_ai_draft_job.subprocess, "run", _provider)
    args = argparse.Namespace(**binding, output_dir=str(tmp_path))
    assert run_foundry_ai_draft_job.generate(args) == 0
    assert seen["command"] == ["/trusted/provider", "draft"]
    request = seen["request"]
    assert request["gap_descriptor"] == descriptor
    serialized = json.dumps(request).lower()
    assert all(
        forbidden not in serialized
        for forbidden in (
            "claim_text",
            "prompt_text",
            "source_text",
            "workspace_id",
            "user_id",
        )
    )
    provider_result = json.loads((tmp_path / "provider-result.json").read_text())
    assert provider_result["gap_descriptor"] == descriptor


@pytest.mark.parametrize(
    ("mutation", "validator_failure"),
    [
        ("missing_source_pins", "candidate_bundle_shape_not_registered"),
        ("unregistered_entrypoint", "candidate_entrypoint_not_allowlisted"),
    ],
)
async def test_invalid_provider_bundle_becomes_recorded_failed_callback(
    db_session,
    tmp_path: Path,
    monkeypatch,
    mutation: str,
    validator_failure: str,
):
    candidate = await _candidate(db_session, f"invalid-{mutation}")
    queued, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )

    descriptor = dict(candidate.gap_descriptor)
    binding = {
        "draft_run_id": str(queued.id),
        "candidate_id": str(candidate.id),
        "gap_fingerprint": candidate.gap_fingerprint,
        "gap_code": candidate.gap_code,
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": candidate.generation_route,
        "risk_level": candidate.risk_level,
    }
    bundle = copy.deepcopy(_candidate_version()["candidate_bundle"])
    if mutation == "missing_source_pins":
        bundle.pop("source_pins")
    else:
        bundle["entrypoint_id"] = "provider_selected_arbitrary_entrypoint"

    server_assigned = copy.deepcopy(bundle)
    server_assigned["candidate_version"] = 1
    with pytest.raises(ValueError, match=validator_failure):
        validate_candidate_bundle(server_assigned)

    def _provider(_command, **_kwargs):
        response = {
            "schema_version": 1,
            "candidate_bundle": bundle,
            "patch": "",
            "sbom": {"components": []},
            "provider": {
                "provider": "provider",
                "model": "model-v1",
                "request_id": "request-invalid-bundle",
            },
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(response).encode("utf-8"),
        )

    monkeypatch.setenv(
        "FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON",
        '["/trusted/provider", "draft"]',
    )
    monkeypatch.setattr(run_foundry_ai_draft_job.subprocess, "run", _provider)
    generate_args = argparse.Namespace(**binding, output_dir=str(tmp_path))
    assert run_foundry_ai_draft_job.generate(generate_args) == 0
    assert json.loads((tmp_path / "provider-result.json").read_text())[
        "status"
    ] == "SUCCEEDED"

    source_receipt = {
        "hash_algorithm": "standard_astro_tracked_source_manifest_v1",
        "base_commit": "a" * 40,
        "base_source_tree_sha256": "4" * 64,
        "post_patch_source_tree_sha256": "4" * 64,
        "patch_sha256": EMPTY_SHA256,
        "patch_applied": False,
        "changed_paths": [],
        "dependency_lock_sha256": "2" * 64,
        "runner_definition_sha256": "3" * 64,
    }
    source_receipt_path = tmp_path / "source-receipt.json"
    run_foundry_ai_draft_job._write_json(source_receipt_path, source_receipt)
    callback_path = tmp_path / "callback.json"
    finalize_args = argparse.Namespace(
        **binding,
        input_dir=str(tmp_path),
        output=str(callback_path),
        validation_runner_image_digest=VALIDATION_IMAGE,
        source_repo=str(tmp_path / "unused-source"),
        source_receipt=str(source_receipt_path),
        artifact_repository="MikhailXiaomaikou/Standard-Astro",
        workflow_run_id="123",
        artifact_id="456",
        artifact_name=f"foundry-draft-{queued.id}",
        artifact_digest="9" * 64,
    )
    assert run_foundry_ai_draft_job.finalize(finalize_args) == 0
    report = json.loads(callback_path.read_text())
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "draft_candidate_bundle_invalid"
    assert report["candidate_version"] is None
    assert report["artifact_receipt"] is None
    assert report["source_receipt"] is None
    assert report["provider_receipt"]["provider"] == "provider"

    version, failed = await record_ai_draft_result(
        db_session,
        draft_run_id=queued.id,
        draft_result=report,
    )
    assert version is None
    assert failed.event_type == "AI_DRAFT_RESULT_FAILED"
    assert failed.event_payload["failure_class"] == "draft_candidate_bundle_invalid"

    retry, retry_created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert retry_created is True
    assert retry.id != queued.id


def test_host_image_failure_cli_freezes_a_failed_callback(tmp_path: Path):
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "desi_dr2_bao",
        "research_domain": "cosmology",
    }
    binding = {
        "draft_run_id": str(uuid.uuid4()),
        "candidate_id": str(uuid.uuid4()),
        "gap_fingerprint": sha256_json(descriptor),
        "gap_code": "registered_workflow_missing",
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": "DATA_ADAPTER",
        "risk_level": "R1",
    }
    provider_receipt = {
        "contract_version": 1,
        "provider": "provider",
        "model": "model-v1",
        "request_id_sha256": "7" * 64,
        "prompt_or_user_data_stored": False,
        "generated_code_executed": False,
        "tests_executed": False,
    }
    run_foundry_ai_draft_job._write_json(
        tmp_path / "provider-result.json",
        {
            "schema_version": 1,
            **{**binding, "gap_descriptor": descriptor},
            "status": "SUCCEEDED",
            "failure_class": None,
            "provider_receipt": provider_receipt,
        },
    )
    callback = tmp_path / "callback.json"
    script = Path(run_foundry_ai_draft_job.__file__).resolve()
    command = [
        sys.executable,
        str(script),
        "finalize-host-failure",
        "--draft-run-id",
        binding["draft_run_id"],
        "--candidate-id",
        binding["candidate_id"],
        "--gap-fingerprint",
        binding["gap_fingerprint"],
        "--gap-code",
        binding["gap_code"],
        "--gap-descriptor",
        binding["gap_descriptor"],
        "--generation-route",
        binding["generation_route"],
        "--risk-level",
        binding["risk_level"],
        "--input-dir",
        str(tmp_path),
        "--output",
        str(callback),
        "--failure-class",
        "draft_host_image_push_failed",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(callback.read_text(encoding="utf-8"))
    declared_hash = report.pop("draft_result_sha256")
    assert declared_hash == sha256_json(report)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "draft_host_image_push_failed"
    assert report["candidate_version"] is None
    assert report["artifact_manifest"] == []
    assert report["artifact_receipt"] is None
    assert report["source_receipt"] is None
    assert report["provider_receipt"] == provider_receipt


async def test_host_failure_callback_unblocks_a_new_draft_attempt(
    tmp_path: Path, db_session
):
    candidate = await _candidate(db_session, "host_failure_retry")
    queued, _ = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    await record_ai_draft_dispatch(
        db_session, draft_run_id=queued.id, dispatched=True
    )
    descriptor = candidate.gap_descriptor
    binding = {
        "draft_run_id": str(queued.id),
        "candidate_id": str(candidate.id),
        "gap_fingerprint": candidate.gap_fingerprint,
        "gap_code": candidate.gap_code,
        "gap_descriptor": json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")
        ),
        "generation_route": candidate.generation_route,
        "risk_level": candidate.risk_level,
    }
    provider_receipt = {
        "contract_version": 1,
        "provider": "provider",
        "model": "model-v1",
        "request_id_sha256": "8" * 64,
        "prompt_or_user_data_stored": False,
        "generated_code_executed": False,
        "tests_executed": False,
    }
    run_foundry_ai_draft_job._write_json(
        tmp_path / "provider-result.json",
        {
            "schema_version": 1,
            **{**binding, "gap_descriptor": descriptor},
            "status": "SUCCEEDED",
            "failure_class": None,
            "provider_receipt": provider_receipt,
        },
    )
    callback = tmp_path / "callback.json"
    args = argparse.Namespace(
        **binding,
        input_dir=str(tmp_path),
        output=str(callback),
        failure_class="draft_host_image_digest_inspection_failed",
    )
    assert run_foundry_ai_draft_job.finalize_host_failure(args) == 0
    version, event = await record_ai_draft_result(
        db_session,
        draft_run_id=queued.id,
        draft_result=json.loads(callback.read_text(encoding="utf-8")),
    )

    assert version is None
    assert event.event_type == "AI_DRAFT_RESULT_FAILED"
    assert event.event_payload["failure_class"] == (
        "draft_host_image_digest_inspection_failed"
    )
    retry, created = await queue_ai_draft(
        db_session,
        candidate_id=candidate.id,
        actor_kind="HUMAN_ADMIN",
        actor_user_id=None,
    )
    assert created is True
    assert retry.id != queued.id


def test_candidate_version_identity_binds_every_reproducibility_input():
    base = {
        "candidate_bundle_sha256": "1" * 64,
        "workflow_spec_sha256": "2" * 64,
        "code_tree_sha256": "3" * 64,
        "patch_sha256": "4" * 64,
        "dependency_lock_sha256": "5" * 64,
        "sbom_sha256": "6" * 64,
        "fixture_hashes": [{"key": "fixture", "sha256": "7" * 64}],
        "data_hashes": {"dataset": "8" * 64},
        "validation_runner_image_digest": "sha256:" + "9" * 64,
    }
    original = candidate_version_sha256(**base)
    mutations = {
        "candidate_bundle_sha256": "a" * 64,
        "workflow_spec_sha256": "a" * 64,
        "code_tree_sha256": "a" * 64,
        "patch_sha256": "a" * 64,
        "dependency_lock_sha256": "a" * 64,
        "sbom_sha256": "a" * 64,
        "fixture_hashes": [{"key": "fixture", "sha256": "a" * 64}],
        "data_hashes": {"dataset": "a" * 64},
        "validation_runner_image_digest": "sha256:" + "a" * 64,
    }
    for field, replacement in mutations.items():
        changed = copy.deepcopy(base)
        changed[field] = replacement
        assert candidate_version_sha256(**changed) != original, field


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _source_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "foundry-tests@example.invalid")
    _git(path, "config", "user.name", "Foundry Tests")
    (path / "backend/app/services/foundry_generated").mkdir(parents=True)
    (path / "backend/app/services/foundry_generated/__init__.py").write_text("")
    (path / "backend/requirements.lock").write_text("pytest==8.4.1\n")
    (path / "backend/Dockerfile.foundry-demo").write_text("FROM scratch\n")
    (path / "tracked.txt").write_text("stable\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "fixture")
    return path


def test_canonical_source_manifest_ignores_mtime_but_binds_mode_and_content(
    tmp_path: Path,
):
    repo = _source_repo(tmp_path / "source")
    first_hash, first_manifest = tracked_source_tree_hash(repo)
    tracked = repo / "tracked.txt"
    os.utime(tracked, (tracked.stat().st_atime + 10, tracked.stat().st_mtime + 10))
    assert tracked_source_tree_hash(repo) == (first_hash, first_manifest)

    (repo / "untracked-private.txt").write_text("not in the manifest\n")
    assert tracked_source_tree_hash(repo) == (first_hash, first_manifest)
    with pytest.raises(FoundrySourceTreeError, match="source_checkout_not_clean"):
        assert_clean_checkout(repo)
    (repo / "untracked-private.txt").unlink()

    tracked.chmod(0o755)
    _git(repo, "add", "tracked.txt")
    mode_hash, mode_manifest = tracked_source_tree_hash(repo)
    assert mode_hash != first_hash
    assert next(
        item for item in mode_manifest["entries"] if item["path"] == "tracked.txt"
    )["mode"] == "100755"

    tracked.write_text("changed\n")
    _git(repo, "add", "tracked.txt")
    content_hash, _ = tracked_source_tree_hash(repo)
    assert content_hash != mode_hash


@pytest.mark.parametrize(
    "patch,failure",
    [
        (
            """diff --git a/backend/app/services/foundry_generated/demo_v1.py b/backend/app/services/foundry_generated/demo_v1.py
similarity index 100%
rename from backend/app/services/foundry_generated/demo_v1.py
rename to backend/app/services/foundry_generated/other_v1.py
""",
            "draft_patch_extended_header_forbidden",
        ),
        (
            """diff --git a/backend/app/services/foundry_generated/demo_v1.py b/backend/app/services/foundry_generated/demo_v1.py
old mode 100644
new mode 120000
--- a/backend/app/services/foundry_generated/demo_v1.py
+++ b/backend/app/services/foundry_generated/demo_v1.py
@@ -1 +1 @@
-VALUE = 1
+../../../../../../outside.py
""",
            "draft_patch_unsafe_type",
        ),
        (
            """diff --git a/backend/app/services/foundry_generated/demo_v1.py b/backend/app/services/foundry_generated/demo_v1.py
new file mode 100644
--- /dev/null
+++ b/backend/app/services/foundry_generated/other_v1.py
@@ -0,0 +1 @@
+value = 1
""",
            "draft_patch_new_path_mismatch",
        ),
        (
            """diff --git a/backend/app/services/foundry_generated/demo_v1.py b/backend/app/services/foundry_generated/demo_v1.py
new file mode 100644
--- /dev/null
+++ /dev/null
@@ -0,0 +0,0 @@
""",
            "draft_patch_new_path_mismatch",
        ),
    ],
)
def test_inert_patch_parser_rejects_extended_header_and_path_bypasses(
    patch: str, failure: str
):
    with pytest.raises(ValueError, match=failure):
        run_foundry_ai_draft_job._validate_patch_paths(patch.encode("utf-8"))


def test_science_code_patch_is_inert_until_exact_isolated_validation(
    tmp_path: Path,
):
    base_repo = _source_repo(tmp_path / "base")
    replay_repo = tmp_path / "replay"
    subprocess.run(
        ["git", "clone", "--quiet", str(base_repo), str(replay_repo)],
        check=True,
    )
    candidate_key = "science_candidate_v1"
    module_path = (
        f"backend/app/services/foundry_generated/{candidate_key}.py"
    )
    sentinel = tmp_path / "candidate-executed"
    code = [
        "from pathlib import Path",
        f"Path({str(sentinel)!r}).write_text('executed')",
        "",
        "def run_demo(bundle, *, cache_root=None):",
        "    return {",
        "        'status': 'PASSED',",
        "        'failure_class': None,",
        "        'result': {'candidate_code_ran': True},",
        "        'validation_summary': {'science_code_fixture': True},",
        "    }",
    ]
    additions = "\n".join(f"+{line}" for line in code)
    patch_bytes = (
        f"diff --git a/{module_path} b/{module_path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{module_path}\n"
        f"@@ -0,0 +1,{len(code)} @@\n"
        f"{additions}\n"
    ).encode("utf-8")
    patch_path = tmp_path / "candidate.patch"
    patch_path.write_bytes(patch_bytes)

    source = run_foundry_ai_draft_job._apply_inert_patch_and_hash(
        source_repo=base_repo,
        patch_path=patch_path,
    )
    assert source["patch_applied"] is True
    assert source["changed_paths"] == [module_path]
    assert source["base_source_tree_sha256"] != source["post_patch_source_tree_sha256"]
    assert not sentinel.exists(), "Draft materialization must not execute candidate code"

    raw_bundle = copy.deepcopy(_candidate_version()["candidate_bundle"])
    raw_bundle.update(
        {
            "candidate_id": candidate_key,
            "candidate_version": 0,
            "proposed_workflow_id": "science_candidate_workflow_v1",
            "entrypoint_id": "candidate_generated_python_demo_v1",
            "risk_level": "R3",
        }
    )
    raw_bundle["workflow_spec"] = {
        "workflow_id": "science_candidate_workflow_v1",
        "workflow_version": "1.0.0-candidate.1",
        "claim_scope": "non_formal_science_code_fixture",
        "output_policy": {"publication_ready": False},
    }
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    run_foundry_ai_draft_job._write_json(
        artifact_dir / "candidate.json", raw_bundle
    )
    (artifact_dir / "candidate.patch").write_bytes(patch_bytes)
    run_foundry_ai_draft_job._write_json(
        artifact_dir / "sbom.json", {"components": []}
    )
    run_foundry_ai_draft_job._write_json(
        artifact_dir / "provider-receipt.json", {"provider": "fixture"}
    )
    run_foundry_ai_draft_job._write_json(
        artifact_dir / "provider-result.json", {"status": "SUCCEEDED"}
    )

    normalized_input = copy.deepcopy(raw_bundle)
    normalized_input["candidate_version"] = 1
    normalized_input["generation"].update(
        {
            "source_hash_algorithm": source["hash_algorithm"],
            "source_base_commit": source["base_commit"],
            "source_base_tree_sha256": source["base_source_tree_sha256"],
            "source_tree_sha256": source["post_patch_source_tree_sha256"],
            "source_materialization_required": True,
        }
    )
    normalized_input["dependency_lock_sha256"] = source[
        "dependency_lock_sha256"
    ]
    normalized_input["runner_definition_sha256"] = source[
        "runner_definition_sha256"
    ]
    normalized = validate_candidate_bundle(normalized_input)
    candidate_bundle_hash = sha256_json(normalized)
    candidate_artifact_hash = run_foundry_ai_draft_job._sha256_bytes(
        (artifact_dir / "candidate.json").read_bytes()
    )
    sbom_hash = run_foundry_ai_draft_job._sha256_bytes(
        (artifact_dir / "sbom.json").read_bytes()
    )
    data_hashes = {
        str(item["key"]): str(item["sha256"])
        for item in normalized["source_pins"]
    }
    version_hash = candidate_version_sha256(
        candidate_bundle_sha256=candidate_bundle_hash,
        workflow_spec_sha256=sha256_json(normalized["workflow_spec"]),
        code_tree_sha256=source["post_patch_source_tree_sha256"],
        patch_sha256=source["patch_sha256"],
        dependency_lock_sha256=source["dependency_lock_sha256"],
        sbom_sha256=sbom_hash,
        fixture_hashes=normalized["fixture_hashes"],
        data_hashes=data_hashes,
        validation_runner_image_digest=VALIDATION_IMAGE,
    )
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(artifact_dir.iterdir()):
            handle.write(path, arcname=path.name)
    binding = {
        "candidate_id": str(uuid.uuid4()),
        "candidate_version_id": str(uuid.uuid4()),
        "candidate_version_number": "1",
        "candidate_version_hash": version_hash,
        "candidate_bundle_hash": candidate_bundle_hash,
        "candidate_artifact_hash": candidate_artifact_hash,
        "draft_run_id": str(uuid.uuid4()),
        "artifact_id": "456",
        "artifact_workflow_run_id": "123",
        "artifact_name": "foundry-draft-fixture",
        "artifact_sha256": run_foundry_ai_draft_job._sha256_bytes(
            archive.read_bytes()
        ),
        "artifact_repository": "standard-astro/platform",
        "base_commit": source["base_commit"],
        "base_source_tree_sha256": source["base_source_tree_sha256"],
        "post_patch_source_tree_sha256": source["post_patch_source_tree_sha256"],
        "patch_sha256": source["patch_sha256"],
        "sbom_sha256": sbom_hash,
        "validation_runner_image_digest": VALIDATION_IMAGE,
    }
    output_candidate = tmp_path / "normalized-candidate.json"
    output_receipt = tmp_path / "preparation-receipt.json"
    args = argparse.Namespace(
        binding_json=json.dumps(binding, sort_keys=True, separators=(",", ":")),
        candidate_key=candidate_key,
        artifact_zip=str(archive),
        extracted_dir=str(tmp_path / "extracted"),
        source_repo=str(replay_repo),
        output_candidate=str(output_candidate),
        output_receipt=str(output_receipt),
    )
    assert prepare_foundry_candidate_validation.prepare(args) == 0
    assert not sentinel.exists(), "Host replay/AST checks must remain non-executing"
    assert json.loads(output_candidate.read_text()) == normalized
    assert json.loads(output_receipt.read_text())["candidate_code_executed"] is False

    import app.services.foundry_generated as generated_package

    generated_path = str(
        replay_repo / "backend/app/services/foundry_generated"
    )
    generated_package.__path__.append(generated_path)
    importlib.invalidate_caches()
    module_name = f"app.services.foundry_generated.{candidate_key}"
    sys.modules.pop(module_name, None)
    try:
        report = run_candidate_demo(
            normalized,
            candidate_version_sha256=version_hash,
            runner_image_digest=VALIDATION_IMAGE,
        )
    finally:
        sys.modules.pop(module_name, None)
        generated_package.__path__.remove(generated_path)
    assert sentinel.read_text() == "executed"
    assert report["status"] == "PASSED"
    assert report["evidence_class"] == "NON_FORMAL_DEMO"
    assert report["candidate_version_sha256"] == version_hash
    assert report["publication_ready"] is False
    assert report["claim_eligible"] is False


def test_workflow_keeps_ai_and_callback_credentials_in_separate_jobs():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "foundry-candidate-draft.yml"
    ).read_text(encoding="utf-8")
    draft_job, remainder = workflow.split(
        "  materialize-and-build-without-callback:", 1
    )
    build_job, callback_job = remainder.split("  callback-only:", 1)
    assert "FOUNDRY_AI_DRAFT_API_KEY" in draft_job
    assert "FOUNDRY_DRAFT_RESULT_SECRET" not in draft_job
    assert "FOUNDRY_DRAFT_RESULT_SECRET" not in build_job
    assert "FOUNDRY_AI_DRAFT_API_KEY" not in build_job
    assert "FOUNDRY_DRAFT_RESULT_SECRET" in callback_job
    assert "FOUNDRY_AI_DRAFT_API_KEY" not in callback_job
    assert "candidate-source" not in callback_job
    assert "docker " not in callback_job
    assert "run_foundry_ai_draft_job.py" not in callback_job
    assert "foundry-candidate-demo.yml" in workflow
    assert "docker run" not in workflow
    assert "pytest" not in workflow
    assert "patch -p" not in workflow
    assert "--gap-descriptor \"$GAP_DESCRIPTOR\"" in workflow

    validation_workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "foundry-candidate-demo.yml"
    ).read_text(encoding="utf-8")
    assert "version_binding:" in validation_workflow
    assert "test -n \"$VERSION_BINDING\"" in validation_workflow
    assert "artifact_workflow_run_id" in validation_workflow
    assert 'data["workflow_run"]["id"]' in validation_workflow
    assert "packages: read" in validation_workflow
    assert "docker/login-action@" in validation_workflow
    assert "foundry-candidate@${RUNNER_IMAGE_DIGEST}" in validation_workflow
    assert '--candidate-version-sha256 "$CANDIDATE_VERSION_SHA256"' in validation_workflow
    assert "--network none" in validation_workflow
    assert "--log-driver none" in validation_workflow
    assert "--ulimit fsize=67108864:67108864" in validation_workflow
    assert "stat.S_ISREG" in validation_workflow
    assert "4 * 1024 * 1024" in validation_workflow
